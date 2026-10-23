// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "boat/v1/frame.pb.h"
#include "core/frame.h"
#include "event/event_bus.h"
#include "event_store/event_store.h"
#include "pdu/tick_timer.h"
#include "trace_store/trace_store.h"

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <deque>
#include <memory>
#include <mutex>
#include <optional>
#include <span>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace boat::replay {

inline constexpr std::uint32_t kReplayBusEventType = 9001;

enum class ReplaySpeed {
  REAL_TIME = 0,
  ACCELERATED = 1,
  STEP_BY_STEP = 2,
};

struct ReplayConfig {
  std::string trace_id;
  ReplaySpeed speed{ReplaySpeed::REAL_TIME};
  double speed_multiplier{1.0};
  std::uint64_t start_tick{0};
  std::string eth_iface;
  std::unordered_map<std::string, std::string> mac_map;
  int loop_delay_ms{0};  // ms gap between loop passes; 0 = no loop
  std::vector<std::string> buses;  // CAN interfaces for channel mapping
                                    // (ch1->buses[0], ch2->buses[1], ...);
                                    // empty = every channel maps to "vcan0".
};

/* Thread safety (issue #63).

   Control calls (Start, StartFromEvents, Stop, Seek, Pause, Resume) arrive on
   gRPC threads while a TickAuthority phase runs PumpDueRecords on the tick
   thread. Everything one replay needs -- its config, the trace mapping, the
   read cursor and the pacing anchors -- lives in one Session, published
   through a shared_ptr:

   - Start builds a complete new Session (mapping the trace first) and only
     then swaps it in, so the pump never sees a half-written config or cursor.
   - The pump snapshots the Session and pins it (pumps_in_flight_) under
     session_mutex_, then works with no lock held. Its own reference keeps the
     trace mapped, so a concurrent Stop can never unmap bytes it is reading.
   - Stop unpublishes the Session, flags it stopped (the pump checks before
     every record), and waits -- bounded by kStopDrainTimeout -- for an
     in-flight pump to finish, so no frame of a stopped replay reaches the sink
     after Stop returns. Memory safety does not depend on that wait; only the
     "nothing after Stop" guarantee does.

   Lock order: ReplayServiceImpl::replay_mutex_ -> control_mutex_ ->
   session_mutex_. forwarder_mutex_, event_queue_mutex_ and error_mutex_ are
   leaves, never held while taking another lock. The tick thread only ever
   takes session_mutex_ (briefly) and the leaves, and no lock it takes is held
   across trace I/O, mmap/munmap, or a callback. control_mutex_ is held across
   mapping a trace, but only control calls ever wait on it.

   PumpDueRecords must be driven from one thread at a time (the TickAuthority);
   a concurrent call returns without doing anything. */
class ReplayController {
 public:
  ReplayController(boat::store::ITraceStore& trace_store,
                   boat::store::IEventStore& event_store,
                   boat::core::EventBus& event_bus);
  ~ReplayController();

  /* Replace whatever is playing with `config`. The trace is mapped before the
     current replay is touched, so a Start that throws (unknown trace id, I/O
     error) leaves the previous replay running. */
  void Start(const ReplayConfig& config);
  void StartFromEvents(const boat::store::EventFilter& filter,
                       const ReplayConfig& config = {});

  /* Deliver every record that has come due by `authority_tick`.
     
     ReplayController owns no clock and no delivery thread: a TickAuthority
     phase calls this once per tick, after the plugins have been ticked, so a replayed
     frame always lands in a definite tick rather than wherever the host
     scheduler happened to put it. It hands event records to the configured
     IEventStore; production wraps the SQLite store in AsyncEventStore, whose
     bounded writer keeps SQLite off the tick path (see FlushEventStore).
     Records are due on elapsed authority ticks
     scaled by speed_multiplier -- no wall clock is consulted, which is what
     makes two runs agree.

     Safe to call when stopped or paused; it does nothing then. */
  void PumpDueRecords(std::uint64_t authority_tick);

  /* How long one authority tick is. Must match the interval the driving
     TickAuthority was started with, since due-times are measured in ticks.
     Defaults to BOAT_NODE_TICK_US/_MS (1 ms) so a standalone controller
     behaves sensibly without one. */
  void SetTickInterval(std::chrono::nanoseconds interval);

  void Seek(std::uint64_t tick);
  void Pause();
  void Resume();
  /* Wait until every event record handed to the event store has been
     committed (or dropped after a store error). Production replay does not
     call this on the tick path; tests and shutdown do. */
  void FlushEventStore();
  /* Stop the current replay and release its trace. Once this returns, no
     record of that replay is handed to the forwarder (unless an in-flight
     dispatch exceeds kStopDrainTimeout, which is reported on stderr). Safe to
     call from a forwarder callback, where it does not wait. */
  void Stop();
  bool HasError() const;
  bool IsRunning() const;
  std::string LastError() const;

  /* The anchor of the current session: the first authority tick at which it
     was pumped (the first PumpDueRecords call that found it running).

     This is what "tick attribution is reproducible" is relative to. For one
     session, the authority tick a record is delivered in is
     anchor + f(trace, speed_multiplier, tick interval, loop_delay_ms); f is
     the same on every run and under any load, while the anchor depends on when
     the Start call happened to arrive. Compare runs by subtracting it.

     Deliberately not moved by Seek, Resume or a loop pass -- those re-base the
     pacing, this is the session's first tick. nullopt until the first pump, for
     an empty trace (never pumped), and once the session has been stopped. */
  [[nodiscard]] std::optional<std::uint64_t> SessionAnchorTick() const;

  struct ReplayEventEntry {
    std::uint64_t tick;            // position in the trace, milliseconds
    std::string payload;
    std::uint64_t authority_tick;  // TickAuthority tick it was delivered in
  };

  /// Thread-safe: push a replay event onto the internal queue.
  /// `authority_tick` is 0 for events not produced by the pump.
  void PushEvent(std::uint64_t tick, std::string payload,
                 std::uint64_t authority_tick = 0);

  /// Thread-safe: consume (pop) all queued replay events.
  std::vector<ReplayEventEntry> ConsumeEvents();

  /* Cumulative events dropped because the StreamReplay backlog hit
     kMaxStreamEvents. Replay queues a record for streaming whether or not a
     client is attached and whether or not it is keeping up; without a bound,
     every replayed frame of every pass accumulated in memory forever. Exposed
     as the grpc.replay.stream.dropped metric. */
  [[nodiscard]] std::uint64_t DroppedEventCount() const;

  /* Backlog cap for one replay's StreamReplay queue, in events -- same value
     and reasoning as the gRPC stream queues (subscriber_queue.h). */
  static constexpr std::size_t kMaxStreamEvents = 4096;

  using EventForwarder = std::function<void(const boat::core::Frame& frame)>;
  void SetEventForwarder(EventForwarder forwarder);
  /* A copy of the playing replay's config; default-constructed when nothing
     has been started or the last replay was stopped. */
  ReplayConfig GetActiveConfig() const;

  /* Upper bound on how long Stop waits for an in-flight dispatch to finish. A
     dispatch writes to a bus, and a blocking write on a wedged physical
     interface must not hang every replay RPC behind it. */
  static constexpr std::chrono::seconds kStopDrainTimeout{2};

 private:
  // One replay: config, trace mapping, cursor and pacing state. Defined in the
  // .cpp; see the thread-safety note above the class.
  struct Session;

  // Both require control_mutex_.
  void StartLocked(const ReplayConfig& config);
  void StopLocked(bool bounded = true);

  [[nodiscard]] std::shared_ptr<Session> CurrentSession() const;

  /* The body of PumpDueRecords, run with no lock held against a pinned
     Session. */
  void PumpSession(Session& s, std::uint64_t authority_tick);

  static bool SeekToTick(std::span<const std::uint8_t> bytes, std::uint64_t tick,
                         std::size_t& offset, std::uint64_t& landed_tick);
  static std::chrono::nanoseconds TickDurationFromEnv();

  /* Has `record_tick` come due as of `authority_tick`? Pure function of the
     two anchors, the tick interval and the speed multiplier -- deliberately
     free of any clock reading. */
  [[nodiscard]] static bool IsRecordDue(const Session& s, std::uint64_t record_tick,
                                        std::uint64_t authority_tick);

  /* Reset the read cursor and re-anchor both clocks to the record we land on
     seeking to `target_tick`. */
  static void AnchorAt(Session& s, std::uint64_t target_tick, std::uint64_t authority_tick);

  /* Called when the read cursor reaches the end of the trace: either arm the
     loop delay or finish the replay. */
  static void FinishPass(Session& s, std::uint64_t authority_tick);

  /* Parse the length-delimited protobuf record starting at `offset` into `pf`,
     advancing `offset` past it. Returns a view of the record's raw bytes
     inside the mapped trace, so callers republish exactly what was stored
     rather than re-serialising. Throws on a malformed record. */
  static std::span<const std::uint8_t> ReadRecordAt(std::span<const std::uint8_t> bytes,
                                                    std::size_t& offset,
                                                    boat::v1::Frame& pf);

  /* Everything that happens once a record is due: hand the frame to the
     forwarder, publish it on the EventBus, queue it for StreamReplay, and
     persist it. Deliberately free of any pacing -- the caller decides when a
     record is due, which is what lets the same body serve both the self-paced
     loop and an externally driven tick. */
  void DispatchRecord(const Session& s, const boat::v1::Frame& pf, std::uint64_t tick,
                      std::uint64_t authority_tick, std::span<const std::uint8_t> raw);

  void RecordError(std::string message);

  /* Append one event to event_queue_, dropping the oldest first once the
     backlog is at kMaxStreamEvents. Caller holds event_queue_mutex_. */
  void PushEventLocked(std::uint64_t tick, std::string payload,
                       std::uint64_t authority_tick);

  boat::store::ITraceStore& trace_store_;
  boat::store::IEventStore& event_store_;
  boat::core::EventBus& event_bus_;

  // Held only to copy the pointer: the forwarder itself (FrameSink -> bus ->
  // plugins' on_frame) runs with no lock held.
  std::shared_ptr<const EventForwarder> event_forwarder_;
  std::mutex forwarder_mutex_;

  // Serialises Start/StartFromEvents/Stop. Never taken by the tick thread.
  std::mutex control_mutex_;

  // Guards session_, pumps_in_flight_, tick_duration_, tick_interval_explicit_.
  mutable std::mutex session_mutex_;
  std::condition_variable pump_idle_cv_;
  std::shared_ptr<Session> session_;
  std::uint32_t pumps_in_flight_{0};
  // How long one authority tick is; copied into each Session at Start. Set
  // once SetTickInterval() has been called, so a later Start() does not
  // clobber the driving authority's interval with the environment's.
  std::chrono::nanoseconds tick_duration_{std::chrono::milliseconds(1)};
  bool tick_interval_explicit_{false};

  mutable std::mutex error_mutex_;
  std::string last_error_;

  // Replay event queue — used by StreamReplay to consume events without going
  // through the EventBus (avoids race between publishing and subscribing).
  // Bounded: see kMaxStreamEvents.
  std::deque<ReplayEventEntry> event_queue_;
  mutable std::mutex event_queue_mutex_;
  std::uint64_t dropped_events_ = 0;  // guarded by event_queue_mutex_
};

}  // namespace boat::replay

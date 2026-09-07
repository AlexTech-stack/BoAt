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

class ReplayController {
 public:
  ReplayController(boat::store::ITraceStore& trace_store,
                   boat::store::IEventStore& event_store,
                   boat::core::EventBus& event_bus);
  ~ReplayController();

  void Start(const ReplayConfig& config);
  void StartFromEvents(const boat::store::EventFilter& filter,
                       const ReplayConfig& config = {});

  /* Deliver every record that has come due by `authority_tick`.
     
     ReplayController owns no thread and no clock: a TickAuthority phase calls
     this once per tick, after the plugins have been ticked, so a replayed
     frame always lands in a definite tick rather than wherever the host
     scheduler happened to put it. Records are due on elapsed authority ticks
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
  void Stop();
  bool HasError() const;
  bool IsRunning() const { return running_.load(); }
  std::string LastError() const;

  struct ReplayEventEntry {
    std::uint64_t tick;
    std::string payload;
  };

  /// Thread-safe: push a replay event onto the internal queue.
  void PushEvent(std::uint64_t tick, std::string payload);

  /// Thread-safe: consume (pop) all queued replay events.
  std::vector<ReplayEventEntry> ConsumeEvents();

  using EventForwarder = std::function<void(const boat::core::Frame& frame)>;
  void SetEventForwarder(EventForwarder forwarder);
  const ReplayConfig& GetActiveConfig() const;

 private:
  bool SeekToTick(std::uint64_t tick, std::size_t& offset, std::uint64_t& landed_tick) const;
  void ParseTickDurationFromEnv();

  /* Has `record_tick` come due as of `authority_tick`? Pure function of the
     two anchors, the tick interval and the speed multiplier -- deliberately
     free of any clock reading. */
  [[nodiscard]] bool IsRecordDue(std::uint64_t record_tick,
                                 std::uint64_t authority_tick) const;

  /* Reset the read cursor and re-anchor both clocks to the record we land on
     seeking to `target_tick`. */
  void AnchorAt(std::uint64_t target_tick, std::uint64_t authority_tick);

  /* Called when the read cursor reaches the end of the trace: either arm the
     loop delay or finish the replay. */
  void FinishPass(std::uint64_t authority_tick);

  /* Parse the length-delimited protobuf record starting at `offset` into `pf`,
     advancing `offset` past it. Returns a view of the record's raw bytes
     inside the mapped trace, so callers republish exactly what was stored
     rather than re-serialising. Throws on a malformed record. */
  std::span<const std::uint8_t> ReadRecordAt(std::size_t& offset,
                                             boat::v1::Frame& pf) const;

  /* Everything that happens once a record is due: hand the frame to the
     forwarder, publish it on the EventBus, queue it for StreamReplay, persist
     it, and advance current_tick_. Deliberately free of any pacing -- the
     caller decides when a record is due, which is what lets the same body
     serve both the self-paced loop and an externally driven tick. */
  void DispatchRecord(const boat::v1::Frame& pf, std::uint64_t tick,
                      std::span<const std::uint8_t> raw);

  boat::store::ITraceStore& trace_store_;
  boat::store::IEventStore& event_store_;
  boat::core::EventBus& event_bus_;

  EventForwarder event_forwarder_;
  std::mutex forwarder_mutex_;

  std::atomic<std::uint64_t> current_tick_{0};
  std::atomic<bool> running_{false};
  std::atomic<bool> paused_{false};
  std::condition_variable pause_cv_;
  std::mutex pause_mutex_;
  mutable std::mutex error_mutex_;

  ReplayConfig active_config_{};
  std::span<const std::uint8_t> mapped_trace_{};
  std::atomic<std::uint64_t> requested_seek_tick_{0};
  std::atomic<bool> seek_pending_{false};
  std::string last_error_;

  // Replay event queue — used by StreamReplay to consume events without going
  // through the EventBus (avoids race between publishing and subscribing).
  std::deque<ReplayEventEntry> event_queue_;
  std::mutex event_queue_mutex_;

  // Tick-driven scheduling. tick_duration_ is how long one authority tick is;
  // replay_base_tick_ is the trace tick the current pass is anchored to and
  // authority_base_tick_ the authority tick it was anchored at. A record's due
  // time is the difference between those two deltas -- no wall clock anywhere.
  std::chrono::nanoseconds tick_duration_{std::chrono::milliseconds(1)};
  std::uint64_t replay_base_tick_{0};
  std::uint64_t authority_base_tick_{0};
  bool          authority_anchored_{false};
  // Set once SetTickInterval() has been called, so a later Start() does not
  // clobber the driving authority's interval with the environment's.
  bool          tick_interval_explicit_{false};

  // Read cursor into mapped_trace_, and the tick at which a looping replay may
  // begin its next pass (0 = not waiting on a loop delay).
  std::size_t   pump_offset_{0};
  bool          pump_has_records_{false};
  std::uint64_t loop_resume_tick_{0};
};

}  // namespace boat::replay

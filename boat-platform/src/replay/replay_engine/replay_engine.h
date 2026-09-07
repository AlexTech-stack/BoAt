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
  void ReplayLoop();
  bool SeekToTick(std::uint64_t tick, std::size_t& offset, std::uint64_t& landed_tick) const;
  void ParseTickDurationFromEnv();

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
  std::thread replay_thread_;
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

  // TickTimer-based absolute-time scheduling (drift-free).
  std::unique_ptr<boat::hil::TickTimer> tick_timer_;
  std::chrono::nanoseconds tick_duration_{std::chrono::milliseconds(1)};
  std::chrono::steady_clock::time_point replay_base_time_;
  std::uint64_t replay_base_tick_{0};
};

}  // namespace boat::replay

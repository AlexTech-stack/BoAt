#pragma once

#include "event/event_bus.h"
#include "event_store/event_store.h"
#include "trace_store/trace_store.h"

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <span>
#include <string>
#include <thread>

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
};

class ReplayController {
 public:
  ReplayController(boat::store::ITraceStore& trace_store,
                   boat::store::IEventStore& event_store,
                   boat::core::EventBus& event_bus);
  ~ReplayController();

  void Start(const ReplayConfig& config);
  void Seek(std::uint64_t tick);
  void Pause();
  void Resume();
  void Stop();
  bool HasError() const;
  std::string LastError() const;

 private:
  void ReplayLoop();
  bool SeekToTick(std::uint64_t tick, std::size_t& offset) const;

  boat::store::ITraceStore& trace_store_;
  boat::store::IEventStore& event_store_;
  boat::core::EventBus& event_bus_;

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
};

}  // namespace boat::replay

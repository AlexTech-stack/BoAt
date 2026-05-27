#include "replay_engine/replay_engine.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstring>
#include <stdexcept>
#include <vector>

namespace boat::replay {
namespace {

constexpr std::uint32_t kTraceMagic = 0xB0A7B0A7;
constexpr std::uint64_t kTickDurationNs = 1'000'000ULL;

}  // namespace

ReplayController::ReplayController(boat::store::ITraceStore& trace_store,
                                   boat::store::IEventStore& event_store,
                                   boat::core::EventBus& event_bus)
    : trace_store_(trace_store), event_store_(event_store), event_bus_(event_bus) {}

ReplayController::~ReplayController() { Stop(); }

void ReplayController::Start(const ReplayConfig& config) {
  Stop();

  active_config_ = config;
  mapped_trace_ = trace_store_.ReadTraceMmap(config.trace_id);
  current_tick_.store(config.start_tick);
  requested_seek_tick_.store(config.start_tick);
  seek_pending_.store(true);
  paused_.store(false);
  running_.store(true);
  {
    std::lock_guard<std::mutex> lock(error_mutex_);
    last_error_.clear();
  }
  replay_thread_ = std::thread(&ReplayController::ReplayLoop, this);
}

void ReplayController::Seek(std::uint64_t tick) {
  requested_seek_tick_.store(tick);
  seek_pending_.store(true);
  pause_cv_.notify_all();
}

void ReplayController::Pause() { paused_.store(true); }

void ReplayController::Resume() {
  paused_.store(false);
  pause_cv_.notify_all();
}

void ReplayController::Stop() {
  const bool was_running = running_.exchange(false);
  pause_cv_.notify_all();
  if (replay_thread_.joinable()) {
    replay_thread_.join();
  }
  if (was_running || !active_config_.trace_id.empty()) {
    trace_store_.UnmapTrace(active_config_.trace_id);
  }
}

bool ReplayController::HasError() const {
  std::lock_guard<std::mutex> lock(error_mutex_);
  return !last_error_.empty();
}

std::string ReplayController::LastError() const {
  std::lock_guard<std::mutex> lock(error_mutex_);
  return last_error_;
}

bool ReplayController::SeekToTick(std::uint64_t tick, std::size_t& offset) const {
  offset = 0;
  while (offset + sizeof(boat::store::TraceRecordHeader) <= mapped_trace_.size()) {
    boat::store::TraceRecordHeader header{};
    std::memcpy(&header, mapped_trace_.data() + offset, sizeof(header));
    if (header.magic != kTraceMagic) {
      throw std::runtime_error("invalid trace record magic");
    }
    offset += sizeof(header);
    if (offset + header.payload_size > mapped_trace_.size()) {
      throw std::runtime_error("trace record payload out of bounds");
    }
    if (header.tick >= tick) {
      offset -= sizeof(header);
      return true;
    }
    offset += header.payload_size;
  }

  // Tick beyond available records: place cursor at end.
  offset = mapped_trace_.size();
  return false;
}

void ReplayController::ReplayLoop() {
  try {
    if (mapped_trace_.empty()) {
      running_.store(false);
      return;
    }

    std::size_t offset = 0;
    std::uint64_t prev_tick = current_tick_.load();
    while (running_.load() && offset + sizeof(boat::store::TraceRecordHeader) <= mapped_trace_.size()) {
      {
        std::unique_lock<std::mutex> lock(pause_mutex_);
        pause_cv_.wait(lock, [this] {
          return !running_.load() || !paused_.load() || seek_pending_.load();
        });
        if (!running_.load()) {
          break;
        }
      }

      if (seek_pending_.exchange(false)) {
        const auto target_tick = requested_seek_tick_.load();
        SeekToTick(target_tick, offset);
        current_tick_.store(target_tick);
        prev_tick = target_tick;
        continue;
      }

      boat::store::TraceRecordHeader header{};
      std::memcpy(&header, mapped_trace_.data() + offset, sizeof(header));
      offset += sizeof(header);
      if (header.magic != kTraceMagic) {
        throw std::runtime_error("invalid trace record magic");
      }

      if (offset + header.payload_size > mapped_trace_.size()) {
        throw std::runtime_error("trace record payload out of bounds");
      }

      double speed_multiplier = active_config_.speed_multiplier;
      if (speed_multiplier <= 0.0) {
        speed_multiplier = 1.0;
      }
      if (active_config_.speed == ReplaySpeed::REAL_TIME ||
          active_config_.speed == ReplaySpeed::ACCELERATED) {
        const auto delta_tick = header.tick > prev_tick ? (header.tick - prev_tick) : 0ULL;
        const auto delay_ns = static_cast<std::uint64_t>(
            static_cast<double>(delta_tick * kTickDurationNs) / speed_multiplier);
        std::this_thread::sleep_for(std::chrono::nanoseconds(delay_ns));
      }

      std::vector<std::uint8_t> payload(header.payload_size);
      if (header.payload_size > 0U) {
        std::memcpy(payload.data(), mapped_trace_.data() + offset, header.payload_size);
      }
      offset += header.payload_size;

      boat::core::BusEvent event;
      event.type = header.event_type;
      event.tick = header.tick;
      event.payload = payload;
      event_bus_.Publish(std::move(event));

      boat::core::BusEvent replay_event;
      replay_event.type = kReplayBusEventType;
      replay_event.tick = header.tick;
      replay_event.payload =
          payload.empty() ? std::string{} : std::string(reinterpret_cast<const char*>(payload.data()), payload.size());
      event_bus_.Publish(std::move(replay_event));

      boat::store::EventRecord record;
      record.id = std::to_string(header.tick) + "_" + std::to_string(header.event_type);
      record.simulation_id = active_config_.trace_id;
      record.tick = header.tick;
      record.wall_time_ns = header.wall_time_ns;
      record.signal_id = std::to_string(header.event_type);
      record.value_type = 0;
      record.value_blob = std::move(payload);
      record.tags = "{}";
      std::array<boat::store::EventRecord, 1> batch{record};
      event_store_.InsertBatch(std::span<const boat::store::EventRecord>(batch));

      current_tick_.store(header.tick);
      prev_tick = header.tick;

      if (active_config_.speed == ReplaySpeed::STEP_BY_STEP) {
        paused_.store(true);
        std::unique_lock<std::mutex> lock(pause_mutex_);
        pause_cv_.wait(lock, [this] {
          return !running_.load() || !paused_.load() || seek_pending_.load();
        });
        if (!running_.load()) {
          break;
        }
      }
    }
  } catch (const std::exception& ex) {
    {
      std::lock_guard<std::mutex> lock(error_mutex_);
      last_error_ = ex.what();
    }
    paused_.store(false);
    running_.store(false);
    pause_cv_.notify_all();
    return;
  } catch (...) {
    {
      std::lock_guard<std::mutex> lock(error_mutex_);
      last_error_ = "unknown replay error";
    }
    paused_.store(false);
    running_.store(false);
    pause_cv_.notify_all();
    return;
  }

  paused_.store(false);
  running_.store(false);
  pause_cv_.notify_all();
}

}  // namespace boat::replay

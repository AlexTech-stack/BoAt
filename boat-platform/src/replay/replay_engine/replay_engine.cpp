#include "replay_engine/replay_engine.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <vector>

namespace boat::replay {
namespace {

constexpr std::uint32_t kTraceMagic = 0xB0A7B0A7;

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

  ParseTickDurationFromEnv();
  tick_timer_ = boat::hil::TickTimer::Create(tick_duration_);
  replay_base_time_ = std::chrono::steady_clock::now();
  replay_base_tick_ = config.start_tick;

  replay_thread_ = std::thread(&ReplayController::ReplayLoop, this);
}

void ReplayController::StartFromEvents(const boat::store::EventFilter& filter,
                                        const ReplayConfig& replay_cfg) {
  const auto events = event_store_.Query(filter);
  if (events.empty()) {
    return;
  }

  std::vector<std::uint8_t> trace_data;
  for (const auto& event : events) {
    boat::store::TraceRecordHeader header{};
    header.magic = kTraceMagic;
    header.event_type = static_cast<std::uint32_t>(event.tick);
    header.tick = event.tick;
    header.wall_time_ns = event.wall_time_ns;
    header.payload_size = static_cast<std::uint32_t>(event.value_blob.size());

    trace_data.insert(trace_data.end(), reinterpret_cast<const std::uint8_t*>(&header),
                      reinterpret_cast<const std::uint8_t*>(&header) + sizeof(header));
    trace_data.insert(trace_data.end(), event.value_blob.begin(), event.value_blob.end());
  }

  const std::string trace_id = "evtstore_replay_" +
                               filter.simulation_id.value_or("default") + "_" +
                               std::to_string(events[0].tick);

  const std::string storage_path = "/tmp/" + trace_id + ".trace";

  boat::store::TraceRecord meta;
  meta.id = trace_id;
  meta.simulation_id = filter.simulation_id.value_or("");
  meta.start_tick = events.front().tick;
  meta.end_tick = events.back().tick;
  meta.format = boat::store::TraceRecord::Format::BINARY;
  meta.storage_path = storage_path;

  trace_store_.WriteTrace(meta, std::span<const std::uint8_t>(trace_data));

  ReplayConfig config = replay_cfg;
  config.trace_id = trace_id;
  config.start_tick = events.front().tick;
  Start(config);
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
  if (tick_timer_) {
    tick_timer_->Stop();
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

void ReplayController::SetEventForwarder(EventForwarder forwarder) {
  std::lock_guard<std::mutex> lock(forwarder_mutex_);
  event_forwarder_ = std::move(forwarder);
}

void ReplayController::ParseTickDurationFromEnv() {
  //   BOAT_NODE_TICK_US=N   — set tick in μs (high-precision, overrides MS)
  //   BOAT_NODE_TICK_MS=N   — set tick in ms (default 1)
  // Same pattern as the gateway node tick in main.cpp.
  const char* us_env = std::getenv("BOAT_NODE_TICK_US");
  if (us_env != nullptr) {
    char* end = nullptr;
    auto val = std::strtoul(us_env, &end, 10);
    if (end != us_env && val > 0) {
      tick_duration_ = std::chrono::microseconds(val);
      return;
    }
  }
  const char* ms_env = std::getenv("BOAT_NODE_TICK_MS");
  if (ms_env != nullptr) {
    char* end = nullptr;
    auto val = std::strtoul(ms_env, &end, 10);
    if (end != ms_env && val > 0) {
      tick_duration_ = std::chrono::milliseconds(val);
      return;
    }
  }
  // Default: 1ms.
  tick_duration_ = std::chrono::milliseconds(1);
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
        // Reset absolute-timing baseline so the target tick fires immediately.
        replay_base_time_ = std::chrono::steady_clock::now();
        replay_base_tick_ = target_tick;
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

      // Absolute-time deadline for this record's tick.
      // t_deadline = t_base + (tick - tick_base) * tick_duration / multiplier
      double speed_multiplier = active_config_.speed_multiplier;
      if (speed_multiplier <= 0.0) {
        speed_multiplier = 1.0;
      }
      if (active_config_.speed == ReplaySpeed::REAL_TIME ||
          active_config_.speed == ReplaySpeed::ACCELERATED) {
        const auto tick_offset_ns = static_cast<double>(
            (header.tick - replay_base_tick_) * tick_duration_.count());
        const auto deadline_offset = std::chrono::nanoseconds(
            static_cast<std::uint64_t>(tick_offset_ns / speed_multiplier));
        tick_timer_->WaitUntil(replay_base_time_ + deadline_offset);
      }

      std::vector<std::uint8_t> payload(header.payload_size);
      if (header.payload_size > 0U) {
        std::memcpy(payload.data(), mapped_trace_.data() + offset, header.payload_size);
      }
      offset += header.payload_size;

      {
        std::lock_guard<std::mutex> lock(forwarder_mutex_);
        if (event_forwarder_) {
          event_forwarder_(header.event_type, header.tick, payload);
        }
      }

      boat::core::BusEvent event;
      event.type = header.event_type;
      event.tick = header.tick;
      event.payload = boat::core::UnknownPayload{header.event_type, payload};
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

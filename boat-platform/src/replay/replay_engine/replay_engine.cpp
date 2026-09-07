// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "replay_engine/replay_engine.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <vector>

#include <arpa/inet.h>

#include "boat/v1/frame.pb.h"
#include "core/frame.h"

namespace boat::replay {
namespace {

std::uint64_t FrameTimestampToMs(std::uint64_t timestamp_ns) {
  return timestamp_ns / 1'000'000ULL;
}

// Resolve a 1-based trace channel to a target CAN interface using the
// replay's --buses mapping (ch1->buses[0], ch2->buses[1], ...); falls back
// to the last bus if there are more channels than buses, and to "vcan0" if
// no buses were configured at all -- this is what makes an imported trace
// replayable on different hardware without re-importing it.
std::string ResolveCanIface(const ReplayConfig& config, std::uint32_t channel) {
  if (config.buses.empty()) {
    return "vcan0";
  }
  const std::size_t idx = channel == 0 ? 0 : static_cast<std::size_t>(channel - 1);
  return config.buses[std::min(idx, config.buses.size() - 1)];
}

// Render a raw 4- or 16-byte IP address back to its canonical string form
// (matching Python's `str(ipaddress.ip_address(...))`, RFC 5952) so it can
// be looked up in a --mac-map keyed by that string.
std::string IpBytesToString(const std::string& ip_bytes, std::uint32_t ip_version) {
  if (ip_bytes.empty()) {
    return {};
  }
  char buf[INET6_ADDRSTRLEN]{};
  const int family = (ip_version == 6) ? AF_INET6 : AF_INET;
  if (inet_ntop(family, ip_bytes.data(), buf, sizeof(buf)) == nullptr) {
    return {};
  }
  return std::string(buf);
}

bool ParseMacInto(const std::string& mac_str, std::uint8_t out[6]) {
  unsigned int bytes[6];
  if (std::sscanf(mac_str.c_str(), "%x:%x:%x:%x:%x:%x", &bytes[0], &bytes[1],
                   &bytes[2], &bytes[3], &bytes[4], &bytes[5]) != 6) {
    return false;
  }
  for (int i = 0; i < 6; ++i) {
    out[i] = static_cast<std::uint8_t>(bytes[i]);
  }
  return true;
}

boat::core::Frame ProtoToCoreFrame(const boat::v1::Frame& pf, const ReplayConfig& config) {
  std::vector<std::uint8_t> payload(pf.payload().begin(), pf.payload().end());
  std::uint64_t ts = pf.timestamp_ns();

  boat::core::Frame f;
  switch (pf.bus_type()) {
    case boat::v1::Frame::CAN:
    case boat::v1::Frame::CANFD: {
      const auto& cm = pf.can();
      std::string iface = ResolveCanIface(config, cm.channel());
      f = boat::core::Frame::FromCan(
          std::move(iface), cm.can_id(), static_cast<std::uint8_t>(cm.dlc()),
          static_cast<std::uint8_t>(cm.flags()), std::move(payload),
          pf.bus_type() == boat::v1::Frame::CANFD);
      break;
    }
    case boat::v1::Frame::ETHERNET: {
      const auto& em = pf.eth();
      uint8_t dm[6]{}, sm[6]{};
      std::memcpy(dm, em.dst_mac().data(), std::min(em.dst_mac().size(), 6UL));
      std::memcpy(sm, em.src_mac().data(), std::min(em.src_mac().size(), 6UL));

      if (!config.mac_map.empty()) {
        const std::string dst_ip_str = IpBytesToString(em.dst_ip(), em.ip_version());
        const std::string src_ip_str = IpBytesToString(em.src_ip(), em.ip_version());
        if (auto it = config.mac_map.find(dst_ip_str); it != config.mac_map.end()) {
          ParseMacInto(it->second, dm);
        }
        if (auto it = config.mac_map.find(src_ip_str); it != config.mac_map.end()) {
          ParseMacInto(it->second, sm);
        }
      }

      const uint8_t* sip = em.src_ip().empty()
                               ? nullptr
                               : reinterpret_cast<const uint8_t*>(em.src_ip().data());
      const uint8_t* dip = em.dst_ip().empty()
                               ? nullptr
                               : reinterpret_cast<const uint8_t*>(em.dst_ip().data());
      std::string iface = config.eth_iface.empty() ? pf.iface() : config.eth_iface;
      f = boat::core::Frame::FromEthernet(
          std::move(iface), dm, sm, static_cast<std::uint16_t>(em.ethertype()),
          static_cast<std::uint16_t>(em.vlan_id()), sip,
          static_cast<std::uint8_t>(em.ip_version()), dip,
          std::move(payload));
      break;
    }
    case boat::v1::Frame::PDU: {
      const auto& pm = pf.pdu();
      f = boat::core::Frame::FromPdu(pf.iface(), pm.pdu_id(), std::move(payload));
      break;
    }
    default:
      break;
  }
  f.set_timestamp_ns(ts);
  return f;
}

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
  {
    std::lock_guard<std::mutex> lock(error_mutex_);
    last_error_.clear();
  }

  // The driving TickAuthority's interval wins when one has been supplied;
  // only a standalone controller falls back to the environment.
  if (!tick_interval_explicit_) ParseTickDurationFromEnv();

  pump_offset_         = 0;
  pump_has_records_    = false;
  authority_anchored_  = false;
  authority_base_tick_ = 0;
  loop_resume_tick_    = 0;
  replay_base_tick_    = config.start_tick;

  // No thread and no clock: a TickAuthority phase drives this controller via
  // PumpDueRecords. An empty trace has nothing to deliver, so it is finished
  // the moment it starts.
  running_.store(!mapped_trace_.empty());
}

void ReplayController::SetTickInterval(std::chrono::nanoseconds interval) {
  if (interval <= std::chrono::nanoseconds::zero()) return;
  tick_duration_          = interval;
  tick_interval_explicit_ = true;
}

void ReplayController::StartFromEvents(const boat::store::EventFilter& filter,
                                        const ReplayConfig& replay_cfg) {
  // Event store replay now directly replays events without building a
  // physical trace file.  The data is already in memory.
  // For simplicity we create an in-memory protobuf trace here.
  const auto events = event_store_.Query(filter);
  if (events.empty()) {
    return;
  }

  std::string trace_id = "evtstore_replay_" +
                         filter.simulation_id.value_or("default") + "_" +
                         std::to_string(events[0].tick);
  std::string storage_path = "/tmp/" + trace_id + ".trace";

  // Build a minimal protobuf-frame trace (CAN-only, with raw payload).
  std::vector<std::uint8_t> trace_data;
  for (const auto& event : events) {
    boat::v1::Frame proto;
    proto.set_bus_type(boat::v1::Frame::CAN);
    proto.set_timestamp_ns(event.wall_time_ns);
    proto.set_payload(event.value_blob.data(), event.value_blob.size());
    proto.mutable_can()->set_can_id(static_cast<std::uint32_t>(event.tick));
    proto.mutable_can()->set_dlc(static_cast<std::uint32_t>(event.value_blob.size()));
    proto.mutable_can()->set_flags(0);

    std::string raw = proto.SerializeAsString();
    std::uint32_t len = static_cast<std::uint32_t>(raw.size());
    trace_data.insert(trace_data.end(),
                       reinterpret_cast<const std::uint8_t*>(&len),
                       reinterpret_cast<const std::uint8_t*>(&len) + sizeof(len));
    trace_data.insert(trace_data.end(), raw.begin(), raw.end());
  }

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
  paused_.store(false);
  pause_cv_.notify_all();
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

const ReplayConfig& ReplayController::GetActiveConfig() const {
  return active_config_;
}

void ReplayController::ParseTickDurationFromEnv() {
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
  tick_duration_ = std::chrono::milliseconds(1);
}

bool ReplayController::SeekToTick(std::uint64_t target_tick, std::size_t& offset,
                                   std::uint64_t& landed_tick) const {
  offset = 0;
  while (offset + sizeof(std::uint32_t) <= mapped_trace_.size()) {
    std::uint32_t record_len;
    std::memcpy(&record_len, mapped_trace_.data() + offset, sizeof(record_len));
    offset += sizeof(record_len);
    if (record_len == 0 || offset + record_len > mapped_trace_.size()) {
      throw std::runtime_error("invalid trace record length");
    }
    boat::v1::Frame pf;
    if (!pf.ParseFromArray(mapped_trace_.data() + offset, record_len)) {
      throw std::runtime_error("invalid protobuf frame record");
    }
    offset += record_len;
    const std::uint64_t record_tick = FrameTimestampToMs(pf.timestamp_ns());
    if (record_tick >= target_tick) {
      offset -= (sizeof(record_len) + record_len);
      landed_tick = record_tick;
      return true;
    }
  }
  offset = mapped_trace_.size();
  return false;
}

std::span<const std::uint8_t> ReplayController::ReadRecordAt(
    std::size_t& offset, boat::v1::Frame& pf) const {
  std::uint32_t record_len;
  std::memcpy(&record_len, mapped_trace_.data() + offset, sizeof(record_len));
  offset += sizeof(record_len);
  if (record_len == 0 || offset + record_len > mapped_trace_.size()) {
    throw std::runtime_error("invalid trace record length");
  }
  if (!pf.ParseFromArray(mapped_trace_.data() + offset, record_len)) {
    throw std::runtime_error("invalid protobuf frame record");
  }
  const auto* begin = mapped_trace_.data() + offset;
  offset += record_len;
  return {begin, record_len};
}

void ReplayController::DispatchRecord(const boat::v1::Frame& pf, std::uint64_t tick,
                                      std::span<const std::uint8_t> raw) {
  // ── Dispatch via core::Frame ──────────────────────────────────────────
  auto core_frame = ProtoToCoreFrame(pf, active_config_);

  {
    std::lock_guard<std::mutex> lock(forwarder_mutex_);
    if (event_forwarder_) {
      event_forwarder_(core_frame);
    }
  }

  // ── Publish replay event for gRPC streaming ───────────────────────────
  std::string proto_bytes(reinterpret_cast<const char*>(raw.data()), raw.size());

  boat::core::BusEvent replay_event;
  replay_event.type = kReplayBusEventType;
  replay_event.tick = tick;
  replay_event.payload = proto_bytes;
  event_bus_.Publish(std::move(replay_event));

  // ── Push to internal queue (StreamReplay) ─────────────────────────────
  {
    std::lock_guard<std::mutex> lock(event_queue_mutex_);
    event_queue_.push_back({tick, proto_bytes});
  }

  // ── Store in event store ──────────────────────────────────────────────
  {
    std::vector<std::uint8_t> payload_copy(proto_bytes.begin(), proto_bytes.end());
    boat::store::EventRecord record;
    record.id = std::to_string(tick) + "_" + std::to_string(pf.can().can_id());
    record.simulation_id = active_config_.trace_id;
    record.tick = tick;
    record.wall_time_ns = static_cast<std::int64_t>(pf.timestamp_ns());
    record.signal_id = std::to_string(pf.can().can_id());
    record.value_type = 0;
    record.value_blob = std::move(payload_copy);
    record.tags = "{}";
    std::array<boat::store::EventRecord, 1> batch{record};
    event_store_.InsertBatch(std::span<const boat::store::EventRecord>(batch));
  }

  current_tick_.store(tick);
}

bool ReplayController::IsRecordDue(std::uint64_t record_tick,
                                  std::uint64_t authority_tick) const {
  double multiplier = active_config_.speed_multiplier;
  if (multiplier <= 0.0) multiplier = 1.0;

  const std::uint64_t elapsed_ticks =
      authority_tick > authority_base_tick_ ? authority_tick - authority_base_tick_ : 0;
  const double elapsed_ns =
      static_cast<double>(elapsed_ticks) * static_cast<double>(tick_duration_.count());

  // Trace ticks are milliseconds (FrameTimestampToMs). A record earlier than
  // the pass anchor -- possible in a hand-edited trace -- is due immediately
  // rather than being allowed to underflow this unsigned delta.
  const std::uint64_t delta_ms =
      record_tick > replay_base_tick_ ? record_tick - replay_base_tick_ : 0;
  const double required_ns = (static_cast<double>(delta_ms) * 1'000'000.0) / multiplier;

  return elapsed_ns >= required_ns;
}

void ReplayController::AnchorAt(std::uint64_t target_tick, std::uint64_t authority_tick) {
  // Anchor to the tick of the record actually landed on, not the raw target --
  // trace timestamps are absolute epoch milliseconds, so a target of 0 would
  // otherwise anchor the schedule decades before the first real record.
  std::uint64_t landed_tick = target_tick;
  pump_offset_ = 0;
  SeekToTick(target_tick, pump_offset_, landed_tick);
  current_tick_.store(landed_tick);
  replay_base_tick_    = landed_tick;
  authority_base_tick_ = authority_tick;
  authority_anchored_  = true;
}

void ReplayController::FinishPass(std::uint64_t authority_tick) {
  if (active_config_.loop_delay_ms > 0 && pump_has_records_) {
    const auto delay_ns =
        std::chrono::nanoseconds(std::chrono::milliseconds(active_config_.loop_delay_ms));
    std::uint64_t delay_ticks =
        static_cast<std::uint64_t>(delay_ns.count() / tick_duration_.count());
    if (delay_ticks == 0) delay_ticks = 1;
    loop_resume_tick_ = authority_tick + delay_ticks;
    return;
  }
  running_.store(false);
  paused_.store(false);
  pause_cv_.notify_all();
}

void ReplayController::PumpDueRecords(std::uint64_t authority_tick) {
  if (!running_.load()) return;

  try {
    if (mapped_trace_.empty()) {
      running_.store(false);
      return;
    }

    if (seek_pending_.exchange(false)) {
      AnchorAt(requested_seek_tick_.load(), authority_tick);
    } else if (!authority_anchored_) {
      authority_base_tick_ = authority_tick;
      authority_anchored_  = true;
    }

    if (paused_.load()) return;

    if (loop_resume_tick_ != 0) {
      if (authority_tick < loop_resume_tick_) return;
      loop_resume_tick_ = 0;
      AnchorAt(active_config_.start_tick, authority_tick);
    }

    // STEP_BY_STEP ignores due-times entirely: one record per Resume().
    const bool stepping = active_config_.speed == ReplaySpeed::STEP_BY_STEP;

    while (running_.load() &&
           pump_offset_ + sizeof(std::uint32_t) <= mapped_trace_.size()) {
      // Read at a probe cursor so a record that is not yet due stays unread
      // and is reconsidered on the next tick.
      std::size_t probe = pump_offset_;
      boat::v1::Frame pf;
      const auto raw = ReadRecordAt(probe, pf);
      const std::uint64_t record_tick = FrameTimestampToMs(pf.timestamp_ns());

      if (!stepping && !IsRecordDue(record_tick, authority_tick)) break;

      pump_offset_      = probe;
      pump_has_records_ = true;
      DispatchRecord(pf, record_tick, raw);

      if (stepping) {
        paused_.store(true);
        return;
      }
    }

    if (pump_offset_ + sizeof(std::uint32_t) > mapped_trace_.size()) {
      FinishPass(authority_tick);
    }
  } catch (const std::exception& ex) {
    {
      std::lock_guard<std::mutex> lock(error_mutex_);
      last_error_ = ex.what();
    }
    paused_.store(false);
    running_.store(false);
    pause_cv_.notify_all();
  } catch (...) {
    {
      std::lock_guard<std::mutex> lock(error_mutex_);
      last_error_ = "unknown replay error";
    }
    paused_.store(false);
    running_.store(false);
    pause_cv_.notify_all();
  }
}

void ReplayController::PushEvent(std::uint64_t tick, std::string payload) {
  std::lock_guard<std::mutex> lock(event_queue_mutex_);
  event_queue_.push_back({tick, std::move(payload)});
}

std::vector<ReplayController::ReplayEventEntry> ReplayController::ConsumeEvents() {
  std::lock_guard<std::mutex> lock(event_queue_mutex_);
  std::vector<ReplayEventEntry> result;
  result.reserve(event_queue_.size());
  while (!event_queue_.empty()) {
    result.push_back(std::move(event_queue_.front()));
    event_queue_.pop_front();
  }
  return result;
}

}  // namespace boat::replay
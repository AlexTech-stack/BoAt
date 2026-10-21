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
#include <memory>
#include <stdexcept>
#include <utility>
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

// Set while this thread is inside PumpDueRecords. A forwarder callback that
// calls Stop()/Start() runs on the pump thread, and waiting there for the
// in-flight pump to drain would wait on itself.
thread_local bool t_in_pump = false;

}  // namespace

struct ReplayController::Session {
  Session(ReplayConfig cfg, boat::store::MappedTracePtr mapped,
          std::chrono::nanoseconds tick)
      : config(std::move(cfg)),
        trace(std::move(mapped)),
        bytes(trace ? trace->bytes : std::span<const std::uint8_t>{}),
        tick_duration(tick),
        requested_seek_tick(config.start_tick),
        replay_base_tick(config.start_tick) {}

  // ── Immutable after Start publishes the session ──────────────────────────
  const ReplayConfig config;
  // Owns the mapping: as long as the pump holds this session, `bytes` stays
  // valid, whatever Stop/Start do meanwhile.
  const boat::store::MappedTracePtr trace;
  const std::span<const std::uint8_t> bytes;
  const std::chrono::nanoseconds tick_duration;

  // ── Control flags: written by control calls, read by the pump ────────────
  std::atomic<bool> running{false};
  // Checked before every record, so Stop waits for at most one dispatch.
  std::atomic<bool> stop_requested{false};
  std::atomic<bool> paused{false};
  // Resume() must shift authority_base_tick by the time spent paused; the
  // shift happens on the pump thread, which is the only one that knows the
  // current authority tick.
  std::atomic<bool> resume_pending{false};
  // Starts pending so the first pump anchors at config.start_tick.
  std::atomic<bool> seek_pending{true};
  std::atomic<std::uint64_t> requested_seek_tick;

  // ── Pump thread only ─────────────────────────────────────────────────────
  // Tick-driven scheduling: replay_base_tick is the trace tick the current
  // pass is anchored to and authority_base_tick the authority tick it was
  // anchored at. A record's due time is the difference between those two
  // deltas -- no wall clock anywhere.
  std::uint64_t replay_base_tick;
  std::uint64_t authority_base_tick{0};
  bool          authority_anchored{false};
  std::uint64_t paused_at_tick{0};  // first tick observed paused
  bool          paused_seen{false};
  // Read cursor into `bytes`, and the tick at which a looping replay may begin
  // its next pass (0 = not waiting on a loop delay).
  std::size_t   pump_offset{0};
  bool          pump_has_records{false};
  std::uint64_t loop_resume_tick{0};
};

ReplayController::ReplayController(boat::store::ITraceStore& trace_store,
                                   boat::store::IEventStore& event_store,
                                   boat::core::EventBus& event_bus)
    : trace_store_(trace_store), event_store_(event_store), event_bus_(event_bus) {}

ReplayController::~ReplayController() {
  // Destructors cannot leave a pump holding `this`: wait without the bounded
  // timeout even if a wedged bus write makes that slow.
  std::lock_guard<std::mutex> control(control_mutex_);
  StopLocked(/*bounded=*/false);
}

void ReplayController::Start(const ReplayConfig& config) {
  std::lock_guard<std::mutex> control(control_mutex_);
  StartLocked(config);
}

void ReplayController::StartLocked(const ReplayConfig& config) {
  // Map before touching the current replay: if this throws, whatever was
  // playing keeps playing. Disk I/O happens here, under control_mutex_ only --
  // the tick thread never waits on that lock.
  auto trace = trace_store_.MapTrace(config.trace_id);

  // The driving TickAuthority's interval wins when one has been supplied;
  // only a standalone controller falls back to the environment.
  std::chrono::nanoseconds tick{};
  bool explicit_tick = false;
  {
    std::lock_guard<std::mutex> lock(session_mutex_);
    tick = tick_duration_;
    explicit_tick = tick_interval_explicit_;
  }
  if (!explicit_tick) tick = TickDurationFromEnv();

  auto next = std::make_shared<Session>(config, std::move(trace), tick);
  // No thread and no clock: a TickAuthority phase drives this controller via
  // PumpDueRecords. An empty trace has nothing to deliver, so it is finished
  // the moment it starts.
  next->running.store(!next->bytes.empty());

  StopLocked();
  {
    std::lock_guard<std::mutex> lock(error_mutex_);
    last_error_.clear();
  }
  {
    std::lock_guard<std::mutex> lock(session_mutex_);
    session_ = std::move(next);
  }
}

void ReplayController::SetTickInterval(std::chrono::nanoseconds interval) {
  if (interval <= std::chrono::nanoseconds::zero()) return;
  std::lock_guard<std::mutex> lock(session_mutex_);
  tick_duration_          = interval;
  tick_interval_explicit_ = true;
}

void ReplayController::StartFromEvents(const boat::store::EventFilter& filter,
                                        const ReplayConfig& replay_cfg) {
  // Held across writing the trace file as well as starting it: two concurrent
  // calls for one simulation would otherwise race on the same /tmp path.
  std::lock_guard<std::mutex> control(control_mutex_);

  // Event store replay now directly replays events without building a
  // physical trace file.  The data is already in memory.
  // For simplicity we create an in-memory protobuf trace here.
  const auto events = event_store_.Query(filter);
  if (events.empty()) {
    return;
  }

  const std::string simulation_id = filter.simulation_id.value_or("default");
  if (!boat::store::IsValidTraceId(simulation_id)) {
    throw std::runtime_error("invalid simulation_id for event replay");
  }
  std::string trace_id = "evtstore_replay_" + simulation_id + "_" +
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

  // Safe even if this very trace is playing: WriteTrace unlinks before
  // recreating, so the playing session keeps its mapping of the old file.
  trace_store_.WriteTrace(meta, std::span<const std::uint8_t>(trace_data));

  ReplayConfig config = replay_cfg;
  config.trace_id = trace_id;
  config.start_tick = events.front().tick;
  StartLocked(config);
}

std::shared_ptr<ReplayController::Session> ReplayController::CurrentSession() const {
  std::lock_guard<std::mutex> lock(session_mutex_);
  return session_;
}

void ReplayController::Seek(std::uint64_t tick) {
  if (auto s = CurrentSession()) {
    s->requested_seek_tick.store(tick);
    s->seek_pending.store(true);
  }
}

void ReplayController::Pause() {
  if (auto s = CurrentSession()) s->paused.store(true);
}

void ReplayController::Resume() {
  // The pump thread shifts the timeline when it next runs; doing it here
  // would need the authority tick, which only PumpDueRecords receives.
  if (auto s = CurrentSession()) {
    s->resume_pending.store(true);
    s->paused.store(false);
  }
}

void ReplayController::Stop() {
  std::lock_guard<std::mutex> control(control_mutex_);
  StopLocked();
}

void ReplayController::StopLocked(bool bounded) {
  std::shared_ptr<Session> old;
  {
    std::unique_lock<std::mutex> lock(session_mutex_);
    old = std::move(session_);
    if (old) {
      old->stop_requested.store(true);
      old->running.store(false);
      old->paused.store(false);
    }
    // Drain an in-flight pump so nothing from the stopped replay reaches the
    // sink after we return. Never from the pump thread itself (a forwarder
    // callback calling Stop), which would wait on its own pin. Destruction
    // waits without the timeout: the pump still needs `this` to unpin.
    if (!t_in_pump && pumps_in_flight_ != 0) {
      if (bounded) {
        if (!pump_idle_cv_.wait_for(lock, kStopDrainTimeout,
                                    [this] { return pumps_in_flight_ == 0; })) {
          std::fprintf(stderr,
                       "[Replay] Stop: tick phase still dispatching after %llds; "
                       "returning without waiting further\n",
                       static_cast<long long>(kStopDrainTimeout.count()));
        }
      } else {
        pump_idle_cv_.wait(lock, [this] { return pumps_in_flight_ == 0; });
      }
    }
  }
  // `old` is released here, outside every lock. After a completed drain this
  // is the last reference, so the trace is unmapped on the caller's thread,
  // never on the tick thread.
}

bool ReplayController::HasError() const {
  std::lock_guard<std::mutex> lock(error_mutex_);
  return !last_error_.empty();
}

std::string ReplayController::LastError() const {
  std::lock_guard<std::mutex> lock(error_mutex_);
  return last_error_;
}

void ReplayController::RecordError(std::string message) {
  std::lock_guard<std::mutex> lock(error_mutex_);
  last_error_ = std::move(message);
}

bool ReplayController::IsRunning() const {
  std::lock_guard<std::mutex> lock(session_mutex_);
  return session_ != nullptr && session_->running.load();
}

void ReplayController::SetEventForwarder(EventForwarder forwarder) {
  auto shared = forwarder ? std::make_shared<const EventForwarder>(std::move(forwarder))
                          : nullptr;
  std::lock_guard<std::mutex> lock(forwarder_mutex_);
  event_forwarder_ = std::move(shared);
}

ReplayConfig ReplayController::GetActiveConfig() const {
  std::lock_guard<std::mutex> lock(session_mutex_);
  return session_ != nullptr ? session_->config : ReplayConfig{};
}

std::chrono::nanoseconds ReplayController::TickDurationFromEnv() {
  const char* us_env = std::getenv("BOAT_NODE_TICK_US");
  if (us_env != nullptr) {
    char* end = nullptr;
    auto val = std::strtoul(us_env, &end, 10);
    if (end != us_env && val > 0) {
      return std::chrono::microseconds(val);
    }
  }
  const char* ms_env = std::getenv("BOAT_NODE_TICK_MS");
  if (ms_env != nullptr) {
    char* end = nullptr;
    auto val = std::strtoul(ms_env, &end, 10);
    if (end != ms_env && val > 0) {
      return std::chrono::milliseconds(val);
    }
  }
  return std::chrono::milliseconds(1);
}

bool ReplayController::SeekToTick(std::span<const std::uint8_t> bytes,
                                  std::uint64_t target_tick, std::size_t& offset,
                                  std::uint64_t& landed_tick) {
  offset = 0;
  while (offset + sizeof(std::uint32_t) <= bytes.size()) {
    std::uint32_t record_len;
    std::memcpy(&record_len, bytes.data() + offset, sizeof(record_len));
    offset += sizeof(record_len);
    if (record_len == 0 || offset + record_len > bytes.size()) {
      throw std::runtime_error("invalid trace record length");
    }
    boat::v1::Frame pf;
    if (!pf.ParseFromArray(bytes.data() + offset, record_len)) {
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
  offset = bytes.size();
  return false;
}

std::span<const std::uint8_t> ReplayController::ReadRecordAt(
    std::span<const std::uint8_t> bytes, std::size_t& offset, boat::v1::Frame& pf) {
  std::uint32_t record_len;
  std::memcpy(&record_len, bytes.data() + offset, sizeof(record_len));
  offset += sizeof(record_len);
  if (record_len == 0 || offset + record_len > bytes.size()) {
    throw std::runtime_error("invalid trace record length");
  }
  if (!pf.ParseFromArray(bytes.data() + offset, record_len)) {
    throw std::runtime_error("invalid protobuf frame record");
  }
  const auto* begin = bytes.data() + offset;
  offset += record_len;
  return {begin, record_len};
}

void ReplayController::DispatchRecord(const Session& s, const boat::v1::Frame& pf,
                                      std::uint64_t tick,
                                      std::span<const std::uint8_t> raw) {
  // ── Dispatch via core::Frame ──────────────────────────────────────────
  auto core_frame = ProtoToCoreFrame(pf, s.config);

  // Copy the forwarder out and call it unlocked: it runs FrameSink -> bus ->
  // plugins' on_frame, none of which may be called under one of our locks.
  std::shared_ptr<const EventForwarder> forwarder;
  {
    std::lock_guard<std::mutex> lock(forwarder_mutex_);
    forwarder = event_forwarder_;
  }
  if (forwarder) {
    (*forwarder)(core_frame);
  }

  // ── Publish replay event for gRPC streaming ───────────────────────────
  std::string proto_bytes(reinterpret_cast<const char*>(raw.data()), raw.size());

  if (event_bus_.HasSubscribers(kReplayBusEventType)) {
    boat::core::BusEvent replay_event;
    replay_event.type = kReplayBusEventType;
    replay_event.tick = tick;
    replay_event.payload = proto_bytes;
    event_bus_.Publish(std::move(replay_event));
  }

  // ── Push to internal queue (StreamReplay) ─────────────────────────────
  {
    std::lock_guard<std::mutex> lock(event_queue_mutex_);
    PushEventLocked(tick, proto_bytes);
  }

  // ── Store in event store ──────────────────────────────────────────────
  {
    std::vector<std::uint8_t> payload_copy(proto_bytes.begin(), proto_bytes.end());
    boat::store::EventRecord record;
    record.id = std::to_string(tick) + "_" + std::to_string(pf.can().can_id());
    record.simulation_id = s.config.trace_id;
    record.tick = tick;
    record.wall_time_ns = static_cast<std::int64_t>(pf.timestamp_ns());
    record.signal_id = std::to_string(pf.can().can_id());
    record.value_type = 0;
    record.value_blob = std::move(payload_copy);
    record.tags = "{}";
    std::array<boat::store::EventRecord, 1> batch{record};
    event_store_.InsertBatch(std::span<const boat::store::EventRecord>(batch));
  }
}

bool ReplayController::IsRecordDue(const Session& s, std::uint64_t record_tick,
                                   std::uint64_t authority_tick) {
  double multiplier = s.config.speed_multiplier;
  if (multiplier <= 0.0) multiplier = 1.0;

  const std::uint64_t elapsed_ticks =
      authority_tick > s.authority_base_tick ? authority_tick - s.authority_base_tick : 0;
  const double elapsed_ns =
      static_cast<double>(elapsed_ticks) * static_cast<double>(s.tick_duration.count());

  // Trace ticks are milliseconds (FrameTimestampToMs). A record earlier than
  // the pass anchor -- possible in a hand-edited trace -- is due immediately
  // rather than being allowed to underflow this unsigned delta.
  const std::uint64_t delta_ms =
      record_tick > s.replay_base_tick ? record_tick - s.replay_base_tick : 0;
  const double required_ns = (static_cast<double>(delta_ms) * 1'000'000.0) / multiplier;

  return elapsed_ns >= required_ns;
}

void ReplayController::AnchorAt(Session& s, std::uint64_t target_tick,
                                std::uint64_t authority_tick) {
  // Anchor to the tick of the record actually landed on, not the raw target --
  // trace timestamps are absolute epoch milliseconds, so a target of 0 would
  // otherwise anchor the schedule decades before the first real record.
  std::uint64_t landed_tick = target_tick;
  s.pump_offset = 0;
  SeekToTick(s.bytes, target_tick, s.pump_offset, landed_tick);
  s.replay_base_tick    = landed_tick;
  s.authority_base_tick = authority_tick;
  s.authority_anchored  = true;
}

void ReplayController::FinishPass(Session& s, std::uint64_t authority_tick) {
  if (s.config.loop_delay_ms > 0 && s.pump_has_records) {
    const auto delay_ns =
        std::chrono::nanoseconds(std::chrono::milliseconds(s.config.loop_delay_ms));
    std::uint64_t delay_ticks =
        static_cast<std::uint64_t>(delay_ns.count() / s.tick_duration.count());
    if (delay_ticks == 0) delay_ticks = 1;
    s.loop_resume_tick = authority_tick + delay_ticks;
    return;
  }
  s.running.store(false);
  s.paused.store(false);
}

void ReplayController::PumpDueRecords(std::uint64_t authority_tick) {
  // Snapshot and pin the session together, so Stop can wait for exactly the
  // pumps that might still be reading it (same discipline as
  // PluginManager::busy_count_).
  std::shared_ptr<Session> s;
  {
    std::lock_guard<std::mutex> lock(session_mutex_);
    // pumps_in_flight_ != 0: a re-entrant or concurrent call. The cursor has
    // exactly one writer, so the second caller does nothing.
    if (session_ == nullptr || !session_->running.load() || pumps_in_flight_ != 0) {
      return;
    }
    s = session_;
    ++pumps_in_flight_;
  }

  t_in_pump = true;
  PumpSession(*s, authority_tick);
  t_in_pump = false;

  // Unpin before dropping our reference. A Stop()/destructor woken by this can
  // then return and destroy `this`; nothing below touches the controller. The
  // local `s` keeps the trace mapped until it goes out of scope, which may be
  // on this thread if Stop already timed out.
  {
    std::lock_guard<std::mutex> lock(session_mutex_);
    --pumps_in_flight_;
    pump_idle_cv_.notify_all();
  }
  s.reset();
}

void ReplayController::PumpSession(Session& s, std::uint64_t authority_tick) {
  try {
    if (s.stop_requested.load()) return;
    if (s.bytes.empty()) {
      s.running.store(false);
      return;
    }

    if (s.seek_pending.exchange(false)) {
      AnchorAt(s, s.requested_seek_tick.load(), authority_tick);
    } else if (!s.authority_anchored) {
      s.authority_base_tick = authority_tick;
      s.authority_anchored  = true;
    }

    if (s.paused.load()) {
      // Remember the first tick observed after Pause(), not the latest: the
      // pause duration is measured from where replay stopped, not from the
      // last tick the caller happened to pump while waiting.
      if (!s.paused_seen) {
        s.paused_at_tick = authority_tick;
        s.paused_seen = true;
      }
      return;
    }

    // A resume must not replay the time spent paused as a burst: shift the
    // anchor by the pause duration so elapsed replay time continues from where
    // it stopped. Without this, every record that came due during the pause is
    // dispatched on the first tick after Resume().
    if (s.resume_pending.exchange(false) && s.paused_seen) {
      const std::uint64_t pause_ticks =
          authority_tick > s.paused_at_tick ? authority_tick - s.paused_at_tick : 0;
      s.authority_base_tick += pause_ticks;
      s.paused_seen = false;
    }

    if (s.loop_resume_tick != 0) {
      if (authority_tick < s.loop_resume_tick) return;
      s.loop_resume_tick = 0;
      AnchorAt(s, s.config.start_tick, authority_tick);
    }

    // STEP_BY_STEP ignores due-times entirely: one record per Resume().
    const bool stepping = s.config.speed == ReplaySpeed::STEP_BY_STEP;

    while (s.running.load() && !s.stop_requested.load() &&
           s.pump_offset + sizeof(std::uint32_t) <= s.bytes.size()) {
      // Read at a probe cursor so a record that is not yet due stays unread
      // and is reconsidered on the next tick.
      std::size_t probe = s.pump_offset;
      boat::v1::Frame pf;
      const auto raw = ReadRecordAt(s.bytes, probe, pf);
      const std::uint64_t record_tick = FrameTimestampToMs(pf.timestamp_ns());

      if (!stepping && !IsRecordDue(s, record_tick, authority_tick)) break;

      s.pump_offset      = probe;
      s.pump_has_records = true;
      DispatchRecord(s, pf, record_tick, raw);

      if (stepping) {
        s.paused.store(true);
        return;
      }
    }

    if (s.stop_requested.load()) return;
    if (s.pump_offset + sizeof(std::uint32_t) > s.bytes.size()) {
      FinishPass(s, authority_tick);
    }
  } catch (const std::exception& ex) {
    RecordError(ex.what());
    s.paused.store(false);
    s.running.store(false);
  } catch (...) {
    RecordError("unknown replay error");
    s.paused.store(false);
    s.running.store(false);
  }
}

void ReplayController::PushEvent(std::uint64_t tick, std::string payload) {
  std::lock_guard<std::mutex> lock(event_queue_mutex_);
  PushEventLocked(tick, std::move(payload));
}

void ReplayController::PushEventLocked(std::uint64_t tick, std::string payload) {
  if (event_queue_.size() >= kMaxStreamEvents) {
    event_queue_.pop_front();
    ++dropped_events_;
    if (dropped_events_ == 1 || dropped_events_ % 1000 == 0) {
      std::fprintf(stderr,
                   "[Replay] StreamReplay backlog full: dropped %llu event(s) "
                   "total -- client cannot keep up\n",
                   static_cast<unsigned long long>(dropped_events_));
    }
  }
  event_queue_.push_back({tick, std::move(payload)});
}

std::uint64_t ReplayController::DroppedEventCount() const {
  std::lock_guard<std::mutex> lock(event_queue_mutex_);
  return dropped_events_;
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

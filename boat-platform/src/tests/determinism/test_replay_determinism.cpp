// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* System-level determinism test.

   The companion test in this directory (test_seed.cpp) exercises
   DeterminismEngine alone: it links boat_core and nothing else, so it cannot
   observe the gateway, replay, plugins, or a bus.  A seeded mt19937_64 is
   deterministic by construction, so that test cannot fail -- it pins the PRNG,
   not the product claim.

   The claim in README.md is "bit-identical replay across runs and
   environments".  Reproducing that requires the real path:

       trace file -> ReplayController -> FrameSink -> CanBusRegistry -> driver

   These tests run that path twice over one trace and compare what actually
   reached the wire.  A recording driver stands in for the hardware, and a
   1 ms tick thread mirrors the gateway's node tick thread (main.cpp) so that
   frame delivery races against tick advancement exactly as it does in
   production. */

#include <catch2/catch_test_macros.hpp>

#include <array>
#include <atomic>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <mutex>
#include <span>
#include <string>
#include <thread>
#include <vector>

#include "boat/v1/frame.pb.h"
#include "can_bus_registry.h"
#include "ethernet_bus_registry.h"
#include "event/event_bus.h"
#include "event_store/event_store.h"
#include "gateway/grpc_gateway/frame_sink.h"
#include "pdu/tick_timer.h"
#include "tick_authority.h"
#include "replay_engine/replay_engine.h"
#include "trace_store/trace_store.h"

namespace {

constexpr int kFrameCount = 8;
constexpr std::uint64_t kSpacingMs = 15;
constexpr std::size_t kPayloadLen = 4;

/* One frame as it reached the wire, plus the tick the node-side tick thread
   had advanced to at that moment. */
struct WireLogEntry {
  std::uint64_t observer_tick;
  std::uint32_t can_id;
  std::uint8_t dlc;
  std::array<std::uint8_t, 64> data;
};

class RecordingCanDriver : public boat::hil::IHalDriver {
 public:
  explicit RecordingCanDriver(const std::atomic<std::uint64_t>& observer_tick)
      : observer_tick_(observer_tick) {}

  bool Open() override { return true; }
  void Close() override {}

  bool ReadFrame(boat::hil::CanFrame&) override {
    // No inbound traffic; keep the registry's bridge thread off a spin loop.
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
    return false;
  }

  bool WriteFrame(const boat::hil::CanFrame& f) override {
    WireLogEntry entry{};
    entry.observer_tick = observer_tick_.load(std::memory_order_acquire);
    entry.can_id = f.can_id;
    entry.dlc = f.dlc;
    std::memcpy(entry.data.data(), f.data, sizeof(f.data));
    std::lock_guard<std::mutex> lock(mutex_);
    log_.push_back(entry);
    return true;
  }

  boat::hil::CanInterfaceInfo GetInfo() const override { return {}; }

  std::vector<WireLogEntry> Log() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return log_;
  }

 private:
  const std::atomic<std::uint64_t>& observer_tick_;
  mutable std::mutex mutex_;
  std::vector<WireLogEntry> log_;
};

/* Length-delimited boat.v1.Frame records, the format ReplayLoop parses. */
std::vector<std::uint8_t> BuildCanTrace() {
  std::vector<std::uint8_t> out;
  for (int i = 0; i < kFrameCount; ++i) {
    boat::v1::Frame pf;
    pf.set_bus_type(boat::v1::Frame::CAN);
    pf.set_timestamp_ns(static_cast<std::uint64_t>(i) * kSpacingMs * 1'000'000ULL);
    auto* cm = pf.mutable_can();
    cm->set_can_id(0x100 + static_cast<std::uint32_t>(i));
    cm->set_dlc(kPayloadLen);
    cm->set_channel(1);
    std::string payload(kPayloadLen, '\0');
    for (std::size_t b = 0; b < kPayloadLen; ++b) {
      payload[b] = static_cast<char>((i * kPayloadLen + b) & 0xFF);
    }
    pf.set_payload(payload);

    const std::string bytes = pf.SerializeAsString();
    const auto len = static_cast<std::uint32_t>(bytes.size());
    const auto* len_p = reinterpret_cast<const std::uint8_t*>(&len);
    out.insert(out.end(), len_p, len_p + sizeof(len));
    out.insert(out.end(), bytes.begin(), bytes.end());
  }
  return out;
}

/* Drive one full replay through the production path and return the wire log. */
std::vector<WireLogEntry> RunReplayOnce(const std::string& prefix,
                                        const std::vector<std::uint8_t>& trace_bytes) {
  const auto tmp = std::filesystem::temp_directory_path();
  const auto event_db = (tmp / (prefix + "_events.db")).string();
  const auto trace_db = (tmp / (prefix + "_traces.db")).string();
  const auto trace_file = (tmp / (prefix + "_trace.bin")).string();
  std::filesystem::remove(event_db);
  std::filesystem::remove(trace_db);

  std::vector<WireLogEntry> log;
  {
    boat::store::SqliteEventStore event_store(event_db);
    boat::store::FlatFileTraceStore trace_store(trace_db);

    boat::store::TraceRecord meta;
    meta.id = "determinism_trace";
    meta.storage_path = trace_file;
    meta.format = boat::store::TraceRecord::Format::BINARY;
    trace_store.WriteTrace(meta, std::span<const std::uint8_t>(trace_bytes));

    boat::core::EventBus event_bus;
    boat::hil::CanBusRegistry can_registry;
    boat::hil::EthernetBusRegistry eth_registry;
    boat::gateway::FrameSink frame_sink{can_registry, eth_registry};
    boat::replay::ReplayController replay_controller{trace_store, event_store, event_bus};

    std::atomic<std::uint64_t> observer_tick{0};
    auto driver = std::make_shared<RecordingCanDriver>(observer_tick);
    REQUIRE(can_registry.Add("vcan0", driver, event_bus));

    // Exactly the gateway's wiring: one authority, plugins first, replay
    // second. The plugin phase stands in for node_manager.TickAll.
    boat::hil::TickAuthority authority;
    authority.AddPhase("node_plugins", [&observer_tick](std::uint64_t tick) {
      observer_tick.store(tick, std::memory_order_release);
    });
    authority.AddPhase("replay", [&replay_controller](std::uint64_t tick) {
      replay_controller.PumpDueRecords(tick);
    });

    replay_controller.SetTickInterval(std::chrono::milliseconds(1));
    replay_controller.SetEventForwarder(
        [&frame_sink](const boat::core::Frame& f) { frame_sink.Publish(f); });

    boat::replay::ReplayConfig cfg;
    cfg.trace_id = meta.id;
    cfg.speed = boat::replay::ReplaySpeed::REAL_TIME;
    cfg.buses = {"vcan0"};
    replay_controller.Start(cfg);
    authority.Start(std::chrono::milliseconds(1), boat::hil::TimeSource::kRealTime);

    while (replay_controller.IsRunning()) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    authority.Stop();
    replay_controller.Stop();
    INFO("replay error: " << replay_controller.LastError());
    REQUIRE_FALSE(replay_controller.HasError());

    log = driver->Log();
  }

  std::filesystem::remove(event_db);
  std::filesystem::remove(trace_db);
  std::filesystem::remove(trace_file);
  return log;
}

/* Frame content and ordering only -- what was put on the wire. */
std::vector<std::uint8_t> ContentProjection(const std::vector<WireLogEntry>& log) {
  std::vector<std::uint8_t> out;
  for (const auto& e : log) {
    const auto* id_p = reinterpret_cast<const std::uint8_t*>(&e.can_id);
    out.insert(out.end(), id_p, id_p + sizeof(e.can_id));
    out.push_back(e.dlc);
    out.insert(out.end(), e.data.begin(), e.data.begin() + e.dlc);
  }
  return out;
}

/* Content plus the tick each frame was attributed to. */
std::vector<std::uint8_t> TickAttributedProjection(const std::vector<WireLogEntry>& log) {
  std::vector<std::uint8_t> out = ContentProjection(log);
  for (const auto& e : log) {
    const auto* t_p = reinterpret_cast<const std::uint8_t*>(&e.observer_tick);
    out.insert(out.end(), t_p, t_p + sizeof(e.observer_tick));
  }
  return out;
}

}  // namespace

TEST_CASE("Replay puts bit-identical frame content on the wire across runs",
          "[determinism][replay][system]") {
  const auto trace = BuildCanTrace();

  const auto run_a = RunReplayOnce("boat_det_a", trace);
  const auto run_b = RunReplayOnce("boat_det_b", trace);

  REQUIRE(run_a.size() == static_cast<std::size_t>(kFrameCount));
  REQUIRE(run_b.size() == static_cast<std::size_t>(kFrameCount));
  REQUIRE(ContentProjection(run_a) == ContentProjection(run_b));
}

/* Tick attribution, now a hard requirement.
 
   Replay used to pace itself on its own thread while plugins were ticked by a
   separate wall-clock thread, so which tick a frame landed in was decided by
   host scheduling: identical on an idle host (30/30) but not under contention
   (0/6, and the tick thread dropped ticks so the numbering itself drifted).
 
   Both now run as ordered phases of one TickAuthority -- plugins, then replay
   delivery, on the same thread, every tick. A record's due time is computed
   from elapsed *ticks* rather than elapsed wall time, so a loaded host makes
   the authority tick later in real terms without ever changing which tick a
   record comes due on. That is what lets this be a plain REQUIRE. */
TEST_CASE("Replay attributes frames to identical ticks across runs",
          "[determinism][replay][system]") {
  const auto trace = BuildCanTrace();

  const auto run_a = RunReplayOnce("boat_det_tick_a", trace);
  const auto run_b = RunReplayOnce("boat_det_tick_b", trace);

  REQUIRE(run_a.size() == run_b.size());
  REQUIRE(TickAttributedProjection(run_a) == TickAttributedProjection(run_b));
}

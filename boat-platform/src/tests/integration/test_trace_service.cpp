// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* Trace mapping lifetimes across concurrent RPCs (issue #63).

   The trace store used to keep one mapping per trace id. A second mapping of
   the same id replaced the first in that table, so whichever caller unmapped
   the id next tore down the *other* caller's mapping while it was still being
   read: two GetTrace calls on one trace, or a StopReplay landing while a
   StreamTrace of the same trace was mid-stream, faulted in the gateway. These
   tests drive exactly those RPC interleavings through the real service impls
   and a real FlatFileTraceStore, so an unmapped read crashes the binary
   rather than reading stale-but-valid memory. */

#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include <grpcpp/grpcpp.h>
#include <unistd.h>

#include "boat/v1/replay.grpc.pb.h"
#include "boat/v1/trace.grpc.pb.h"
#include "can_bus_registry.h"
#include "core/plugin/plugin_manager.h"
#include "ethernet_bus_registry.h"
#include "event_store/event_store.h"
#include "gateway/grpc_gateway/frame_sink.h"
#include "gateway/grpc_gateway/gateway_context.h"
#include "gateway/grpc_gateway/replay_service_impl.h"
#include "gateway/grpc_gateway/rpc_audit_log.h"
#include "gateway/grpc_gateway/trace_service_impl.h"
#include "replay_engine/replay_engine.h"
#include "scenario/scenario_loader.h"
#include "simulation/simulation_context.h"
#include "trace_store/trace_store.h"

static const boat::gateway::EffectiveConfig kTestEffectiveConfig;

namespace {

/* Keep each test process's temp traces separate; ctest -j may run several
   instances of this binary at once. */
std::string UniquePath(const std::string& name) {
  return (std::filesystem::temp_directory_path() /
          (name + "_" + std::to_string(::getpid()))).string();
}

/* Byte `i` of event `n`'s payload -- lets a reader verify every byte it got.
   ASCII only: TraceEvent.payload is a proto3 `string`, which rejects invalid
   UTF-8 on parse. */
std::uint8_t PayloadByte(std::uint64_t n, std::size_t i) {
  return static_cast<std::uint8_t>('a' + (n * 31 + i) % 26);
}

/* TraceRecordHeader-framed events, the format GetTrace/StreamTrace parse. */
std::vector<std::uint8_t> BuildHeaderTrace(std::uint64_t count, std::uint32_t payload_size) {
  std::vector<std::uint8_t> data;
  data.reserve(count * (sizeof(boat::store::TraceRecordHeader) + payload_size));
  for (std::uint64_t n = 0; n < count; ++n) {
    boat::store::TraceRecordHeader header{};
    header.magic = 0xB0A7B0A7;
    header.event_type = 100;
    header.tick = n;
    header.wall_time_ns = static_cast<std::int64_t>(n * 1'000'000ULL);
    header.payload_size = payload_size;
    const auto* hp = reinterpret_cast<const std::uint8_t*>(&header);
    data.insert(data.end(), hp, hp + sizeof(header));
    for (std::size_t i = 0; i < payload_size; ++i) data.push_back(PayloadByte(n, i));
  }
  return data;
}

bool EventMatches(const boat::v1::TraceEvent& event, std::uint64_t n, std::uint32_t payload_size) {
  if (event.tick() != n || event.payload().size() != payload_size) return false;
  for (std::size_t i = 0; i < payload_size; ++i) {
    if (static_cast<std::uint8_t>(event.payload()[i]) != PayloadByte(n, i)) return false;
  }
  return true;
}

struct TraceFixture {
  std::string event_db;
  std::string trace_db;
  std::vector<std::string> trace_files;

  boat::core::SimulationContext sim{777, 2};
  boat::core::SignalBus signal_bus;
  boat::core::ScenarioLoader scenario_loader;
  boat::store::SqliteEventStore event_store;
  boat::store::FlatFileTraceStore trace_store;
  boat::replay::ReplayController replay_controller{trace_store, event_store, sim.event_bus()};
  boat::hil::CanBusRegistry can_registry;
  boat::hil::EthernetBusRegistry eth_registry;
  boat::core::PluginManager plugin_manager;
  boat::gateway::FrameSink frame_sink{can_registry, eth_registry};
  boat::gateway::RpcAuditLog audit_log;
  boat::gateway::GatewayContext ctx{
      .sim = sim,
      .signal_bus = signal_bus,
      .scenario_loader = scenario_loader,
      .event_store = event_store,
      .trace_store = trace_store,
      .replay_controller = replay_controller,
      .can_bus_registry = can_registry,
      .ethernet_bus_registry = eth_registry,
      .plugin_manager = plugin_manager,
      .frame_sink = frame_sink,
      .audit_log = audit_log,
      .effective_config = kTestEffectiveConfig,
  };
  boat::gateway::TraceServiceImpl trace_service{ctx};
  boat::gateway::ReplayServiceImpl replay_service{ctx};
  std::unique_ptr<grpc::Server> server;
  int port = 0;

  explicit TraceFixture(const std::string& prefix)
      : event_db(UniquePath(prefix + "_events.db")),
        trace_db(UniquePath(prefix + "_traces.db")),
        event_store(event_db),
        trace_store(trace_db) {
    grpc::ServerBuilder builder;
    // A real TCP listener rather than only the in-process channel: StreamTrace
    // must be held back by HTTP/2 flow control so the handler is still reading
    // its mapping when the competing RPC lands.
    builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials(), &port);
    builder.RegisterService(&trace_service);
    builder.RegisterService(&replay_service);
    server = builder.BuildAndStart();
    REQUIRE(server != nullptr);
    REQUIRE(port != 0);
  }

  ~TraceFixture() {
    server->Shutdown();
    replay_controller.Stop();
    sim.scheduler().Stop();
    std::filesystem::remove(event_db);
    std::filesystem::remove(trace_db);
    for (const auto& f : trace_files) std::filesystem::remove(f);
  }

  void AddTrace(const std::string& id, const std::vector<std::uint8_t>& bytes) {
    const auto path = UniquePath(id + "_trace.bin");
    trace_files.push_back(path);
    boat::store::TraceRecord meta{};
    meta.id = id;
    meta.storage_path = path;
    meta.format = boat::store::TraceRecord::Format::BINARY;
    trace_store.WriteTrace(meta, std::span<const std::uint8_t>(bytes));
  }

  std::shared_ptr<grpc::Channel> Channel() const {
    return grpc::CreateChannel("127.0.0.1:" + std::to_string(port),
                               grpc::InsecureChannelCredentials());
  }

  std::string StartReplay(boat::v1::ReplayService::Stub& stub, const std::string& trace_id) {
    grpc::ClientContext cctx;
    boat::v1::StartReplayRequest req;
    req.set_trace_id(trace_id);
    boat::v1::ReplayControlResponse resp;
    REQUIRE(stub.StartReplay(&cctx, req, &resp).ok());
    return resp.replay_id();
  }

  static bool StopReplay(boat::v1::ReplayService::Stub& stub, const std::string& replay_id) {
    grpc::ClientContext cctx;
    boat::v1::StopReplayRequest req;
    req.set_replay_id(replay_id);
    boat::v1::ReplayControlResponse resp;
    return stub.StopReplay(&cctx, req, &resp).ok();
  }
};

}  // namespace

TEST_CASE("TraceService concurrent GetTrace calls all return the full trace",
          "[integration][trace][concurrency]") {
  constexpr std::uint64_t kEvents = 200;
  constexpr std::uint32_t kPayload = 1024;
  constexpr int kThreads = 8;
  constexpr int kCallsPerThread = 50;

  TraceFixture f("trace_svc_get");
  f.AddTrace("trace_svc_get", BuildHeaderTrace(kEvents, kPayload));
  auto channel = f.Channel();

  std::atomic<int> failures{0};
  std::vector<std::thread> threads;
  for (int t = 0; t < kThreads; ++t) {
    threads.emplace_back([&] {
      auto stub = boat::v1::TraceService::NewStub(channel);
      for (int c = 0; c < kCallsPerThread; ++c) {
        grpc::ClientContext cctx;
        boat::v1::GetTraceRequest req;
        req.set_trace_id("trace_svc_get");
        boat::v1::TraceResponse resp;
        const auto status = stub->GetTrace(&cctx, req, &resp);
        if (!status.ok() || resp.events_size() != static_cast<int>(kEvents)) {
          ++failures;
          continue;
        }
        for (int n = 0; n < resp.events_size(); ++n) {
          if (!EventMatches(resp.events(n), static_cast<std::uint64_t>(n), kPayload)) {
            ++failures;
            break;
          }
        }
      }
    });
  }
  for (auto& th : threads) th.join();
  REQUIRE(failures.load() == 0);
}

TEST_CASE("TraceService StopReplay racing StreamTrace cannot unmap the stream's trace",
          "[integration][trace][concurrency]") {
  // ~8 MiB: far beyond any HTTP/2 window, so the handler is parked in Write()
  // with most of the trace still unread when StopReplay arrives.
  constexpr std::uint64_t kEvents = 4096;
  constexpr std::uint32_t kPayload = 2048;

  TraceFixture f("trace_svc_stream");
  f.AddTrace("trace_svc_stream", BuildHeaderTrace(kEvents, kPayload));
  auto channel = f.Channel();
  auto replay_stub = boat::v1::ReplayService::NewStub(channel);
  auto trace_stub = boat::v1::TraceService::NewStub(channel);

  // Replay maps the trace first, then the stream maps it too: the order in
  // which the old per-id table let StopReplay unmap the stream's mapping.
  const std::string replay_id = f.StartReplay(*replay_stub, "trace_svc_stream");

  grpc::ClientContext stream_ctx;
  boat::v1::StreamTraceRequest stream_req;
  stream_req.set_trace_id("trace_svc_stream");
  auto reader = trace_stub->StreamTrace(&stream_ctx, stream_req);

  boat::v1::TraceEvent event;
  REQUIRE(reader->Read(&event));
  REQUIRE(EventMatches(event, 0, kPayload));

  REQUIRE(TraceFixture::StopReplay(*replay_stub, replay_id));

  std::uint64_t n = 1;
  bool all_match = true;
  while (reader->Read(&event)) {
    if (!EventMatches(event, n, kPayload)) all_match = false;
    ++n;
  }
  REQUIRE(reader->Finish().ok());
  REQUIRE(all_match);
  REQUIRE(n == kEvents);
}

TEST_CASE("TraceService StreamTrace readers survive repeated StartReplay/StopReplay",
          "[integration][trace][concurrency]") {
  constexpr std::uint64_t kEvents = 512;
  constexpr std::uint32_t kPayload = 1024;
  constexpr int kReaders = 4;
  constexpr int kCycles = 200;

  TraceFixture f("trace_svc_cycle");
  f.AddTrace("trace_svc_cycle", BuildHeaderTrace(kEvents, kPayload));
  auto channel = f.Channel();

  std::atomic<bool> done{false};
  std::atomic<int> failures{0};
  std::atomic<int> streams_completed{0};

  std::vector<std::thread> readers;
  for (int r = 0; r < kReaders; ++r) {
    readers.emplace_back([&] {
      auto stub = boat::v1::TraceService::NewStub(channel);
      while (!done.load()) {
        grpc::ClientContext cctx;
        boat::v1::StreamTraceRequest req;
        req.set_trace_id("trace_svc_cycle");
        auto reader = stub->StreamTrace(&cctx, req);
        boat::v1::TraceEvent event;
        std::uint64_t n = 0;
        while (reader->Read(&event)) {
          if (!EventMatches(event, n, kPayload)) ++failures;
          ++n;
        }
        if (!reader->Finish().ok() || n != kEvents) ++failures;
        ++streams_completed;
      }
    });
  }

  auto replay_stub = boat::v1::ReplayService::NewStub(channel);
  for (int c = 0; c < kCycles; ++c) {
    const std::string replay_id = f.StartReplay(*replay_stub, "trace_svc_cycle");
    REQUIRE(TraceFixture::StopReplay(*replay_stub, replay_id));
  }
  done.store(true);
  for (auto& th : readers) th.join();

  REQUIRE(failures.load() == 0);
  REQUIRE(streams_completed.load() >= kReaders);
}

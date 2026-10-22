// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <filesystem>
#include <memory>
#include <string>
#include <thread>

#include <grpcpp/grpcpp.h>
#include <unistd.h>

#include "boat/v1/frame.pb.h"
#include "boat/v1/replay.grpc.pb.h"
#include "can_bus_registry.h"
#include "ethernet_bus_registry.h"
#include "event_store/event_store.h"
#include "gateway/grpc_gateway/frame_sink.h"
#include "gateway/grpc_gateway/gateway_context.h"
#include "gateway/grpc_gateway/replay_service_impl.h"
#include "gateway/grpc_gateway/rpc_audit_log.h"
#include "core/plugin/plugin_manager.h"
#include "replay_engine/replay_engine.h"
#include "scenario/scenario_loader.h"
#include "simulation/simulation_context.h"
#include "trace_store/trace_store.h"

static const boat::gateway::EffectiveConfig kTestEffectiveConfig;


namespace {

std::string UniquePath(const std::string& name) {
  return (std::filesystem::temp_directory_path() /
          (name + "_" + std::to_string(::getpid()))).string();
}

std::vector<std::uint8_t> BuildTestTraceData() {
  std::vector<std::uint8_t> data;
  constexpr std::uint32_t magic = 0xB0A7B0A7;
  for (std::uint64_t tick = 100; tick <= 200; tick += 10) {
    std::uint32_t event_type = 100;
    std::uint32_t payload_size = 2;
    std::int64_t wall = static_cast<std::int64_t>(tick * 1'000'000ULL);

    auto append = [&](const void* src, size_t n) {
      const auto* p = static_cast<const std::uint8_t*>(src);
      data.insert(data.end(), p, p + n);
    };

    append(&magic, 4);
    append(&event_type, 4);
    append(&tick, 8);
    append(&wall, 8);
    append(&payload_size, 4);
    std::uint8_t val = static_cast<std::uint8_t>(tick & 0xFF);
    append(&val, 1);
    append(&val, 1);
  }
  return data;
}

/* Length-delimited boat.v1.Frame records, the format ReplayController reads.
   BuildTestTraceData above is the TraceStore header format used by the
   trace-service tests; feeding it to PumpDueRecords just records a parse
   error. */
std::vector<std::uint8_t> BuildReplayTraceData() {
  std::vector<std::uint8_t> data;
  for (std::uint64_t tick = 100; tick <= 200; tick += 10) {
    boat::v1::Frame pf;
    pf.set_bus_type(boat::v1::Frame::CAN);
    pf.set_timestamp_ns(tick * 1'000'000ULL);
    pf.set_payload(std::string(2, static_cast<char>(tick & 0xFF)));
    auto* cm = pf.mutable_can();
    cm->set_can_id(0x123);
    cm->set_dlc(2);
    cm->set_channel(1);

    const std::string raw = pf.SerializeAsString();
    const auto len = static_cast<std::uint32_t>(raw.size());
    const auto* len_bytes = reinterpret_cast<const std::uint8_t*>(&len);
    data.insert(data.end(), len_bytes, len_bytes + sizeof(len));
    data.insert(data.end(), raw.begin(), raw.end());
  }
  return data;
}

struct IntegrationFixture {
  std::string event_db;
  std::string trace_db;
  std::string trace_file;

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
      // These tests drive the service impls directly rather than through
      // main(), so there is no resolved gateway config; a default-constructed
      // one satisfies the reference without pretending to describe a run.
      .effective_config = kTestEffectiveConfig,
  };
  boat::gateway::ReplayServiceImpl replay_service{ctx};

  IntegrationFixture(const std::string& prefix,
                     const std::vector<std::uint8_t>& trace_data = BuildTestTraceData())
      : event_db(UniquePath(prefix + "_events.db")),
        trace_db(UniquePath(prefix + "_traces.db")),
        trace_file(UniquePath(prefix + "_trace.bin")),
        event_store(event_db),
        trace_store(trace_db) {
    std::filesystem::remove(event_db);
    std::filesystem::remove(trace_db);
    boat::store::TraceRecord meta;
    meta.id = prefix;
    meta.storage_path = trace_file;
    meta.format = boat::store::TraceRecord::Format::BINARY;
    trace_store.WriteTrace(meta, std::span<const std::uint8_t>(trace_data));
  }

  ~IntegrationFixture() {
    replay_controller.Stop();
    sim.scheduler().Stop();
    std::filesystem::remove(event_db);
    std::filesystem::remove(trace_db);
    std::filesystem::remove(trace_file);
  }
};

}  // namespace

TEST_CASE("ReplayService StartReplay accepts valid trace and rejects empty", "[integration][replay]") {
  IntegrationFixture f("r1");

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&f.replay_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});
  auto stub = boat::v1::ReplayService::NewStub(channel);

  grpc::ClientContext start_ctx;
  boat::v1::StartReplayRequest start_req;
  start_req.set_trace_id("r1");
  start_req.set_speed(boat::v1::REPLAY_SPEED_ACCELERATED);
  start_req.set_speed_multiplier(100.0);
  boat::v1::ReplayControlResponse start_resp;
  auto status = stub->StartReplay(&start_ctx, start_req, &start_resp);
  REQUIRE(status.ok());
  REQUIRE(start_resp.accepted());
  REQUIRE_FALSE(start_resp.replay_id().empty());

  grpc::ClientContext empty_ctx;
  boat::v1::StartReplayRequest empty_req;
  empty_req.set_trace_id("");
  boat::v1::ReplayControlResponse empty_resp;
  auto empty_status = stub->StartReplay(&empty_ctx, empty_req, &empty_resp);
  REQUIRE(empty_status.error_code() == grpc::StatusCode::INVALID_ARGUMENT);

  grpc::ClientContext stop_ctx;
  boat::v1::StopReplayRequest stop_req;
  stop_req.set_replay_id(start_resp.replay_id());
  boat::v1::ReplayControlResponse stop_resp;
  auto stop_status = stub->StopReplay(&stop_ctx, stop_req, &stop_resp);
  REQUIRE(stop_status.ok());

  server->Shutdown();
}

TEST_CASE("ReplayService streams trace data past the unary message limit",
          "[integration][replay]") {
  IntegrationFixture f("r_stream_import");

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&f.replay_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});
  auto stub = boat::v1::ReplayService::NewStub(channel);

  grpc::ClientContext context;
  boat::v1::ReplayControlResponse response;
  auto writer = stub->ImportTraceDataStream(&context, &response);

  boat::v1::ImportTraceDataChunk first;
  first.set_trace_id("big-stream");
  first.set_format("ASC");
  REQUIRE(writer->Write(first));

  constexpr std::size_t kChunkBytes = 1024 * 1024;
  const std::string chunk(kChunkBytes, 'x');
  for (int i = 0; i < 5; ++i) {
    boat::v1::ImportTraceDataChunk data;
    data.set_data(chunk);
    REQUIRE(writer->Write(data));
  }
  REQUIRE(writer->WritesDone());
  const auto status = writer->Finish();
  REQUIRE(status.ok());
  REQUIRE(response.accepted());
  REQUIRE(response.replay_id() == "big-stream");

  const auto mapped = f.trace_store.MapTrace("big-stream");
  REQUIRE(mapped != nullptr);
  REQUIRE(mapped->bytes.size() == 5 * kChunkBytes);

  server->Shutdown();
}

TEST_CASE("ReplayService StreamReplay exposes the authority tick", "[integration][replay]") {
  IntegrationFixture f("r_anchor", BuildReplayTraceData());

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&f.replay_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});
  auto stub = boat::v1::ReplayService::NewStub(channel);

  grpc::ClientContext start_ctx;
  boat::v1::StartReplayRequest start_req;
  start_req.set_trace_id("r_anchor");
  start_req.set_speed(boat::v1::REPLAY_SPEED_ACCELERATED);
  start_req.set_speed_multiplier(1000.0);
  boat::v1::ReplayControlResponse start_resp;
  REQUIRE(stub->StartReplay(&start_ctx, start_req, &start_resp).ok());

  // The controller has no thread in this fixture: drive the first replay phase
  // at a non-zero authority tick.
  f.replay_controller.PumpDueRecords(500);

  grpc::ClientContext stream_ctx;
  boat::v1::StreamReplayRequest stream_req;
  stream_req.set_replay_id(start_resp.replay_id());
  auto reader = stub->StreamReplay(&stream_ctx, stream_req);
  boat::v1::ReplayEvent event;
  REQUIRE(reader->Read(&event));
  REQUIRE(event.authority_tick() == 500);

  stream_ctx.TryCancel();
  (void)reader->Finish();
  server->Shutdown();
}

TEST_CASE("ReplayService controls return NOT_FOUND for unknown replay", "[integration][replay]") {
  IntegrationFixture f("r2");

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&f.replay_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});
  auto stub = boat::v1::ReplayService::NewStub(channel);

  grpc::ClientContext pause_ctx;
  boat::v1::PauseReplayRequest pause_req;
  pause_req.set_replay_id("unknown");
  boat::v1::ReplayControlResponse pause_resp;
  auto pause_status = stub->PauseReplay(&pause_ctx, pause_req, &pause_resp);
  REQUIRE(pause_status.error_code() == grpc::StatusCode::NOT_FOUND);

  grpc::ClientContext resume_ctx;
  boat::v1::ResumeReplayRequest resume_req;
  resume_req.set_replay_id("unknown");
  boat::v1::ReplayControlResponse resume_resp;
  auto resume_status = stub->ResumeReplay(&resume_ctx, resume_req, &resume_resp);
  REQUIRE(resume_status.error_code() == grpc::StatusCode::NOT_FOUND);

  grpc::ClientContext stop_ctx;
  boat::v1::StopReplayRequest stop_req;
  stop_req.set_replay_id("unknown");
  boat::v1::ReplayControlResponse stop_resp;
  auto stop_status = stub->StopReplay(&stop_ctx, stop_req, &stop_resp);
  REQUIRE(stop_status.error_code() == grpc::StatusCode::NOT_FOUND);

  grpc::ClientContext seek_ctx;
  boat::v1::SeekReplayRequest seek_req;
  seek_req.set_replay_id("unknown");
  seek_req.set_tick(100);
  boat::v1::ReplayControlResponse seek_resp;
  auto seek_status = stub->SeekReplay(&seek_ctx, seek_req, &seek_resp);
  REQUIRE(seek_status.error_code() == grpc::StatusCode::NOT_FOUND);

  grpc::ClientContext stream_ctx;
  boat::v1::StreamReplayRequest stream_req;
  stream_req.set_replay_id("unknown");
  auto reader = stub->StreamReplay(&stream_ctx, stream_req);
  auto stream_status = reader->Finish();
  REQUIRE(stream_status.error_code() == grpc::StatusCode::NOT_FOUND);

  server->Shutdown();
}

TEST_CASE("ReplayService Pause/Resume/Stop lifecycle via gRPC", "[integration][replay]") {
  IntegrationFixture f("r3");

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&f.replay_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});
  auto stub = boat::v1::ReplayService::NewStub(channel);

  grpc::ClientContext start_ctx;
  boat::v1::StartReplayRequest start_req;
  start_req.set_trace_id("r3");
  start_req.set_speed(boat::v1::REPLAY_SPEED_ACCELERATED);
  start_req.set_speed_multiplier(10.0);
  boat::v1::ReplayControlResponse start_resp;
  auto start_status = stub->StartReplay(&start_ctx, start_req, &start_resp);
  REQUIRE(start_status.ok());
  const std::string replay_id = start_resp.replay_id();
  REQUIRE_FALSE(replay_id.empty());

  std::this_thread::sleep_for(std::chrono::milliseconds(30));

  grpc::ClientContext pause_ctx;
  boat::v1::PauseReplayRequest pause_req;
  pause_req.set_replay_id(replay_id);
  boat::v1::ReplayControlResponse pause_resp;
  auto pause_status = stub->PauseReplay(&pause_ctx, pause_req, &pause_resp);
  REQUIRE(pause_status.ok());
  REQUIRE(pause_resp.accepted());

  grpc::ClientContext resume_ctx;
  boat::v1::ResumeReplayRequest resume_req;
  resume_req.set_replay_id(replay_id);
  boat::v1::ReplayControlResponse resume_resp;
  auto resume_status = stub->ResumeReplay(&resume_ctx, resume_req, &resume_resp);
  REQUIRE(resume_status.ok());
  REQUIRE(resume_resp.accepted());

  std::this_thread::sleep_for(std::chrono::milliseconds(30));

  grpc::ClientContext stop_ctx;
  boat::v1::StopReplayRequest stop_req;
  stop_req.set_replay_id(replay_id);
  boat::v1::ReplayControlResponse stop_resp;
  auto stop_status = stub->StopReplay(&stop_ctx, stop_req, &stop_resp);
  REQUIRE(stop_status.ok());
  REQUIRE(stop_resp.accepted());

  grpc::ClientContext stop2_ctx;
  boat::v1::ReplayControlResponse stop2_resp;
  auto stop2_status = stub->StopReplay(&stop2_ctx, stop_req, &stop2_resp);
  REQUIRE(stop2_status.error_code() == grpc::StatusCode::NOT_FOUND);

  server->Shutdown();
}

TEST_CASE("ReplayService StartReplay invalidates the previous replay_id", "[integration][replay]") {
  // Only one boat::replay::ReplayController is ever shared across all
  // replays, so starting a second replay silently stops whatever was
  // playing under the first replay_id. active_replays_ must reflect that --
  // otherwise a stale replay_id from a superseded StartReplay call would
  // still pass the PauseReplay/ResumeReplay/StopReplay/SeekReplay lookup
  // and end up controlling the *new* replay under the old name.
  IntegrationFixture f("r5a");
  boat::store::TraceRecord meta_b;
  meta_b.id = "r5b";
  meta_b.storage_path = f.trace_file + "_b";
  meta_b.format = boat::store::TraceRecord::Format::BINARY;
  f.trace_store.WriteTrace(meta_b, std::span<const std::uint8_t>(BuildTestTraceData()));

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&f.replay_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});
  auto stub = boat::v1::ReplayService::NewStub(channel);

  grpc::ClientContext start_a_ctx;
  boat::v1::StartReplayRequest start_a_req;
  start_a_req.set_trace_id("r5a");
  start_a_req.set_speed(boat::v1::REPLAY_SPEED_ACCELERATED);
  start_a_req.set_speed_multiplier(100.0);
  boat::v1::ReplayControlResponse start_a_resp;
  REQUIRE(stub->StartReplay(&start_a_ctx, start_a_req, &start_a_resp).ok());
  const std::string replay_id_a = start_a_resp.replay_id();
  REQUIRE_FALSE(replay_id_a.empty());

  grpc::ClientContext start_b_ctx;
  boat::v1::StartReplayRequest start_b_req;
  start_b_req.set_trace_id("r5b");
  start_b_req.set_speed(boat::v1::REPLAY_SPEED_ACCELERATED);
  start_b_req.set_speed_multiplier(100.0);
  boat::v1::ReplayControlResponse start_b_resp;
  REQUIRE(stub->StartReplay(&start_b_ctx, start_b_req, &start_b_resp).ok());
  const std::string replay_id_b = start_b_resp.replay_id();
  REQUIRE_FALSE(replay_id_b.empty());
  REQUIRE(replay_id_a != replay_id_b);

  // The old replay_id must no longer be controllable -- it was superseded,
  // not merely one of several concurrently active replays.
  grpc::ClientContext pause_a_ctx;
  boat::v1::PauseReplayRequest pause_a_req;
  pause_a_req.set_replay_id(replay_id_a);
  boat::v1::ReplayControlResponse pause_a_resp;
  auto pause_a_status = stub->PauseReplay(&pause_a_ctx, pause_a_req, &pause_a_resp);
  REQUIRE(pause_a_status.error_code() == grpc::StatusCode::NOT_FOUND);

  // The current replay_id must still work.
  grpc::ClientContext stop_b_ctx;
  boat::v1::StopReplayRequest stop_b_req;
  stop_b_req.set_replay_id(replay_id_b);
  boat::v1::ReplayControlResponse stop_b_resp;
  auto stop_b_status = stub->StopReplay(&stop_b_ctx, stop_b_req, &stop_b_resp);
  REQUIRE(stop_b_status.ok());
  REQUIRE(stop_b_resp.accepted());

  server->Shutdown();
}

TEST_CASE("ReplayService speed proto mapping works for all modes", "[integration][replay]") {
  IntegrationFixture f("r4");

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&f.replay_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});
  auto stub = boat::v1::ReplayService::NewStub(channel);

  std::vector<boat::v1::ReplaySpeed> test_speeds = {
      boat::v1::REPLAY_SPEED_REAL_TIME,
      boat::v1::REPLAY_SPEED_ACCELERATED,
      boat::v1::REPLAY_SPEED_STEP_BY_STEP,
  };

  for (auto speed : test_speeds) {
    grpc::ClientContext start_ctx;
    boat::v1::StartReplayRequest start_req;
    start_req.set_trace_id("r4");
    start_req.set_speed(speed);
    start_req.set_speed_multiplier(500.0);
    boat::v1::ReplayControlResponse start_resp;
    auto status = stub->StartReplay(&start_ctx, start_req, &start_resp);
    REQUIRE(status.ok());
    REQUIRE(start_resp.accepted());
    std::this_thread::sleep_for(std::chrono::milliseconds(30));

    grpc::ClientContext stop_ctx;
    boat::v1::StopReplayRequest stop_req;
    stop_req.set_replay_id(start_resp.replay_id());
    boat::v1::ReplayControlResponse stop_resp;
    stub->StopReplay(&stop_ctx, stop_req, &stop_resp);
  }

  server->Shutdown();
}

TEST_CASE("ReplayService concurrent StartReplay calls leave the registered replay_id controlling the playing trace",
          "[integration][replay][concurrency]") {
  // StartReplay used to call the controller outside replay_mutex_, so two
  // concurrent calls could interleave as A.Start, B.Start, B.register,
  // A.register: B playing, A's id registered -- and pausing "A" would then
  // pause B. Whatever the interleaving, the one id left registered must name
  // the trace actually playing.
  IntegrationFixture f("r6a");
  boat::store::TraceRecord meta_b;
  meta_b.id = "r6b";
  meta_b.storage_path = f.trace_file + "_b";
  meta_b.format = boat::store::TraceRecord::Format::BINARY;
  f.trace_store.WriteTrace(meta_b, std::span<const std::uint8_t>(BuildTestTraceData()));

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&f.replay_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});

  constexpr int kStartsPerThread = 100;
  std::atomic<int> failed_starts{0};
  auto starter = [&](const std::string& trace_id) {
    auto stub = boat::v1::ReplayService::NewStub(channel);
    for (int i = 0; i < kStartsPerThread; ++i) {
      grpc::ClientContext ctx;
      boat::v1::StartReplayRequest req;
      req.set_trace_id(trace_id);
      boat::v1::ReplayControlResponse resp;
      if (!stub->StartReplay(&ctx, req, &resp).ok()) ++failed_starts;
    }
  };
  std::thread a(starter, "r6a");
  std::thread b(starter, "r6b");
  a.join();
  b.join();
  REQUIRE(failed_starts.load() == 0);

  const std::string playing = f.replay_controller.GetActiveConfig().trace_id;
  REQUIRE((playing == "r6a" || playing == "r6b"));
  const std::string other = playing == "r6a" ? "r6b" : "r6a";

  auto stub = boat::v1::ReplayService::NewStub(channel);
  auto pause = [&](const std::string& trace_id) {
    grpc::ClientContext ctx;
    boat::v1::PauseReplayRequest req;
    req.set_replay_id("trace:" + trace_id);
    boat::v1::ReplayControlResponse resp;
    return stub->PauseReplay(&ctx, req, &resp).error_code();
  };
  REQUIRE(pause(playing) == grpc::StatusCode::OK);
  REQUIRE(pause(other) == grpc::StatusCode::NOT_FOUND);

  server->Shutdown();
}

TEST_CASE("ReplayService failed StartReplay leaves the previous replay and its id in place",
          "[integration][replay]") {
  IntegrationFixture f("r7");

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&f.replay_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto stub = boat::v1::ReplayService::NewStub(server->InProcessChannel({}));

  grpc::ClientContext start_ctx;
  boat::v1::StartReplayRequest start_req;
  start_req.set_trace_id("r7");
  boat::v1::ReplayControlResponse start_resp;
  REQUIRE(stub->StartReplay(&start_ctx, start_req, &start_resp).ok());

  grpc::ClientContext bad_ctx;
  boat::v1::StartReplayRequest bad_req;
  bad_req.set_trace_id("r7_does_not_exist");
  boat::v1::ReplayControlResponse bad_resp;
  REQUIRE(stub->StartReplay(&bad_ctx, bad_req, &bad_resp).error_code() ==
          grpc::StatusCode::NOT_FOUND);

  // The trace is mapped before anything is stopped, so the failed call
  // neither stopped the running replay nor unregistered its id.
  REQUIRE(f.replay_controller.IsRunning());
  REQUIRE(f.replay_controller.GetActiveConfig().trace_id == "r7");
  grpc::ClientContext stop_ctx;
  boat::v1::StopReplayRequest stop_req;
  stop_req.set_replay_id(start_resp.replay_id());
  boat::v1::ReplayControlResponse stop_resp;
  REQUIRE(stub->StopReplay(&stop_ctx, stop_req, &stop_resp).ok());

  server->Shutdown();
}

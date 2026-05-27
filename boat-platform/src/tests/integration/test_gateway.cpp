#include <catch2/catch_test_macros.hpp>

#include <filesystem>
#include <memory>
#include <span>
#include <vector>

#include <grpcpp/grpcpp.h>

#include "boat/v1/scenario.grpc.pb.h"
#include "boat/v1/signal.grpc.pb.h"
#include "boat/v1/simulation.grpc.pb.h"
#include "determinism/determinism_engine.h"
#include "event/event_bus.h"
#include "event_store/event_store.h"
#include "fault/fault_injector.h"
#include "gateway/grpc_gateway/gateway_context.h"
#include "gateway/grpc_gateway/scenario_service_impl.h"
#include "gateway/grpc_gateway/signal_service_impl.h"
#include "gateway/grpc_gateway/simulation_service_impl.h"
#include "can_bus_registry.h"
#include "ethernet_bus_registry.h"
#include "pdu/pdu_router.h"
#include "plugin/plugin_manager.h"
#include "gateway/grpc_gateway/rpc_audit_log.h"
#include "replay_engine/replay_engine.h"
#include "scenario/scenario_loader.h"
#include "scheduler/sim_clock.h"
#include "scheduler/tick_scheduler.h"
#include "signal/signal_router.h"
#include "state/sim_state_machine.h"
#include "trace_store/trace_store.h"

TEST_CASE("Gateway integration runs lifecycle and queries events via RPC", "[integration][gateway]") {
  std::filesystem::remove("boat_config.db");
  const auto temp_dir = std::filesystem::temp_directory_path();
  const auto event_db_path = temp_dir / "boat_integration_gateway_events.db";
  const auto trace_db_path = temp_dir / "boat_integration_gateway_traces.db";
  std::filesystem::remove(event_db_path);
  std::filesystem::remove(trace_db_path);

  boat::core::SimClock clock(777);
  boat::core::DeterminismEngine determinism(777);
  boat::core::EventBus event_bus;
  boat::core::SignalRouter signal_router;
  boat::core::FaultInjector fault_injector(determinism);
  boat::core::SimStateMachine state_machine;
  boat::core::PluginManager plugin_manager;
  boat::core::TickScheduler scheduler(clock, event_bus, determinism, 2);
  boat::core::ScenarioLoader scenario_loader;
  boat::store::SqliteEventStore event_store(event_db_path.string());
  boat::store::FlatFileTraceStore trace_store(trace_db_path.string());
  boat::replay::ReplayController replay_controller(trace_store, event_store, event_bus);
  boat::hil::CanBusRegistry can_registry;  // no interfaces opened in unit tests
  boat::hil::EthernetBusRegistry eth_registry;
  boat::hil::PduRouter pdu_router(can_registry, eth_registry);
  boat::gateway::RpcAuditLog audit_log;
  signal_router.SetFaultInjector(&fault_injector);

  boat::gateway::GatewayContext ctx{
      .sim_state_machine = state_machine,
      .sim_clock = clock,
      .tick_scheduler = scheduler,
      .event_bus = event_bus,
      .signal_router = signal_router,
      .plugin_manager = plugin_manager,
      .fault_injector = fault_injector,
      .scenario_loader = scenario_loader,
      .event_store = event_store,
      .trace_store = trace_store,
      .replay_controller = replay_controller,
      .can_bus_registry = can_registry,
      .ethernet_bus_registry = eth_registry,
      .pdu_router = pdu_router,
      .audit_log = audit_log,
  };

  boat::gateway::ScenarioServiceImpl scenario_service(ctx);
  boat::gateway::SimulationServiceImpl simulation_service(ctx);
  boat::gateway::SignalServiceImpl signal_service(ctx);

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&scenario_service);
  builder.RegisterService(&simulation_service);
  builder.RegisterService(&signal_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});

  auto scenario_stub = boat::v1::ScenarioService::NewStub(channel);
  auto simulation_stub = boat::v1::SimulationService::NewStub(channel);
  auto signal_stub = boat::v1::SignalService::NewStub(channel);

  boat::v1::CreateScenarioRequest create_scenario_request;
  auto* scenario = create_scenario_request.mutable_scenario();
  scenario->set_scenario_id("scn-int");
  scenario->set_name("Integration Scenario");
  scenario->set_content(
      R"({"id":"scn-int","name":"Integration Scenario","version":"1.0","duration_ticks":1000,"seed":777,"plugins":[],"signals":[],"faults":[]})");
  boat::v1::ScenarioResponse create_scenario_response;
  grpc::ClientContext create_scenario_ctx;
  REQUIRE(scenario_stub->CreateScenario(&create_scenario_ctx, create_scenario_request, &create_scenario_response).ok());

  boat::v1::CreateSimulationRequest create_sim_request;
  create_sim_request.set_scenario_id("scn-int");
  boat::v1::SimulationResponse create_sim_response;
  grpc::ClientContext create_sim_ctx;
  REQUIRE(simulation_stub->CreateSimulation(&create_sim_ctx, create_sim_request, &create_sim_response).ok());
  const std::string simulation_id = create_sim_response.simulation().simulation_id();
  REQUIRE_FALSE(simulation_id.empty());

  boat::v1::StartSimulationRequest start_request;
  start_request.set_simulation_id(simulation_id);
  boat::v1::SimulationResponse start_response;
  grpc::ClientContext start_ctx;
  REQUIRE(simulation_stub->StartSimulation(&start_ctx, start_request, &start_response).ok());

  boat::v1::PauseSimulationRequest pause_request;
  pause_request.set_simulation_id(simulation_id);
  boat::v1::SimulationResponse pause_response;
  grpc::ClientContext pause_ctx;
  REQUIRE(simulation_stub->PauseSimulation(&pause_ctx, pause_request, &pause_response).ok());

  boat::v1::StepSimulationRequest step_request;
  step_request.set_simulation_id(simulation_id);
  step_request.set_ticks(1000);
  boat::v1::SimulationResponse step_response;
  grpc::ClientContext step_ctx;
  REQUIRE(simulation_stub->StepSimulation(&step_ctx, step_request, &step_response).ok());
  REQUIRE(clock.tick() >= 1000);

  boat::v1::StopSimulationRequest stop_request;
  stop_request.set_simulation_id(simulation_id);
  boat::v1::SimulationResponse stop_response;
  grpc::ClientContext stop_ctx;
  REQUIRE(simulation_stub->StopSimulation(&stop_ctx, stop_request, &stop_response).ok());

  const std::vector<boat::store::EventRecord> expected = {
      {.id = "e1",
       .simulation_id = simulation_id,
       .tick = 10,
       .wall_time_ns = 100,
       .signal_id = "speed",
       .value_type = 1,
       .value_blob = {'1', '2'},
       .tags = "integration"},
      {.id = "e2",
       .simulation_id = simulation_id,
       .tick = 20,
       .wall_time_ns = 200,
       .signal_id = "speed",
       .value_type = 1,
       .value_blob = {'3', '4'},
       .tags = "integration"},
  };
  event_store.InsertBatch(std::span<const boat::store::EventRecord>(expected.data(), expected.size()));

  boat::v1::GetSignalHistoryRequest history_request;
  history_request.set_simulation_id(simulation_id);
  history_request.set_name("speed");
  boat::v1::SignalHistoryResponse history_response;
  grpc::ClientContext history_ctx;
  REQUIRE(signal_stub->GetSignalHistory(&history_ctx, history_request, &history_response).ok());
  REQUIRE(history_response.values_size() == static_cast<int>(expected.size()));
  REQUIRE(history_response.values(0).name() == "speed");
  REQUIRE(history_response.values(0).tick() == expected[0].tick);
  REQUIRE(history_response.values(1).tick() == expected[1].tick);

  boat::store::EventFilter filter;
  filter.simulation_id = simulation_id;
  filter.signal_id = "speed";
  const auto db_rows = event_store.Query(filter);
  REQUIRE(db_rows.size() == expected.size());

  server->Shutdown();
  scheduler.Stop();
  std::filesystem::remove("boat_config.db");
  std::filesystem::remove(event_db_path);
  std::filesystem::remove(trace_db_path);
}

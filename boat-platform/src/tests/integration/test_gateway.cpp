// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <limits>
#include <memory>
#include <mutex>
#include <span>
#include <string>
#include <thread>
#include <vector>

#include <grpcpp/grpcpp.h>

#include "boat/v1/bus.grpc.pb.h"
#include "boat/v1/can_tp.grpc.pb.h"
#include "boat/v1/debug.grpc.pb.h"
#include "boat/v1/fault.grpc.pb.h"
#include "boat/v1/frame.grpc.pb.h"
#include "boat/v1/metrics.grpc.pb.h"
#include "boat/v1/node_plugin.grpc.pb.h"
#include "boat/v1/pdu.grpc.pb.h"
#include "boat/v1/plugin.grpc.pb.h"
#include "boat/v1/scenario.grpc.pb.h"
#include "boat/v1/signal.grpc.pb.h"
#include "boat/v1/simulation.grpc.pb.h"
#include "event_store/event_store.h"
#include "gateway/grpc_gateway/bus_service_impl.h"
#include "gateway/grpc_gateway/can_tp_service_impl.h"
#include "gateway/grpc_gateway/debug_service_impl.h"
#include "gateway/grpc_gateway/fault_service_impl.h"
#include "gateway/grpc_gateway/frame_sink.h"
#include "gateway/grpc_gateway/frame_service_impl.h"
#include "gateway/grpc_gateway/gateway_context.h"
#include "gateway/grpc_gateway/metrics_service_impl.h"
#include "gateway/grpc_gateway/node_plugin_service_impl.h"
#include "gateway/grpc_gateway/pdu_service_impl.h"
#include "gateway/grpc_gateway/plugin_service_impl.h"
#include "gateway/grpc_gateway/scenario_service_impl.h"
#include "gateway/grpc_gateway/signal_service_impl.h"
#include "gateway/grpc_gateway/simulation_service_impl.h"
#include "can_bus_registry.h"
#include "ethernet_bus_registry.h"
#include "pdu/pdu_router.h"
#include "core/plugin/plugin_manager.h"
#include "gateway/grpc_gateway/rpc_audit_log.h"
#include "replay_engine/replay_engine.h"
#include "scenario/scenario_loader.h"
#include "simulation/simulation_context.h"
#include "trace_store/trace_store.h"

static const boat::gateway::EffectiveConfig kTestEffectiveConfig;

TEST_CASE("Gateway integration runs lifecycle and queries events via RPC", "[integration][gateway]") {
  std::filesystem::remove("boat_config.db");
  const auto temp_dir = std::filesystem::temp_directory_path();
  const auto event_db_path = temp_dir / "boat_integration_gateway_events.db";
  const auto trace_db_path = temp_dir / "boat_integration_gateway_traces.db";
  std::filesystem::remove(event_db_path);
  std::filesystem::remove(trace_db_path);

  boat::core::SimulationContext sim(777, 2);
  boat::core::SignalBus signal_bus;
  boat::core::ScenarioLoader scenario_loader;
  boat::store::SqliteEventStore event_store(event_db_path.string());
  boat::store::FlatFileTraceStore trace_store(trace_db_path.string());
  boat::replay::ReplayController replay_controller(trace_store, event_store, sim.event_bus());
  boat::hil::CanBusRegistry can_registry;  // no interfaces opened in unit tests
  boat::hil::EthernetBusRegistry eth_registry;
  boat::core::PluginManager plugin_manager;
  boat::gateway::FrameSink frame_sink(can_registry, eth_registry);
  boat::gateway::RpcAuditLog audit_log;
  sim.signal_router().SetFaultInjector(&sim.fault_injector());

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

  boat::gateway::PduServiceImpl pdu_service(ctx);
  boat::gateway::ScenarioServiceImpl scenario_service(ctx);
  boat::gateway::SimulationServiceImpl simulation_service(sim, can_registry, eth_registry);
  boat::gateway::SignalServiceImpl signal_service(ctx);
  boat::gateway::FaultServiceImpl fault_service(ctx);

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&pdu_service);
  builder.RegisterService(&scenario_service);
  builder.RegisterService(&simulation_service);
  builder.RegisterService(&signal_service);
  builder.RegisterService(&fault_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});

  auto scenario_stub = boat::v1::ScenarioService::NewStub(channel);
  auto simulation_stub = boat::v1::SimulationService::NewStub(channel);
  auto signal_stub = boat::v1::SignalService::NewStub(channel);
  auto fault_stub = boat::v1::FaultService::NewStub(channel);

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
  REQUIRE(sim.clock().tick() >= 1000);

  boat::v1::StopSimulationRequest stop_request;
  stop_request.set_simulation_id(simulation_id);
  boat::v1::SimulationResponse stop_response;
  grpc::ClientContext stop_ctx;
  REQUIRE(simulation_stub->StopSimulation(&stop_ctx, stop_request, &stop_response).ok());

  // ── FaultService integration RPC tests ──────────────────────────────────
  {
    boat::v1::InjectFaultRequest fault_request;
    fault_request.set_simulation_id(simulation_id);
    fault_request.set_target("speed");
    fault_request.set_type(boat::v1::FAULT_TYPE_SIGNAL_STUCK);
    fault_request.set_tick(10);
    fault_request.set_end_tick(20);
    fault_request.set_magnitude(42.5);
    boat::v1::InjectFaultResponse fault_response;
    grpc::ClientContext fault_ctx;
    REQUIRE(fault_stub->InjectFault(&fault_ctx, fault_request, &fault_response).ok());
    REQUIRE(fault_response.fault().target() == "speed");
    REQUIRE(fault_response.fault().type() == boat::v1::FAULT_TYPE_SIGNAL_STUCK);
    REQUIRE(fault_response.fault().tick() == 10);
    REQUIRE(fault_response.fault().end_tick() == 20);
    REQUIRE(fault_response.fault().magnitude() == 42.5);

    // The RPC must have scheduled the full spec, not just recorded the event.
    boat::core::SignalEvent routed_event{
        .signal_id = static_cast<std::uint64_t>(std::hash<std::string>{}("speed")),
        .tick = 15,
        .value = 1.0,
    };
    REQUIRE(sim.fault_injector().Apply(routed_event, 15) ==
            boat::core::FaultInjector::ApplyResult::PASS);
    REQUIRE(std::get<double>(routed_event.value) == 42.5);

    boat::v1::InjectFaultRequest invert_request;
    invert_request.set_simulation_id(simulation_id);
    invert_request.set_target("flag");
    invert_request.set_type(boat::v1::FAULT_TYPE_SIGNAL_INVERT);
    invert_request.set_tick(1);
    invert_request.set_end_tick(2);
    boat::v1::InjectFaultResponse invert_response;
    grpc::ClientContext invert_ctx;
    REQUIRE(fault_stub->InjectFault(&invert_ctx, invert_request, &invert_response).ok());

    boat::core::SignalEvent flag_event{
        .signal_id = static_cast<std::uint64_t>(std::hash<std::string>{}("flag")),
        .tick = 1,
        .value = true,
    };
    REQUIRE(sim.fault_injector().Apply(flag_event, 1) ==
            boat::core::FaultInjector::ApplyResult::PASS);
    REQUIRE(std::get<bool>(flag_event.value) == false);

    boat::v1::InjectFaultRequest one_tick_request;
    one_tick_request.set_simulation_id(simulation_id);
    one_tick_request.set_target("one-tick");
    one_tick_request.set_type(boat::v1::FAULT_TYPE_SIGNAL_STUCK);
    one_tick_request.set_tick(30);
    one_tick_request.set_magnitude(7.0);
    boat::v1::InjectFaultResponse one_tick_response;
    grpc::ClientContext one_tick_ctx;
    REQUIRE(fault_stub->InjectFault(&one_tick_ctx, one_tick_request, &one_tick_response).ok());
    REQUIRE(one_tick_response.fault().end_tick() == 30);

    boat::core::SignalEvent one_tick_event{
        .signal_id = static_cast<std::uint64_t>(std::hash<std::string>{}("one-tick")),
        .tick = 30,
        .value = 1.0,
    };
    REQUIRE(sim.fault_injector().Apply(one_tick_event, 30) ==
            boat::core::FaultInjector::ApplyResult::PASS);
    REQUIRE(std::get<double>(one_tick_event.value) == 7.0);

    boat::core::SignalEvent after_one_tick{
        .signal_id = static_cast<std::uint64_t>(std::hash<std::string>{}("one-tick")),
        .tick = 31,
        .value = 1.0,
    };
    REQUIRE(sim.fault_injector().Apply(after_one_tick, 31) ==
            boat::core::FaultInjector::ApplyResult::PASS);
    REQUIRE(std::get<double>(after_one_tick.value) == 1.0);

    boat::v1::InjectFaultRequest delay_request;
    delay_request.set_simulation_id(simulation_id);
    delay_request.set_target("delayed");
    delay_request.set_type(boat::v1::FAULT_TYPE_DELAY);
    delay_request.set_tick(5);
    delay_request.set_delay_ticks(3);
    boat::v1::InjectFaultResponse delay_response;
    grpc::ClientContext delay_ctx;
    REQUIRE(fault_stub->InjectFault(&delay_ctx, delay_request, &delay_response).ok());
    REQUIRE(delay_response.fault().delay_ticks() == 3);

    boat::core::SignalEvent delayed_event{
        .signal_id = static_cast<std::uint64_t>(std::hash<std::string>{}("delayed")),
        .tick = 5,
        .value = 1.0,
    };
    REQUIRE(sim.fault_injector().Apply(delayed_event, 5) ==
            boat::core::FaultInjector::ApplyResult::DEFER);
    REQUIRE(sim.fault_injector().FlushDelayed(7).empty());
    REQUIRE(sim.fault_injector().FlushDelayed(8).size() == 1);

    boat::v1::InjectFaultRequest invalid_range;
    invalid_range.set_simulation_id(simulation_id);
    invalid_range.set_target("speed");
    invalid_range.set_type(boat::v1::FAULT_TYPE_SIGNAL_STUCK);
    invalid_range.set_tick(40);
    invalid_range.set_end_tick(39);
    boat::v1::InjectFaultResponse invalid_response;
    grpc::ClientContext invalid_ctx;
    const auto invalid_status = fault_stub->InjectFault(&invalid_ctx, invalid_range, &invalid_response);
    REQUIRE(invalid_status.error_code() == grpc::StatusCode::INVALID_ARGUMENT);

    boat::v1::InjectFaultRequest unspecified;
    unspecified.set_target("speed");
    unspecified.set_tick(40);
    boat::v1::InjectFaultResponse unspecified_response;
    grpc::ClientContext unspecified_ctx;
    const auto unspecified_status = fault_stub->InjectFault(&unspecified_ctx, unspecified, &unspecified_response);
    REQUIRE(unspecified_status.error_code() == grpc::StatusCode::INVALID_ARGUMENT);

    boat::v1::InjectFaultRequest non_finite;
    non_finite.set_target("speed");
    non_finite.set_type(boat::v1::FAULT_TYPE_SIGNAL_STUCK);
    non_finite.set_tick(40);
    non_finite.set_magnitude(std::numeric_limits<double>::quiet_NaN());
    boat::v1::InjectFaultResponse non_finite_response;
    grpc::ClientContext non_finite_ctx;
    const auto non_finite_status = fault_stub->InjectFault(&non_finite_ctx, non_finite, &non_finite_response);
    REQUIRE(non_finite_status.error_code() == grpc::StatusCode::INVALID_ARGUMENT);

    boat::v1::InjectFaultRequest empty_target;
    empty_target.set_type(boat::v1::FAULT_TYPE_SIGNAL_STUCK);
    empty_target.set_tick(40);
    boat::v1::InjectFaultResponse empty_target_response;
    grpc::ClientContext empty_target_ctx;
    const auto empty_target_status = fault_stub->InjectFault(&empty_target_ctx, empty_target, &empty_target_response);
    REQUIRE(empty_target_status.error_code() == grpc::StatusCode::INVALID_ARGUMENT);

    boat::v1::InjectFaultRequest unknown_type;
    unknown_type.set_target("speed");
    unknown_type.set_type(static_cast<boat::v1::FaultType>(99));
    unknown_type.set_tick(40);
    boat::v1::InjectFaultResponse unknown_type_response;
    grpc::ClientContext unknown_type_ctx;
    const auto unknown_type_status = fault_stub->InjectFault(&unknown_type_ctx, unknown_type, &unknown_type_response);
    REQUIRE(unknown_type_status.error_code() == grpc::StatusCode::INVALID_ARGUMENT);

    boat::v1::InjectFaultRequest zero_delay;
    zero_delay.set_target("speed");
    zero_delay.set_type(boat::v1::FAULT_TYPE_DELAY);
    zero_delay.set_tick(40);
    boat::v1::InjectFaultResponse zero_delay_response;
    grpc::ClientContext zero_delay_ctx;
    const auto zero_delay_status = fault_stub->InjectFault(&zero_delay_ctx, zero_delay, &zero_delay_response);
    REQUIRE(zero_delay_status.error_code() == grpc::StatusCode::INVALID_ARGUMENT);

    boat::v1::InjectFaultRequest overflow_delay;
    overflow_delay.set_target("speed");
    overflow_delay.set_type(boat::v1::FAULT_TYPE_DELAY);
    overflow_delay.set_tick(std::numeric_limits<std::uint64_t>::max() - 1);
    overflow_delay.set_delay_ticks(2);
    boat::v1::InjectFaultResponse overflow_delay_response;
    grpc::ClientContext overflow_delay_ctx;
    const auto overflow_delay_status =
        fault_stub->InjectFault(&overflow_delay_ctx, overflow_delay, &overflow_delay_response);
    REQUIRE(overflow_delay_status.error_code() == grpc::StatusCode::INVALID_ARGUMENT);

    boat::v1::ListFaultsRequest list_request;
    list_request.set_simulation_id(simulation_id);
    boat::v1::ListFaultsResponse list_response;
    grpc::ClientContext list_ctx;
    REQUIRE(fault_stub->ListFaults(&list_ctx, list_request, &list_response).ok());
    REQUIRE(list_response.faults_size() == 4);
    REQUIRE(list_response.faults(0).fault_id() == fault_response.fault().fault_id());
    REQUIRE(list_response.faults(0).end_tick() == 20);
    REQUIRE(list_response.faults(1).type() == boat::v1::FAULT_TYPE_SIGNAL_INVERT);
    REQUIRE(list_response.faults(2).end_tick() == 30);
    REQUIRE(list_response.faults(3).delay_ticks() == 3);

    boat::v1::ListFaultsRequest page_request;
    page_request.set_simulation_id(simulation_id);
    page_request.mutable_page()->set_page_size(2);
    boat::v1::ListFaultsResponse page_response;
    grpc::ClientContext page_ctx;
    REQUIRE(fault_stub->ListFaults(&page_ctx, page_request, &page_response).ok());
    REQUIRE(page_response.faults_size() == 2);
    REQUIRE(page_response.page().next_page_token() == "2");

    boat::v1::ListFaultsRequest next_page_request;
    next_page_request.set_simulation_id(simulation_id);
    next_page_request.mutable_page()->set_page_size(2);
    next_page_request.mutable_page()->set_page_token(page_response.page().next_page_token());
    boat::v1::ListFaultsResponse next_page_response;
    grpc::ClientContext next_page_ctx;
    REQUIRE(fault_stub->ListFaults(&next_page_ctx, next_page_request, &next_page_response).ok());
    REQUIRE(next_page_response.faults_size() == 2);
    REQUIRE(next_page_response.page().next_page_token().empty());

    boat::v1::ListFaultsRequest bad_page_request;
    bad_page_request.mutable_page()->set_page_token("not-a-number");
    boat::v1::ListFaultsResponse bad_page_response;
    grpc::ClientContext bad_page_ctx;
    const auto bad_page_status = fault_stub->ListFaults(&bad_page_ctx, bad_page_request, &bad_page_response);
    REQUIRE(bad_page_status.error_code() == grpc::StatusCode::INVALID_ARGUMENT);
  }

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

  // ── PDU Group integration RPC tests ──────────────────────────────────────
  {
    // Create and register a PduRouter for the test
    boat::hil::PduRouter test_pdu_router(can_registry, eth_registry);
    plugin_manager.RegisterService("pdu_router", &test_pdu_router);

    auto pdu_stub = boat::v1::PduService::NewStub(channel);

    // Configure a route (no physical interface needed for config)
    boat::v1::ConfigureRouteRequest route_req;
    route_req.mutable_route()->set_pdu_id(0x100);
    route_req.mutable_route()->set_transport(boat::v1::PDU_TRANSPORT_CAN);
    route_req.mutable_route()->set_iface("vcan0");
    boat::v1::ConfigureRouteResponse route_resp;
    grpc::ClientContext route_ctx;
    REQUIRE(pdu_stub->ConfigureRoute(&route_ctx, route_req, &route_resp).ok());
    REQUIRE(route_resp.ok());

    // Configure a group (disabled)
    boat::v1::ConfigureGroupRequest group_req;
    group_req.mutable_group()->set_group_id(1);
    group_req.mutable_group()->set_name("Safety");
    group_req.mutable_group()->add_pdu_ids(0x100);
    group_req.mutable_group()->set_enabled(false);
    boat::v1::ConfigureGroupResponse group_resp;
    grpc::ClientContext group_ctx;
    REQUIRE(pdu_stub->ConfigureGroup(&group_ctx, group_req, &group_resp).ok());
    REQUIRE(group_resp.ok());

    // Send PDU while group disabled → PduRouter returns false, gRPC returns NOT_FOUND
    boat::v1::SendPduRequest send_req;
    send_req.mutable_pdu()->set_pdu_id(0x100);
    send_req.mutable_pdu()->set_payload(std::string({0x01}));
    boat::v1::SendPduResponse send_resp;
    grpc::ClientContext send_ctx;
    auto send_status = pdu_stub->SendPdu(&send_ctx, send_req, &send_resp);
    REQUIRE(send_status.error_code() == grpc::StatusCode::NOT_FOUND);

    // Enable group via RPC
    boat::v1::EnableGroupRequest enable_req;
    enable_req.set_group_id(1);
    boat::v1::EnableGroupResponse enable_resp;
    grpc::ClientContext enable_ctx;
    REQUIRE(pdu_stub->EnableGroup(&enable_ctx, enable_req, &enable_resp).ok());
    REQUIRE(enable_resp.ok());

    // Verify group is now enabled via IsGroupEnabled (list groups)
    // Disable group via RPC
    boat::v1::DisableGroupRequest disable_req;
    disable_req.set_group_id(1);
    boat::v1::DisableGroupResponse disable_resp;
    grpc::ClientContext disable_ctx;
    REQUIRE(pdu_stub->DisableGroup(&disable_ctx, disable_req, &disable_resp).ok());
    REQUIRE(disable_resp.ok());

    // List groups → verify disabled state
    boat::v1::ListGroupsRequest list_groups_req;
    boat::v1::ListGroupsResponse list_groups_resp;
    grpc::ClientContext list_grp_ctx;
    REQUIRE(pdu_stub->ListGroups(&list_grp_ctx, list_groups_req, &list_groups_resp).ok());
    REQUIRE(list_groups_resp.groups_size() == 1);
    REQUIRE(list_groups_resp.groups(0).group_id() == 1);
    REQUIRE(list_groups_resp.groups(0).name() == "Safety");
    REQUIRE_FALSE(list_groups_resp.groups(0).enabled());

    // List routes → verify configured route
    boat::v1::ListRoutesRequest list_route_req;
    boat::v1::ListRoutesResponse list_route_resp;
    grpc::ClientContext list_route_ctx;
    REQUIRE(pdu_stub->ListRoutes(&list_route_ctx, list_route_req, &list_route_resp).ok());
    bool route_found = false;
    for (const auto& r : list_route_resp.routes()) {
      if (r.pdu_id() == 0x100) route_found = true;
    }
    REQUIRE(route_found);

    // Configure route with schedule
    boat::v1::ConfigureRouteRequest sched_req;
    sched_req.mutable_route()->set_pdu_id(0x200);
    sched_req.mutable_route()->set_transport(boat::v1::PDU_TRANSPORT_CAN);
    sched_req.mutable_route()->set_iface("vcan0");
    sched_req.mutable_route()->mutable_schedule()->set_send_type(boat::v1::SEND_TYPE_CYCLIC);
    sched_req.mutable_route()->mutable_schedule()->set_cycle_ms(100);
    sched_req.mutable_route()->mutable_schedule()->set_fast_ms(10);
    sched_req.mutable_route()->mutable_schedule()->set_repetitions(3);
    boat::v1::ConfigureRouteResponse sched_resp;
    grpc::ClientContext sched_ctx;
    REQUIRE(pdu_stub->ConfigureRoute(&sched_ctx, sched_req, &sched_resp).ok());

    // List routes and verify schedule is preserved through proto round-trip
    boat::v1::ListRoutesRequest list2_req;
    boat::v1::ListRoutesResponse list2_resp;
    grpc::ClientContext list2_ctx;
    REQUIRE(pdu_stub->ListRoutes(&list2_ctx, list2_req, &list2_resp).ok());
    bool sched_ok = false;
    for (const auto& r : list2_resp.routes()) {
      if (r.pdu_id() == 0x200 && r.has_schedule() &&
          r.schedule().send_type() == boat::v1::SEND_TYPE_CYCLIC &&
          r.schedule().cycle_ms() == 100 &&
          r.schedule().fast_ms() == 10 &&
          r.schedule().repetitions() == 3) {
        sched_ok = true;
      }
    }
    REQUIRE(sched_ok);

    // Re-enable group before PduRouter shutdown (cleanup)
    boat::v1::EnableGroupRequest enable_cleanup;
    enable_cleanup.set_group_id(1);
    boat::v1::EnableGroupResponse enable_cleanup_resp;
    grpc::ClientContext enable_cleanup_ctx;
    pdu_stub->EnableGroup(&enable_cleanup_ctx, enable_cleanup, &enable_cleanup_resp);
  }

  server->Shutdown();
  sim.scheduler().Stop();
  std::filesystem::remove("boat_config.db");
  std::filesystem::remove(event_db_path);
  std::filesystem::remove(trace_db_path);
}

#ifdef PDU_ROUTER_SO
TEST_CASE("PluginService and NodePluginService see disjoint PluginManager scopes",
          "[integration][gateway][plugin]") {
  // Regression test for the gap that motivated NodePluginService: a plugin
  // loaded into the always-on "node" manager (ctx.plugin_manager, what
  // BOAT_NODE_PLUGINS populates in the real gateway) must be invisible to
  // PluginService (which only ever talks to the simulation-scoped manager,
  // ctx.sim.plugin_manager()) and only visible through NodePluginService.
  std::filesystem::remove("boat_config.db");
  const auto temp_dir = std::filesystem::temp_directory_path();
  const auto event_db_path = temp_dir / "boat_integration_gateway_scope_events.db";
  const auto trace_db_path = temp_dir / "boat_integration_gateway_scope_traces.db";
  std::filesystem::remove(event_db_path);
  std::filesystem::remove(trace_db_path);

  boat::core::SimulationContext sim(778, 2);
  boat::core::SignalBus signal_bus;
  boat::core::ScenarioLoader scenario_loader;
  boat::store::SqliteEventStore event_store(event_db_path.string());
  boat::store::FlatFileTraceStore trace_store(trace_db_path.string());
  boat::replay::ReplayController replay_controller(trace_store, event_store, sim.event_bus());
  boat::hil::CanBusRegistry can_registry;
  boat::hil::EthernetBusRegistry eth_registry;
  boat::core::PluginManager node_manager;  // stands in for main.cpp's node_manager
  boat::gateway::FrameSink frame_sink(can_registry, eth_registry);
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
      .plugin_manager = node_manager,
      .frame_sink = frame_sink,
      .audit_log = audit_log,
      // These tests drive the service impls directly rather than through
      // main(), so there is no resolved gateway config; a default-constructed
      // one satisfies the reference without pretending to describe a run.
      .effective_config = kTestEffectiveConfig,
  };

  boat::gateway::PluginServiceImpl plugin_service(ctx);
  boat::gateway::NodePluginServiceImpl node_plugin_service(ctx);

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&plugin_service);
  builder.RegisterService(&node_plugin_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});

  auto plugin_stub = boat::v1::PluginService::NewStub(channel);
  auto node_plugin_stub = boat::v1::NodePluginService::NewStub(channel);

  // Load directly into the node-scoped manager (mirrors BOAT_NODE_PLUGINS),
  // not via RegisterPlugin RPC (which only ever targets the sim-scoped one).
  const auto handle = node_manager.Load(PDU_ROUTER_SO, R"({"iface":"vcan0"})");

  // Visible via NodePluginService...
  boat::v1::ListNodePluginsResponse node_list_resp;
  grpc::ClientContext node_list_ctx;
  REQUIRE(node_plugin_stub->ListNodePlugins(&node_list_ctx, boat::v1::ListNodePluginsRequest(), &node_list_resp).ok());
  bool found_in_node_scope = false;
  for (const auto& p : node_list_resp.plugins()) {
    if (p.plugin_id() == handle.name) {
      found_in_node_scope = true;
      REQUIRE(p.config_json() == R"({"iface":"vcan0"})");
    }
  }
  REQUIRE(found_in_node_scope);

  boat::v1::GetNodePluginInfoRequest node_info_req;
  node_info_req.set_plugin_id(handle.name);
  boat::v1::PluginResponse node_info_resp;
  grpc::ClientContext node_info_ctx;
  REQUIRE(node_plugin_stub->GetNodePluginInfo(&node_info_ctx, node_info_req, &node_info_resp).ok());
  REQUIRE(node_info_resp.plugin().loaded());

  // ...but invisible via the sim-scoped PluginService.
  boat::v1::ListPluginsResponse sim_list_resp;
  grpc::ClientContext sim_list_ctx;
  REQUIRE(plugin_stub->ListPlugins(&sim_list_ctx, boat::v1::ListPluginsRequest(), &sim_list_resp).ok());
  for (const auto& p : sim_list_resp.plugins()) {
    REQUIRE(p.plugin_id() != handle.name);
  }

  boat::v1::GetPluginInfoRequest sim_info_req;
  sim_info_req.set_plugin_id(handle.name);
  boat::v1::PluginResponse sim_info_resp;
  grpc::ClientContext sim_info_ctx;
  const auto sim_info_status = plugin_stub->GetPluginInfo(&sim_info_ctx, sim_info_req, &sim_info_resp);
  REQUIRE_FALSE(sim_info_status.ok());
  REQUIRE(sim_info_status.error_code() == grpc::StatusCode::NOT_FOUND);

  // Unload requires explicit confirm=true.
  boat::v1::UnloadNodePluginRequest unload_req;
  unload_req.set_plugin_id(handle.name);
  boat::v1::UnloadNodePluginResponse unload_resp;
  grpc::ClientContext unload_ctx;
  const auto unload_status = node_plugin_stub->UnloadNodePlugin(&unload_ctx, unload_req, &unload_resp);
  REQUIRE_FALSE(unload_status.ok());
  REQUIRE(unload_status.error_code() == grpc::StatusCode::FAILED_PRECONDITION);

  unload_req.set_confirm(true);
  boat::v1::UnloadNodePluginResponse unload_resp2;
  grpc::ClientContext unload_ctx2;
  REQUIRE(node_plugin_stub->UnloadNodePlugin(&unload_ctx2, unload_req, &unload_resp2).ok());
  REQUIRE(unload_resp2.unloaded());

  boat::v1::ListNodePluginsResponse node_list_resp2;
  grpc::ClientContext node_list_ctx2;
  REQUIRE(node_plugin_stub->ListNodePlugins(&node_list_ctx2, boat::v1::ListNodePluginsRequest(), &node_list_resp2).ok());
  for (const auto& p : node_list_resp2.plugins()) {
    REQUIRE(p.plugin_id() != handle.name);
  }

  server->Shutdown();
  std::filesystem::remove("boat_config.db");
  std::filesystem::remove(event_db_path);
  std::filesystem::remove(trace_db_path);
}
#endif

#ifdef CAN_TP_SO
TEST_CASE("CanTpService.ListSessions aggregates across every loaded instance",
          "[integration][gateway][can_tp]") {
  std::filesystem::remove("boat_config.db");
  const auto temp_dir = std::filesystem::temp_directory_path();
  const auto event_db_path = temp_dir / "boat_integration_gateway_cantp_events.db";
  const auto trace_db_path = temp_dir / "boat_integration_gateway_cantp_traces.db";
  std::filesystem::remove(event_db_path);
  std::filesystem::remove(trace_db_path);

  boat::core::SimulationContext sim(779, 2);
  boat::core::SignalBus signal_bus;
  boat::core::ScenarioLoader scenario_loader;
  boat::store::SqliteEventStore event_store(event_db_path.string());
  boat::store::FlatFileTraceStore trace_store(trace_db_path.string());
  boat::replay::ReplayController replay_controller(trace_store, event_store, sim.event_bus());
  boat::hil::CanBusRegistry can_registry;
  boat::hil::EthernetBusRegistry eth_registry;
  boat::core::PluginManager node_manager;
  boat::gateway::FrameSink frame_sink(can_registry, eth_registry);
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
      .plugin_manager = node_manager,
      .frame_sink = frame_sink,
      .audit_log = audit_log,
      // These tests drive the service impls directly rather than through
      // main(), so there is no resolved gateway config; a default-constructed
      // one satisfies the reference without pretending to describe a run.
      .effective_config = kTestEffectiveConfig,
  };

  boat::gateway::CanTpServiceImpl can_tp_service(ctx);

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&can_tp_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});
  auto can_tp_stub = boat::v1::CanTpService::NewStub(channel);

  // Two instances, mirroring BOAT_NODE_PLUGINS with two ?{"iface":...} entries.
  node_manager.Load(CAN_TP_SO, R"({"iface":"vcan0"})");
  node_manager.Load(CAN_TP_SO, R"({"iface":"vcan1"})");

  auto configure_on = [&](const std::string& iface, uint32_t nsdu_id) {
    boat::v1::ConfigureRequest req;
    req.set_iface(iface);
    req.mutable_config()->set_nsdu_id(nsdu_id);
    req.mutable_config()->set_source_addr(nsdu_id);
    req.mutable_config()->set_target_addr(nsdu_id + 0x100);
    req.mutable_config()->set_can_dlc(8);
    boat::v1::ConfigureResponse resp;
    grpc::ClientContext client_ctx;
    REQUIRE(can_tp_stub->Configure(&client_ctx, req, &resp).ok());
    REQUIRE(resp.ok());
  };
  configure_on("vcan0", 0x100);
  configure_on("vcan1", 0x300);

  // No iface filter -> sessions from both instances, each correctly tagged.
  boat::v1::ListSessionsRequest all_req;
  boat::v1::ListSessionsResponse all_resp;
  grpc::ClientContext all_ctx;
  REQUIRE(can_tp_stub->ListSessions(&all_ctx, all_req, &all_resp).ok());
  REQUIRE(all_resp.sessions_size() == 2);
  bool found_vcan0 = false, found_vcan1 = false;
  for (const auto& s : all_resp.sessions()) {
    if (s.iface() == "vcan0") { REQUIRE(s.nsdu_id() == 0x100); found_vcan0 = true; }
    if (s.iface() == "vcan1") { REQUIRE(s.nsdu_id() == 0x300); found_vcan1 = true; }
  }
  REQUIRE(found_vcan0);
  REQUIRE(found_vcan1);

  // iface filter -> only that instance's sessions.
  boat::v1::ListSessionsRequest scoped_req;
  scoped_req.set_iface("vcan0");
  boat::v1::ListSessionsResponse scoped_resp;
  grpc::ClientContext scoped_ctx;
  REQUIRE(can_tp_stub->ListSessions(&scoped_ctx, scoped_req, &scoped_resp).ok());
  REQUIRE(scoped_resp.sessions_size() == 1);
  REQUIRE(scoped_resp.sessions(0).iface() == "vcan0");
  REQUIRE(scoped_resp.sessions(0).nsdu_id() == 0x100);

  server->Shutdown();
  std::filesystem::remove("boat_config.db");
  std::filesystem::remove(event_db_path);
  std::filesystem::remove(trace_db_path);
}

TEST_CASE("CanTpService RemoveSession and Subscribe round trip",
          "[integration][gateway][can_tp]") {
  std::filesystem::remove("boat_config.db");
  const auto temp_dir = std::filesystem::temp_directory_path();
  const auto event_db_path = temp_dir / "boat_integration_gateway_cantp2_events.db";
  const auto trace_db_path = temp_dir / "boat_integration_gateway_cantp2_traces.db";
  std::filesystem::remove(event_db_path);
  std::filesystem::remove(trace_db_path);

  boat::core::SimulationContext sim(780, 2);
  boat::core::SignalBus signal_bus;
  boat::core::ScenarioLoader scenario_loader;
  boat::store::SqliteEventStore event_store(event_db_path.string());
  boat::store::FlatFileTraceStore trace_store(trace_db_path.string());
  boat::replay::ReplayController replay_controller(trace_store, event_store, sim.event_bus());
  boat::hil::CanBusRegistry can_registry;
  boat::hil::EthernetBusRegistry eth_registry;
  boat::core::PluginManager node_manager;
  boat::gateway::FrameSink frame_sink(can_registry, eth_registry);
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
      .plugin_manager = node_manager,
      .frame_sink = frame_sink,
      .audit_log = audit_log,
      // These tests drive the service impls directly rather than through
      // main(), so there is no resolved gateway config; a default-constructed
      // one satisfies the reference without pretending to describe a run.
      .effective_config = kTestEffectiveConfig,
  };

  boat::gateway::CanTpServiceImpl can_tp_service(ctx);

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&can_tp_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});
  auto can_tp_stub = boat::v1::CanTpService::NewStub(channel);

  // Send() bails out early if no frame publisher is wired (see
  // can_tp_send's frame_publish_fn nullptr guard) -- a no-op sink is enough
  // here since this test only cares about the RPC-level Send()/Subscribe()
  // contract, not what actually reaches a bus.
  node_manager.SetFramePublisher([](const BoatFrame&) {});
  node_manager.Load(CAN_TP_SO, R"({"iface":"vcan0"})");

  auto configure = [&](uint32_t nsdu_id, uint32_t source_addr, uint32_t target_addr) {
    boat::v1::ConfigureRequest req;
    req.mutable_config()->set_nsdu_id(nsdu_id);
    req.mutable_config()->set_source_addr(source_addr);
    req.mutable_config()->set_target_addr(target_addr);
    req.mutable_config()->set_can_dlc(8);
    boat::v1::ConfigureResponse resp;
    grpc::ClientContext client_ctx;
    REQUIRE(can_tp_stub->Configure(&client_ctx, req, &resp).ok());
    REQUIRE(resp.ok());
  };

  SECTION("Subscribe streams a decoded Single Frame payload") {
    configure(0x9, 0x700, 0x800);

    std::mutex received_mutex;
    std::vector<boat::v1::CanTpRxEvent> received;
    grpc::ClientContext sub_ctx;
    boat::v1::SubscribeRequest sub_req;  // nsdu_ids empty -> all sessions
    auto reader = can_tp_stub->Subscribe(&sub_ctx, sub_req);

    std::thread reader_thread([&] {
      boat::v1::CanTpRxEvent ev;
      while (reader->Read(&ev)) {
        std::lock_guard<std::mutex> lock(received_mutex);
        received.push_back(ev);
      }
    });
    // Cancels the stream and joins the reader thread no matter how this
    // SECTION exits, including a REQUIRE failure -- Catch2 unwinds via an
    // exception on failure, and std::thread's destructor calls
    // std::terminate() if it's still joinable, which would otherwise mask
    // the real assertion failure behind a SIGABRT.
    struct ReaderGuard {
      grpc::ClientContext& ctx;
      std::thread& t;
      grpc::ClientReader<boat::v1::CanTpRxEvent>& r;
      ~ReaderGuard() {
        ctx.TryCancel();
        if (t.joinable()) t.join();
        r.Finish();
      }
    } reader_guard{sub_ctx, reader_thread, *reader};

    // Let the subscription establish server-side before injecting a frame --
    // the server's poll loop wakes every 50ms (see CanTpServiceImpl::Subscribe).
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Subscribe reports *decoded RX* payloads (what tp_on_frame reassembles
    // from a peer), not our own outbound Send() traffic -- CanTpService.Send
    // only ever transmits, it never loops back into on_frame (self-sent
    // frames are filtered by design; see BOAT_CAN_FLAG_SELF_SENT). So the
    // trigger here is a synthetic *inbound* Single Frame from the peer,
    // matching the connection's target_addr (0x800) the same way
    // find_by_target() does -- mirrors this file's other DispatchFrame-based
    // tests and can_tp_plugin.cpp's own tp_on_frame doc comments.
    const std::vector<uint8_t> sf_payload = {0x03, 0xAA, 0xBB, 0xCC};  // PCI 0x03 = SF len 3
    BoatFrame sf_frame{};
    sf_frame.bus_type = BOAT_BUS_CAN;
    sf_frame.iface = "vcan0";
    sf_frame.meta.can.can_id = 0x800;  // target_addr
    sf_frame.meta.can.dlc = static_cast<uint8_t>(sf_payload.size());
    sf_frame.meta.can.flags = 0;
    sf_frame.payload = const_cast<uint8_t*>(sf_payload.data());
    sf_frame.payload_len = sf_payload.size();
    node_manager.DispatchFrame(sf_frame);

    // Poll for the event to arrive rather than a fixed sleep.
    bool got_event = false;
    for (int i = 0; i < 50 && !got_event; ++i) {
      std::this_thread::sleep_for(std::chrono::milliseconds(20));
      std::lock_guard<std::mutex> lock(received_mutex);
      got_event = !received.empty();
    }

    REQUIRE(got_event);
    std::lock_guard<std::mutex> lock(received_mutex);
    REQUIRE(received.size() >= 1);
    REQUIRE(received[0].nsdu_id() == 0x9);
    REQUIRE(received[0].iface() == "vcan0");
    REQUIRE(received[0].data() == std::string("\xAA\xBB\xCC", 3));
  }

  SECTION("RemoveSession deletes a connection; Send then fails") {
    configure(0xA, 0x701, 0x801);

    boat::v1::RemoveSessionRequest remove_req;
    remove_req.set_nsdu_id(0xA);
    boat::v1::RemoveSessionResponse remove_resp;
    grpc::ClientContext remove_ctx;
    REQUIRE(can_tp_stub->RemoveSession(&remove_ctx, remove_req, &remove_resp).ok());
    REQUIRE(remove_resp.ok());

    boat::v1::SendRequest send_req;
    send_req.set_nsdu_id(0xA);
    send_req.set_data("\x01", 1);
    boat::v1::SendResponse send_resp;
    grpc::ClientContext send_ctx;
    auto status = can_tp_stub->Send(&send_ctx, send_req, &send_resp);
    REQUIRE_FALSE(status.ok());
    REQUIRE(status.error_code() == grpc::StatusCode::FAILED_PRECONDITION);

    boat::v1::ListSessionsRequest list_req;
    boat::v1::ListSessionsResponse list_resp;
    grpc::ClientContext list_ctx;
    REQUIRE(can_tp_stub->ListSessions(&list_ctx, list_req, &list_resp).ok());
    REQUIRE(list_resp.sessions_size() == 0);
  }

  SECTION("RemoveSession on an unconfigured nsdu_id fails with NOT_FOUND") {
    boat::v1::RemoveSessionRequest remove_req;
    remove_req.set_nsdu_id(0xBEEF);
    boat::v1::RemoveSessionResponse remove_resp;
    grpc::ClientContext remove_ctx;
    auto status = can_tp_stub->RemoveSession(&remove_ctx, remove_req, &remove_resp);
    REQUIRE_FALSE(status.ok());
    REQUIRE(status.error_code() == grpc::StatusCode::NOT_FOUND);
  }

  server->Shutdown();
  std::filesystem::remove("boat_config.db");
  std::filesystem::remove(event_db_path);
  std::filesystem::remove(trace_db_path);
}
#endif

namespace {

/* No wire: ReadFrame parks like a quiet bus so the bridge's RX thread does not
   spin, and every SendFrame still goes through the registry's dispatch path. */
class LoopbackCanDriver : public boat::hil::IHalDriver {
 public:
  bool Open() override { return true; }
  void Close() override {}
  bool ReadFrame(boat::hil::CanFrame&) override {
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    return false;
  }
  bool WriteFrame(const boat::hil::CanFrame&) override { return true; }
  boat::hil::CanInterfaceInfo GetInfo() const override {
    boat::hil::CanInterfaceInfo info;
    info.driver_name = "loopback";
    info.state = "up";
    return info;
  }
};

}  // namespace

TEST_CASE("Subscription teardown races cleanly with dispatch",
          "[integration][gateway][frame][pdu]") {
  /* Regression test for the N18-class use-after-free on every streaming RPC:
     producers snapshot their subscriber callbacks under a lock and invoke them
     after releasing it, and Unsubscribe does not wait for a callback already
     in flight. The handlers used to capture stack locals (a mutex, a queue,
     even the gRPC writer) by reference, so a callback racing the handler's
     return wrote into a dead stack frame. The fix heap-owns the queue
     (subscriber_queue.h); under ASan/TSan the old code reports here.

     Each round opens a stream, dispatches frames/PDUs while it is live, then
     cancels and joins -- the exact window where a straggling callback can
     outlive the handler. */
  std::filesystem::remove("boat_config.db");
  const auto temp_dir = std::filesystem::temp_directory_path();
  const auto event_db_path = temp_dir / "boat_integration_sub_teardown_events.db";
  const auto trace_db_path = temp_dir / "boat_integration_sub_teardown_traces.db";
  std::filesystem::remove(event_db_path);
  std::filesystem::remove(trace_db_path);

  boat::core::SimulationContext sim(779, 2);
  boat::core::SignalBus signal_bus;
  boat::core::ScenarioLoader scenario_loader;
  boat::store::SqliteEventStore event_store(event_db_path.string());
  boat::store::FlatFileTraceStore trace_store(trace_db_path.string());
  boat::replay::ReplayController replay_controller(trace_store, event_store, sim.event_bus());
  boat::hil::CanBusRegistry can_registry;
  REQUIRE(can_registry.Add("vcan0", std::make_shared<LoopbackCanDriver>(), sim.event_bus()));
  boat::hil::EthernetBusRegistry eth_registry;
  boat::core::PluginManager plugin_manager;
  boat::hil::PduRouter pdu_router(can_registry, eth_registry);
  plugin_manager.RegisterService("pdu_router", &pdu_router);
  boat::gateway::FrameSink frame_sink(can_registry, eth_registry);
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

  boat::gateway::FrameServiceImpl frame_service(ctx);
  boat::gateway::PduServiceImpl pdu_service(ctx);
  boat::gateway::BusServiceImpl bus_service(audit_log, signal_bus, ctx.stream_drops,
                                            ctx.stream_queue_capacity);
  boat::gateway::SignalServiceImpl signal_service(ctx);
  boat::gateway::DebugServiceImpl debug_service(audit_log, kTestEffectiveConfig,
                                                ctx.stream_drops, ctx.stream_queue_capacity);

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&frame_service);
  builder.RegisterService(&pdu_service);
  builder.RegisterService(&bus_service);
  builder.RegisterService(&signal_service);
  builder.RegisterService(&debug_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});

  auto frame_stub = boat::v1::FrameService::NewStub(channel);
  auto pdu_stub = boat::v1::PduService::NewStub(channel);
  auto bus_stub = boat::v1::BusService::NewStub(channel);
  auto signal_stub = boat::v1::SignalService::NewStub(channel);
  auto debug_stub = boat::v1::DebugService::NewStub(channel);

  boat::v1::ConfigureRouteRequest route_req;
  route_req.mutable_route()->set_pdu_id(0x100);
  route_req.mutable_route()->set_transport(boat::v1::PDU_TRANSPORT_CAN);
  route_req.mutable_route()->set_iface("vcan0");
  boat::v1::ConfigureRouteResponse route_resp;
  grpc::ClientContext route_ctx;
  REQUIRE(pdu_stub->ConfigureRoute(&route_ctx, route_req, &route_resp).ok());

  boat::hil::CanFrame can_frame {};
  can_frame.can_id = 0x100;
  can_frame.dlc = 2;
  can_frame.data[0] = 0xAB;
  can_frame.data[1] = 0xCD;

  for (int round = 0; round < 20; ++round) {
    grpc::ClientContext sub_ctx;
    boat::v1::SubscribeFramesRequest request;
    request.add_bus_types(boat::v1::Frame::CAN);
    auto reader = frame_stub->SubscribeFrames(&sub_ctx, request);
    std::thread reader_thread([&reader] {
      boat::v1::Frame frame;
      while (reader->Read(&frame)) {
      }
    });
    for (int i = 0; i < 100; ++i) {
      can_registry.SendFrame("vcan0", can_frame);
    }
    sub_ctx.TryCancel();
    reader_thread.join();
    (void)reader->Finish();
  }

  for (int round = 0; round < 20; ++round) {
    grpc::ClientContext sub_ctx;
    boat::v1::SubscribePdusRequest request;
    request.add_pdu_ids(0x100);
    auto reader = pdu_stub->SubscribePdus(&sub_ctx, request);
    std::thread reader_thread([&reader] {
      boat::v1::PduFrame frame;
      while (reader->Read(&frame)) {
      }
    });
    for (int i = 0; i < 100; ++i) {
      pdu_router.OnCanFrame(can_frame, "vcan0");
    }
    sub_ctx.TryCancel();
    reader_thread.join();
    (void)reader->Finish();
  }

  /* BusService, SignalService and DebugService used to keep their queue (and,
     for the debug stream, its filter string) as stack locals captured by
     reference -- SignalBus::Publish, SignalRouter dispatch and RpcAuditLog::
     Push all invoke callbacks after releasing their lock and Unsubscribe does
     not wait for one in flight, so these were the same use-after-free. They
     now share the heap-owned queue; the rounds below race teardown against
     dispatch exactly like the frame and PDU ones above. */
  for (int round = 0; round < 10; ++round) {
    {
      grpc::ClientContext sub_ctx;
      boat::v1::BusSubscribeRequest request;
      auto reader = bus_stub->Subscribe(&sub_ctx, request);
      std::thread reader_thread([&reader] {
        boat::v1::BusSignal sig;
        while (reader->Read(&sig)) {
        }
      });
      for (int i = 0; i < 50; ++i) {
        signal_bus.Publish("teardown.sig", 1.0);
      }
      sub_ctx.TryCancel();
      reader_thread.join();
      (void)reader->Finish();
    }
    {
      grpc::ClientContext sub_ctx;
      boat::v1::SubscribeSignalsRequest request;
      request.add_names("teardown.sig");
      auto reader = signal_stub->SubscribeSignals(&sub_ctx, request);
      std::thread reader_thread([&reader] {
        boat::v1::SignalValue value;
        while (reader->Read(&value)) {
        }
      });
      const std::uint64_t signal_id =
          static_cast<std::uint64_t>(std::hash<std::string>{}("teardown.sig"));
      for (int i = 0; i < 50; ++i) {
        boat::core::SignalEvent event;
        event.signal_id = signal_id;
        event.tick = static_cast<std::uint64_t>(i);
        event.value = 1.0;
        sim.signal_router().Publish(event);
      }
      sub_ctx.TryCancel();
      reader_thread.join();
      (void)reader->Finish();
    }
    {
      grpc::ClientContext sub_ctx;
      boat::v1::StreamRpcEventsRequest request;
      auto reader = debug_stub->StreamEvents(&sub_ctx, request);
      std::thread reader_thread([&reader] {
        boat::v1::RpcEvent event;
        while (reader->Read(&event)) {
        }
      });
      for (int i = 0; i < 50; ++i) {
        boat::gateway::RpcEvent event;
        event.method = "teardown";
        event.event_type = "DATA";
        audit_log.Push(std::move(event));
      }
      sub_ctx.TryCancel();
      reader_thread.join();
      (void)reader->Finish();
    }
  }

  server->Shutdown();
  std::filesystem::remove("boat_config.db");
  std::filesystem::remove(event_db_path);
  std::filesystem::remove(trace_db_path);
}

TEST_CASE("Reset rewinds the tick and only one simulation can be active",
          "[integration][gateway][simulation]") {
  /* The gateway hosts one live session; `simulation_id` selects a scenario
     definition, not an instance. Before this, ResetSimulation left the tick
     where it was (a reset simulation reported tick 11911, and a fresh
     CreateSimulation inherited it), and StartSimulation on any id drove the
     same shared state. */
  std::filesystem::remove("boat_config.db");
  const auto temp_dir = std::filesystem::temp_directory_path();
  const auto event_db_path = temp_dir / "boat_integration_sim_reset_events.db";
  const auto trace_db_path = temp_dir / "boat_integration_sim_reset_traces.db";
  std::filesystem::remove(event_db_path);
  std::filesystem::remove(trace_db_path);

  boat::core::SimulationContext sim(782, 2);
  boat::core::SignalBus signal_bus;
  boat::core::ScenarioLoader scenario_loader;
  boat::store::SqliteEventStore event_store(event_db_path.string());
  boat::store::FlatFileTraceStore trace_store(trace_db_path.string());
  boat::replay::ReplayController replay_controller(trace_store, event_store, sim.event_bus());
  boat::hil::CanBusRegistry can_registry;
  boat::hil::EthernetBusRegistry eth_registry;
  boat::core::PluginManager plugin_manager;
  boat::gateway::FrameSink frame_sink(can_registry, eth_registry);
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

  boat::gateway::SimulationServiceImpl simulation_service(sim, can_registry, eth_registry);
  boat::gateway::ScenarioServiceImpl scenario_service(ctx);

  grpc::ServerBuilder builder;
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials());
  builder.RegisterService(&simulation_service);
  builder.RegisterService(&scenario_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  auto channel = server->InProcessChannel({});

  auto scenario_stub = boat::v1::ScenarioService::NewStub(channel);
  auto simulation_stub = boat::v1::SimulationService::NewStub(channel);

  const auto create_scenario = [&](const std::string& id, std::uint64_t seed) {
    boat::v1::CreateScenarioRequest req;
    req.mutable_scenario()->set_scenario_id(id);
    req.mutable_scenario()->set_name(id);
    req.mutable_scenario()->set_content(
        R"({"id":")" + id +
        R"(","name":")" + id +
        R"(","version":"1.0","duration_ticks":1000,"seed":)" + std::to_string(seed) +
        R"(,"plugins":[],"signals":[],"faults":[]})");
    boat::v1::ScenarioResponse resp;
    grpc::ClientContext context;
    REQUIRE(scenario_stub->CreateScenario(&context, req, &resp).ok());
  };

  const auto create_sim = [&](const std::string& scenario_id) {
    boat::v1::CreateSimulationRequest req;
    req.set_scenario_id(scenario_id);
    boat::v1::SimulationResponse resp;
    grpc::ClientContext context;
    REQUIRE(simulation_stub->CreateSimulation(&context, req, &resp).ok());
    return resp.simulation();
  };

  create_scenario("scn-reset", 1);
  const auto created_a = create_sim("scn-reset");
  REQUIRE(created_a.tick() == 0);
  REQUIRE(created_a.state() == boat::v1::SIMULATION_STATE_IDLE);

  {
    boat::v1::StartSimulationRequest req;
    req.set_simulation_id(created_a.simulation_id());
    boat::v1::SimulationResponse resp;
    grpc::ClientContext context;
    REQUIRE(simulation_stub->StartSimulation(&context, req, &resp).ok());
    // The scenario's own seed is what the run's random stream comes from
    // (issue #53); the gateway's fixed seed used to win and make the field
    // meaningless.
    REQUIRE(sim.determinism().Seed() == 1);
  }

  // A second definition can be created while one session runs -- it is staged,
  // not started -- but it must not be startable over the live one.
  create_scenario("scn-other", 2);
  const auto created_b = create_sim("scn-other");
  {
    boat::v1::StartSimulationRequest req;
    req.set_simulation_id(created_b.simulation_id());
    boat::v1::SimulationResponse resp;
    grpc::ClientContext context;
    const auto status = simulation_stub->StartSimulation(&context, req, &resp);
    REQUIRE_FALSE(status.ok());
    REQUIRE(status.error_code() == grpc::StatusCode::FAILED_PRECONDITION);
  }

  // Advance the live session, then reset it from PAUSED: IDLE at tick 0.
  {
    boat::v1::PauseSimulationRequest req;
    req.set_simulation_id(created_a.simulation_id());
    boat::v1::SimulationResponse resp;
    grpc::ClientContext context;
    REQUIRE(simulation_stub->PauseSimulation(&context, req, &resp).ok());
  }
  {
    boat::v1::StepSimulationRequest req;
    req.set_simulation_id(created_a.simulation_id());
    req.set_ticks(7);
    boat::v1::SimulationResponse resp;
    grpc::ClientContext context;
    REQUIRE(simulation_stub->StepSimulation(&context, req, &resp).ok());
    REQUIRE(sim.clock().tick() == 7);
  }
  {
    boat::v1::ResetSimulationRequest req;
    req.set_simulation_id(created_a.simulation_id());
    boat::v1::SimulationResponse resp;
    grpc::ClientContext context;
    REQUIRE(simulation_stub->ResetSimulation(&context, req, &resp).ok());
    REQUIRE(resp.simulation().state() == boat::v1::SIMULATION_STATE_IDLE);
    REQUIRE(resp.simulation().tick() == 0);
    REQUIRE(sim.clock().tick() == 0);
    // A reset run is a rerun of the same scenario, so its seed is restored
    // rather than left at whatever ran before.
    REQUIRE(sim.determinism().Seed() == 1);
  }

  // The reset cleared the active session, so the staged definition can start
  // now -- and tick 1 works again instead of throwing
  // "tick must be monotonically increasing".
  {
    boat::v1::StartSimulationRequest req;
    req.set_simulation_id(created_b.simulation_id());
    boat::v1::SimulationResponse resp;
    grpc::ClientContext context;
    REQUIRE(simulation_stub->StartSimulation(&context, req, &resp).ok());
    REQUIRE(sim.determinism().Seed() == 2);  // the second scenario's seed
  }
  {
    boat::v1::StopSimulationRequest req;
    req.set_simulation_id(created_b.simulation_id());
    boat::v1::SimulationResponse resp;
    grpc::ClientContext context;
    REQUIRE(simulation_stub->StopSimulation(&context, req, &resp).ok());
  }

  // Creating after a stopped run rewinds: the new definition reports tick 0
  // before it has ever been started, and a start-step cycle lands on tick 3.
  const auto created_c = create_sim("scn-reset");
  REQUIRE(created_c.tick() == 0);
  {
    boat::v1::StartSimulationRequest req;
    req.set_simulation_id(created_c.simulation_id());
    boat::v1::SimulationResponse resp;
    grpc::ClientContext context;
    REQUIRE(simulation_stub->StartSimulation(&context, req, &resp).ok());
  }
  {
    boat::v1::PauseSimulationRequest req;
    req.set_simulation_id(created_c.simulation_id());
    boat::v1::SimulationResponse resp;
    grpc::ClientContext context;
    REQUIRE(simulation_stub->PauseSimulation(&context, req, &resp).ok());
  }
  {
    boat::v1::StepSimulationRequest req;
    req.set_simulation_id(created_c.simulation_id());
    req.set_ticks(3);
    boat::v1::SimulationResponse resp;
    grpc::ClientContext context;
    REQUIRE(simulation_stub->StepSimulation(&context, req, &resp).ok());
    REQUIRE(sim.clock().tick() == 3);
  }

  // Resetting an already-idle simulation is a success, not FAILED_PRECONDITION.
  {
    boat::v1::ResetSimulationRequest req;
    req.set_simulation_id(created_c.simulation_id());
    boat::v1::SimulationResponse resp;
    grpc::ClientContext context;
    REQUIRE(simulation_stub->ResetSimulation(&context, req, &resp).ok());
    REQUIRE(sim.clock().tick() == 0);
  }
  {
    boat::v1::ResetSimulationRequest req;
    req.set_simulation_id(created_c.simulation_id());
    boat::v1::SimulationResponse resp;
    grpc::ClientContext context;
    REQUIRE(simulation_stub->ResetSimulation(&context, req, &resp).ok());
    REQUIRE(sim.clock().tick() == 0);
  }

  server->Shutdown();
  std::filesystem::remove("boat_config.db");
  std::filesystem::remove(event_db_path);
  std::filesystem::remove(trace_db_path);
}

TEST_CASE("A slow frame subscriber overflows into dropped-oldest, and metrics say so",
          "[integration][gateway][frame][metrics]") {
  /* The gRPC streams used to buffer without bound: a fast bus plus a slow
     client grew the server process until it was OOM-killed. The queues are now
     bounded and drop the oldest events, and the cumulative count is served as
     grpc.<stream>.dropped so a consumer can tell its view has gaps.

     This drives the real failure mode over a real TCP channel: the client
     never reads, the HTTP/2 window fills, the server's writer blocks in
     Write(), and the handler queue (capacity 8) overflows while it is parked.
     In-process channels are no good here -- they do not exercise transport
     flow control, and it is the blocked writer that creates the backlog. */
  std::filesystem::remove("boat_config.db");
  const auto temp_dir = std::filesystem::temp_directory_path();
  const auto event_db_path = temp_dir / "boat_integration_stream_overflow_events.db";
  const auto trace_db_path = temp_dir / "boat_integration_stream_overflow_traces.db";
  std::filesystem::remove(event_db_path);
  std::filesystem::remove(trace_db_path);

  boat::core::SimulationContext sim(781, 2);
  boat::core::SignalBus signal_bus;
  boat::core::ScenarioLoader scenario_loader;
  boat::store::SqliteEventStore event_store(event_db_path.string());
  boat::store::FlatFileTraceStore trace_store(trace_db_path.string());
  boat::replay::ReplayController replay_controller(trace_store, event_store, sim.event_bus());
  boat::hil::CanBusRegistry can_registry;
  REQUIRE(can_registry.Add("vcan0", std::make_shared<LoopbackCanDriver>(), sim.event_bus()));
  boat::hil::EthernetBusRegistry eth_registry;
  boat::core::PluginManager plugin_manager;
  boat::gateway::FrameSink frame_sink(can_registry, eth_registry);
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
  // Small enough that the overflow happens while the writer is briefly blocked
  // on the transport window, without publishing gigabytes.
  ctx.stream_queue_capacity = 8;

  boat::gateway::FrameServiceImpl frame_service(ctx);
  boat::gateway::MetricsServiceImpl metrics_service(ctx);

  grpc::ServerBuilder builder;
  int port = 0;  // filled in by BuildAndStart() for a :0 listen address
  builder.AddListeningPort("127.0.0.1:0", grpc::InsecureServerCredentials(), &port);
  builder.RegisterService(&frame_service);
  builder.RegisterService(&metrics_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  REQUIRE(server != nullptr);
  REQUIRE(port > 0);

  auto channel = grpc::CreateChannel("127.0.0.1:" + std::to_string(port),
                                     grpc::InsecureChannelCredentials());
  auto frame_stub = boat::v1::FrameService::NewStub(channel);
  auto metrics_stub = boat::v1::MetricsService::NewStub(channel);

  grpc::ClientContext sub_ctx;
  sub_ctx.set_deadline(std::chrono::system_clock::now() + std::chrono::seconds(10));
  boat::v1::SubscribeFramesRequest request;
  request.add_bus_types(boat::v1::Frame::CAN);
  request.set_iface_filter("vcan0");
  auto reader = frame_stub->SubscribeFrames(&sub_ctx, request);

  boat::hil::CanFrame can_frame{};
  can_frame.can_id = 0x123;
  can_frame.dlc = 8;

  /* Read exactly one frame before the flood, then stop reading. This is a
     handshake, not the failure mode: it proves the server handler has
     registered in the registry and can deliver, so the 50k-frame flood below
     cannot be lost to the subscription-startup race. (Without it, a slow CI
     runner can process all 50k sends before the RPC handler runs, leaving the
     drop counter at zero.) A feeder thread keeps producing until the read
     returns; the deadline only stops a genuinely broken test from hanging. */
  std::atomic<bool> stop_feeding{false};
  std::thread feeder([&] {
    int i = 0;
    while (!stop_feeding.load(std::memory_order_relaxed)) {
      can_frame.data[0] = static_cast<std::uint8_t>(i++);
      can_registry.SendFrame("vcan0", can_frame);
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
  });
  boat::v1::Frame first;
  const bool got_first = reader->Read(&first);
  stop_feeding.store(true, std::memory_order_relaxed);
  feeder.join();
  REQUIRE(got_first);

  // The client stops reading here. The transport window fills, the server
  // writer parks, and the capacity-8 queue must overflow.
  for (int i = 0; i < 50000; ++i) {
    can_frame.data[0] = static_cast<std::uint8_t>(i);
    can_registry.SendFrame("vcan0", can_frame);
  }
  // Let the server writer hit the transport window and the queue overflow
  // while it is parked there.
  std::this_thread::sleep_for(std::chrono::milliseconds(200));

  boat::v1::GetMetricsRequest metrics_req;
  boat::v1::MetricsResponse metrics_resp;
  grpc::ClientContext metrics_ctx;
  REQUIRE(metrics_stub->GetMetrics(&metrics_ctx, metrics_req, &metrics_resp).ok());

  double dropped = -1.0;
  double pdu_dropped = -1.0;
  double replay_dropped = -1.0;
  double router_dropped = -1.0;
  double event_bus_dropped = -1.0;
  for (const auto& point : metrics_resp.points()) {
    if (point.name() == "grpc.frames.subscribe.dropped") dropped = point.value();
    if (point.name() == "grpc.pdus.subscribe.dropped") pdu_dropped = point.value();
    if (point.name() == "grpc.replay.stream.dropped") replay_dropped = point.value();
    if (point.name() == "signal_router.dropped") router_dropped = point.value();
    if (point.name() == "event_bus.dropped") event_bus_dropped = point.value();
  }
  REQUIRE(dropped > 0.0);
  // Every stream is reported, zeros included: "no drops" and "does not
  // report" must not look the same to a consumer.
  REQUIRE(pdu_dropped == 0.0);
  REQUIRE(replay_dropped == 0.0);
  REQUIRE(router_dropped == 0.0);
  REQUIRE(event_bus_dropped == 0.0);

  sub_ctx.TryCancel();
  (void)reader->Finish();
  server->Shutdown();
  std::filesystem::remove("boat_config.db");
  std::filesystem::remove(event_db_path);
  std::filesystem::remove(trace_db_path);
}

#include <csignal>
#include <memory>

#include <grpcpp/grpcpp.h>

#include "determinism/determinism_engine.h"
#include "event/event_bus.h"
#include "fault/fault_injector.h"
#include "fault_service_impl.h"
#include "gateway_context.h"
#include "metrics_service_impl.h"
#include "plugin/plugin_manager.h"
#include "plugin_service_impl.h"
#include "replay_engine/replay_engine.h"
#include "replay_service_impl.h"
#include "scenario/scenario_loader.h"
#include "scenario_service_impl.h"
#include "scheduler/sim_clock.h"
#include "scheduler/tick_scheduler.h"
#include "signal/signal_router.h"
#include "signal_service_impl.h"
#include "simulation_service_impl.h"
#include "state/sim_state_machine.h"
#include "event_store/event_store.h"
#include "trace_store/trace_store.h"
#include "trace_service_impl.h"

namespace {
std::shared_ptr<grpc::Server> g_server;
boat::core::TickScheduler* g_scheduler = nullptr;

void HandleSignal(int) {
  if (g_server) {
    g_server->Shutdown();
  }
  if (g_scheduler != nullptr) {
    g_scheduler->Stop();
  }
}
}  // namespace

int main() {
  boat::core::SimClock clock(0);
  boat::core::DeterminismEngine determinism;
  boat::core::EventBus event_bus;
  boat::core::SignalRouter signal_router;
  boat::core::FaultInjector fault_injector(determinism);
  boat::core::SimStateMachine state_machine;
  boat::core::PluginManager plugin_manager;
  boat::core::TickScheduler scheduler(clock, event_bus, determinism);
  boat::store::SqliteEventStore event_store("boat_events.db");
  boat::store::FlatFileTraceStore trace_store("boat_traces.db");
  boat::replay::ReplayController replay_controller(trace_store, event_store, event_bus);
  boat::core::ScenarioLoader scenario_loader;

  boat::gateway::GatewayContext ctx{
      .sim_state_machine = state_machine,
      .tick_scheduler = scheduler,
      .event_bus = event_bus,
      .signal_router = signal_router,
      .plugin_manager = plugin_manager,
      .fault_injector = fault_injector,
      .scenario_loader = scenario_loader,
      .event_store = event_store,
      .trace_store = trace_store,
      .replay_controller = replay_controller,
  };

  boat::gateway::SimulationServiceImpl simulation_impl(ctx);
  boat::gateway::SignalServiceImpl signal_impl(ctx);
  boat::gateway::ScenarioServiceImpl scenario_impl(ctx);
  boat::gateway::ReplayServiceImpl replay_impl(ctx);
  boat::gateway::PluginServiceImpl plugin_impl(ctx);
  boat::gateway::MetricsServiceImpl metrics_impl(ctx);
  boat::gateway::TraceServiceImpl trace_impl(ctx);
  boat::gateway::FaultServiceImpl fault_impl(ctx);

  grpc::ServerBuilder builder;
  builder.AddListeningPort("0.0.0.0:50051", grpc::InsecureServerCredentials());
  builder.RegisterService(&simulation_impl);
  builder.RegisterService(&signal_impl);
  builder.RegisterService(&scenario_impl);
  builder.RegisterService(&replay_impl);
  builder.RegisterService(&plugin_impl);
  builder.RegisterService(&metrics_impl);
  builder.RegisterService(&trace_impl);
  builder.RegisterService(&fault_impl);

  g_server = builder.BuildAndStart();
  g_scheduler = &scheduler;
  std::signal(SIGINT, HandleSignal);
  std::signal(SIGTERM, HandleSignal);

  if (g_server) {
    g_server->Wait();
  }
  scheduler.Stop();
  return 0;
}

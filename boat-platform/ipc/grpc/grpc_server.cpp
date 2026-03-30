#include "ipc/grpc/grpc_server.h"

namespace boat::ipc {

GrpcServer::GrpcServer(boat::core::EventBus& event_bus, boat::core::SimStateMachine& sim_state_machine,
                       boat::core::PluginManager& plugin_manager, boat::core::SignalRouter& signal_router)
    : GrpcServer("0.0.0.0:50051", event_bus, sim_state_machine, plugin_manager, signal_router) {}

GrpcServer::GrpcServer(std::string listen_address, boat::core::EventBus& event_bus,
                       boat::core::SimStateMachine& sim_state_machine, boat::core::PluginManager& plugin_manager,
                       boat::core::SignalRouter& signal_router)
    : listen_address_(std::move(listen_address)),
      event_bus_(event_bus),
      sim_state_machine_(sim_state_machine),
      plugin_manager_(plugin_manager),
      signal_router_(signal_router) {}

void GrpcServer::Start() {
  grpc::ServerBuilder builder;
  builder.AddListeningPort(listen_address_, grpc::InsecureServerCredentials());

  simulation_service_ = std::make_unique<SimulationServiceStub>(event_bus_, sim_state_machine_, plugin_manager_, signal_router_);
  signal_service_ = std::make_unique<SignalServiceStub>(event_bus_, sim_state_machine_, plugin_manager_, signal_router_);
  scenario_service_ = std::make_unique<ScenarioServiceStub>(event_bus_, sim_state_machine_, plugin_manager_, signal_router_);
  replay_service_ = std::make_unique<ReplayServiceStub>(event_bus_, sim_state_machine_, plugin_manager_, signal_router_);
  plugin_service_ = std::make_unique<PluginServiceStub>(event_bus_, sim_state_machine_, plugin_manager_, signal_router_);
  metrics_service_ = std::make_unique<MetricsServiceStub>(event_bus_, sim_state_machine_, plugin_manager_, signal_router_);
  trace_service_ = std::make_unique<TraceServiceStub>(event_bus_, sim_state_machine_, plugin_manager_, signal_router_);
  fault_service_ = std::make_unique<FaultServiceStub>(event_bus_, sim_state_machine_, plugin_manager_, signal_router_);

  builder.RegisterService(simulation_service_.get());
  builder.RegisterService(signal_service_.get());
  builder.RegisterService(scenario_service_.get());
  builder.RegisterService(replay_service_.get());
  builder.RegisterService(plugin_service_.get());
  builder.RegisterService(metrics_service_.get());
  builder.RegisterService(trace_service_.get());
  builder.RegisterService(fault_service_.get());

  server_ = builder.BuildAndStart();
}

void GrpcServer::Shutdown() {
  if (server_) {
    server_->Shutdown();
  }
}

}  // namespace boat::ipc

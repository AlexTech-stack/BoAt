#pragma once

#include <memory>
#include <string>

#include <grpcpp/grpcpp.h>

#include "core/event/event_bus.h"
#include "core/plugin/plugin_manager.h"
#include "core/signal/signal_router.h"
#include "core/state/sim_state_machine.h"
#include "ipc/grpc/services/fault_service_stub.h"
#include "ipc/grpc/services/metrics_service_stub.h"
#include "ipc/grpc/services/plugin_service_stub.h"
#include "ipc/grpc/services/replay_service_stub.h"
#include "ipc/grpc/services/scenario_service_stub.h"
#include "ipc/grpc/services/signal_service_stub.h"
#include "ipc/grpc/services/simulation_service_stub.h"
#include "ipc/grpc/services/trace_service_stub.h"

namespace boat::ipc {

class GrpcServer {
 public:
  GrpcServer(boat::core::EventBus& event_bus, boat::core::SimStateMachine& sim_state_machine,
             boat::core::PluginManager& plugin_manager, boat::core::SignalRouter& signal_router);
  GrpcServer(std::string listen_address, boat::core::EventBus& event_bus,
             boat::core::SimStateMachine& sim_state_machine, boat::core::PluginManager& plugin_manager,
             boat::core::SignalRouter& signal_router);

  void Start();
  void Shutdown();

 private:
  std::string listen_address_;
  std::unique_ptr<grpc::Server> server_;

  std::unique_ptr<SimulationServiceStub> simulation_service_;
  std::unique_ptr<SignalServiceStub> signal_service_;
  std::unique_ptr<ScenarioServiceStub> scenario_service_;
  std::unique_ptr<ReplayServiceStub> replay_service_;
  std::unique_ptr<PluginServiceStub> plugin_service_;
  std::unique_ptr<MetricsServiceStub> metrics_service_;
  std::unique_ptr<TraceServiceStub> trace_service_;
  std::unique_ptr<FaultServiceStub> fault_service_;

  boat::core::EventBus& event_bus_;
  boat::core::SimStateMachine& sim_state_machine_;
  boat::core::PluginManager& plugin_manager_;
  boat::core::SignalRouter& signal_router_;
};

}  // namespace boat::ipc

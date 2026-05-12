#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/simulation.grpc.pb.h"
#include "core/event/event_bus.h"
#include "core/plugin/plugin_manager.h"
#include "core/signal/signal_router.h"
#include "core/state/sim_state_machine.h"

namespace boat::ipc {

class SimulationServiceStub final : public boat::v1::SimulationService::Service {
 public:
  SimulationServiceStub(boat::core::EventBus& event_bus, boat::core::SimStateMachine& sim_state_machine,
                        boat::core::PluginManager& plugin_manager, boat::core::SignalRouter& signal_router);

  grpc::Status CreateSimulation(grpc::ServerContext* context, const boat::v1::CreateSimulationRequest* request,
                                boat::v1::SimulationResponse* response) override;
  grpc::Status StartSimulation(grpc::ServerContext* context, const boat::v1::StartSimulationRequest* request,
                               boat::v1::SimulationResponse* response) override;
  grpc::Status PauseSimulation(grpc::ServerContext* context, const boat::v1::PauseSimulationRequest* request,
                               boat::v1::SimulationResponse* response) override;
  grpc::Status StepSimulation(grpc::ServerContext* context, const boat::v1::StepSimulationRequest* request,
                              boat::v1::SimulationResponse* response) override;
  grpc::Status ResetSimulation(grpc::ServerContext* context, const boat::v1::ResetSimulationRequest* request,
                               boat::v1::SimulationResponse* response) override;
  grpc::Status StopSimulation(grpc::ServerContext* context, const boat::v1::StopSimulationRequest* request,
                              boat::v1::SimulationResponse* response) override;
  grpc::Status GetSimulationState(grpc::ServerContext* context, const boat::v1::GetSimulationStateRequest* request,
                                  boat::v1::SimulationResponse* response) override;
  grpc::Status WatchSimulation(grpc::ServerContext* context, const boat::v1::GetSimulationStateRequest* request,
                               grpc::ServerWriter<boat::v1::SimulationResponse>* writer) override;
  grpc::Status ListSimulations(grpc::ServerContext* context, const boat::v1::ListSimulationsRequest* request,
                               boat::v1::ListSimulationsResponse* response) override;

 private:
  boat::core::EventBus& event_bus_;
  boat::core::SimStateMachine& sim_state_machine_;
  boat::core::PluginManager& plugin_manager_;
  boat::core::SignalRouter& signal_router_;
};

}  // namespace boat::ipc

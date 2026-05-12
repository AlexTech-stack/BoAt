#include "ipc/grpc/services/simulation_service_stub.h"

namespace boat::ipc {

SimulationServiceStub::SimulationServiceStub(boat::core::EventBus& event_bus,
                                             boat::core::SimStateMachine& sim_state_machine,
                                             boat::core::PluginManager& plugin_manager,
                                             boat::core::SignalRouter& signal_router)
    : event_bus_(event_bus),
      sim_state_machine_(sim_state_machine),
      plugin_manager_(plugin_manager),
      signal_router_(signal_router) {}

grpc::Status SimulationServiceStub::CreateSimulation(grpc::ServerContext*, const boat::v1::CreateSimulationRequest*,
                                                     boat::v1::SimulationResponse*) {
  return grpc::Status::OK;
}
grpc::Status SimulationServiceStub::StartSimulation(grpc::ServerContext*, const boat::v1::StartSimulationRequest*,
                                                    boat::v1::SimulationResponse*) {
  return grpc::Status::OK;
}
grpc::Status SimulationServiceStub::PauseSimulation(grpc::ServerContext*, const boat::v1::PauseSimulationRequest*,
                                                    boat::v1::SimulationResponse*) {
  return grpc::Status::OK;
}
grpc::Status SimulationServiceStub::StepSimulation(grpc::ServerContext*, const boat::v1::StepSimulationRequest*,
                                                   boat::v1::SimulationResponse*) {
  return grpc::Status::OK;
}
grpc::Status SimulationServiceStub::ResetSimulation(grpc::ServerContext*, const boat::v1::ResetSimulationRequest*,
                                                    boat::v1::SimulationResponse*) {
  return grpc::Status::OK;
}
grpc::Status SimulationServiceStub::StopSimulation(grpc::ServerContext*, const boat::v1::StopSimulationRequest*,
                                                   boat::v1::SimulationResponse*) {
  return grpc::Status::OK;
}
grpc::Status SimulationServiceStub::GetSimulationState(grpc::ServerContext*,
                                                       const boat::v1::GetSimulationStateRequest*,
                                                       boat::v1::SimulationResponse*) {
  return grpc::Status::OK;
}
grpc::Status SimulationServiceStub::WatchSimulation(grpc::ServerContext*, const boat::v1::GetSimulationStateRequest*,
                                                    grpc::ServerWriter<boat::v1::SimulationResponse>*) {
  return grpc::Status::OK;
}
grpc::Status SimulationServiceStub::ListSimulations(grpc::ServerContext*, const boat::v1::ListSimulationsRequest*,
                                                    boat::v1::ListSimulationsResponse*) {
  return grpc::Status::OK;
}

}  // namespace boat::ipc

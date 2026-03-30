#include "ipc/grpc/services/scenario_service_stub.h"

namespace boat::ipc {

ScenarioServiceStub::ScenarioServiceStub(boat::core::EventBus& event_bus,
                                         boat::core::SimStateMachine& sim_state_machine,
                                         boat::core::PluginManager& plugin_manager,
                                         boat::core::SignalRouter& signal_router)
    : event_bus_(event_bus),
      sim_state_machine_(sim_state_machine),
      plugin_manager_(plugin_manager),
      signal_router_(signal_router) {}

grpc::Status ScenarioServiceStub::CreateScenario(grpc::ServerContext*, const boat::v1::CreateScenarioRequest*,
                                                 boat::v1::ScenarioResponse*) {
  return grpc::Status::OK;
}
grpc::Status ScenarioServiceStub::GetScenario(grpc::ServerContext*, const boat::v1::GetScenarioRequest*,
                                              boat::v1::ScenarioResponse*) {
  return grpc::Status::OK;
}
grpc::Status ScenarioServiceStub::ListScenarios(grpc::ServerContext*, const boat::v1::ListScenariosRequest*,
                                                boat::v1::ListScenariosResponse*) {
  return grpc::Status::OK;
}
grpc::Status ScenarioServiceStub::ValidateScenario(grpc::ServerContext*, const boat::v1::ValidateScenarioRequest*,
                                                   boat::v1::ValidateScenarioResponse*) {
  return grpc::Status::OK;
}
grpc::Status ScenarioServiceStub::DeleteScenario(grpc::ServerContext*, const boat::v1::DeleteScenarioRequest*,
                                                 boat::v1::DeleteScenarioResponse*) {
  return grpc::Status::OK;
}

}  // namespace boat::ipc

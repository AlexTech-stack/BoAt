#include "ipc/grpc/services/fault_service_stub.h"

namespace boat::ipc {

FaultServiceStub::FaultServiceStub(boat::core::EventBus& event_bus, boat::core::SimStateMachine& sim_state_machine,
                                   boat::core::PluginManager& plugin_manager,
                                   boat::core::SignalRouter& signal_router)
    : event_bus_(event_bus),
      sim_state_machine_(sim_state_machine),
      plugin_manager_(plugin_manager),
      signal_router_(signal_router) {}

grpc::Status FaultServiceStub::InjectFault(grpc::ServerContext*, const boat::v1::InjectFaultRequest*,
                                           boat::v1::InjectFaultResponse*) {
  return grpc::Status::OK;
}
grpc::Status FaultServiceStub::ListFaults(grpc::ServerContext*, const boat::v1::ListFaultsRequest*,
                                          boat::v1::ListFaultsResponse*) {
  return grpc::Status::OK;
}

}  // namespace boat::ipc

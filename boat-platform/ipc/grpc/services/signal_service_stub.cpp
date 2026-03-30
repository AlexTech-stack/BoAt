#include "ipc/grpc/services/signal_service_stub.h"

namespace boat::ipc {

SignalServiceStub::SignalServiceStub(boat::core::EventBus& event_bus, boat::core::SimStateMachine& sim_state_machine,
                                     boat::core::PluginManager& plugin_manager,
                                     boat::core::SignalRouter& signal_router)
    : event_bus_(event_bus),
      sim_state_machine_(sim_state_machine),
      plugin_manager_(plugin_manager),
      signal_router_(signal_router) {}

grpc::Status SignalServiceStub::InjectSignal(grpc::ServerContext*, const boat::v1::InjectSignalRequest*,
                                             boat::v1::InjectSignalResponse*) {
  return grpc::Status::OK;
}
grpc::Status SignalServiceStub::SubscribeSignals(grpc::ServerContext*, const boat::v1::SubscribeSignalsRequest*,
                                                 grpc::ServerWriter<boat::v1::SignalValue>*) {
  return grpc::Status::OK;
}
grpc::Status SignalServiceStub::GetSignalHistory(grpc::ServerContext*, const boat::v1::GetSignalHistoryRequest*,
                                                 boat::v1::SignalHistoryResponse*) {
  return grpc::Status::OK;
}

}  // namespace boat::ipc

#include "ipc/grpc/services/replay_service_stub.h"

namespace boat::ipc {

ReplayServiceStub::ReplayServiceStub(boat::core::EventBus& event_bus, boat::core::SimStateMachine& sim_state_machine,
                                     boat::core::PluginManager& plugin_manager,
                                     boat::core::SignalRouter& signal_router)
    : event_bus_(event_bus),
      sim_state_machine_(sim_state_machine),
      plugin_manager_(plugin_manager),
      signal_router_(signal_router) {}

grpc::Status ReplayServiceStub::StartReplay(grpc::ServerContext*, const boat::v1::StartReplayRequest*,
                                            boat::v1::ReplayControlResponse*) {
  return grpc::Status::OK;
}
grpc::Status ReplayServiceStub::SeekReplay(grpc::ServerContext*, const boat::v1::SeekReplayRequest*,
                                           boat::v1::ReplayControlResponse*) {
  return grpc::Status::OK;
}
grpc::Status ReplayServiceStub::StreamReplay(grpc::ServerContext*, const boat::v1::StreamReplayRequest*,
                                             grpc::ServerWriter<boat::v1::ReplayEvent>*) {
  return grpc::Status::OK;
}

}  // namespace boat::ipc

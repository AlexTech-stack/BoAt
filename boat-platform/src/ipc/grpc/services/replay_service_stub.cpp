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
  // IPC stub: no-op — the gateway binary uses ReplayServiceImpl directly.
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
grpc::Status ReplayServiceStub::PauseReplay(grpc::ServerContext*, const boat::v1::PauseReplayRequest*,
                                            boat::v1::ReplayControlResponse*) {
  return grpc::Status::OK;
}
grpc::Status ReplayServiceStub::ResumeReplay(grpc::ServerContext*, const boat::v1::ResumeReplayRequest*,
                                             boat::v1::ReplayControlResponse*) {
  return grpc::Status::OK;
}
grpc::Status ReplayServiceStub::StopReplay(grpc::ServerContext*, const boat::v1::StopReplayRequest*,
                                           boat::v1::ReplayControlResponse*) {
  return grpc::Status::OK;
}
grpc::Status ReplayServiceStub::ImportTraceData(grpc::ServerContext*, const boat::v1::ImportTraceDataRequest*,
                                                boat::v1::ReplayControlResponse*) {
  return grpc::Status::OK;
}
grpc::Status ReplayServiceStub::StartReplayFromEvents(grpc::ServerContext*,
                                                      const boat::v1::StartReplayFromEventsRequest*,
                                                      boat::v1::ReplayControlResponse*) {
  return grpc::Status::OK;
}

}  // namespace boat::ipc

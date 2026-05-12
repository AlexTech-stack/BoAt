#include "ipc/grpc/services/trace_service_stub.h"

namespace boat::ipc {

TraceServiceStub::TraceServiceStub(boat::core::EventBus& event_bus, boat::core::SimStateMachine& sim_state_machine,
                                   boat::core::PluginManager& plugin_manager,
                                   boat::core::SignalRouter& signal_router)
    : event_bus_(event_bus),
      sim_state_machine_(sim_state_machine),
      plugin_manager_(plugin_manager),
      signal_router_(signal_router) {}

grpc::Status TraceServiceStub::GetTrace(grpc::ServerContext*, const boat::v1::GetTraceRequest*,
                                        boat::v1::TraceResponse*) {
  return grpc::Status::OK;
}
grpc::Status TraceServiceStub::ListTraces(grpc::ServerContext*, const boat::v1::ListTracesRequest*,
                                          boat::v1::TraceResponse*) {
  return grpc::Status::OK;
}
grpc::Status TraceServiceStub::StreamTrace(grpc::ServerContext*, const boat::v1::StreamTraceRequest*,
                                           grpc::ServerWriter<boat::v1::TraceEvent>*) {
  return grpc::Status::OK;
}

}  // namespace boat::ipc

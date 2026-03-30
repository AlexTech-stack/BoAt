#include "ipc/grpc/services/metrics_service_stub.h"

namespace boat::ipc {

MetricsServiceStub::MetricsServiceStub(boat::core::EventBus& event_bus, boat::core::SimStateMachine& sim_state_machine,
                                       boat::core::PluginManager& plugin_manager,
                                       boat::core::SignalRouter& signal_router)
    : event_bus_(event_bus),
      sim_state_machine_(sim_state_machine),
      plugin_manager_(plugin_manager),
      signal_router_(signal_router) {}

grpc::Status MetricsServiceStub::GetMetrics(grpc::ServerContext*, const boat::v1::GetMetricsRequest*,
                                            boat::v1::MetricsResponse*) {
  return grpc::Status::OK;
}
grpc::Status MetricsServiceStub::StreamMetrics(grpc::ServerContext*, const boat::v1::StreamMetricsRequest*,
                                               grpc::ServerWriter<boat::v1::MetricPoint>*) {
  return grpc::Status::OK;
}

}  // namespace boat::ipc

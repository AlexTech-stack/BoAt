#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/metrics.grpc.pb.h"
#include "core/event/event_bus.h"
#include "core/plugin/plugin_manager.h"
#include "core/signal/signal_router.h"
#include "core/state/sim_state_machine.h"

namespace boat::ipc {

class MetricsServiceStub final : public boat::v1::MetricsService::Service {
 public:
  MetricsServiceStub(boat::core::EventBus& event_bus, boat::core::SimStateMachine& sim_state_machine,
                     boat::core::PluginManager& plugin_manager, boat::core::SignalRouter& signal_router);

  grpc::Status GetMetrics(grpc::ServerContext* context, const boat::v1::GetMetricsRequest* request,
                          boat::v1::MetricsResponse* response) override;
  grpc::Status StreamMetrics(grpc::ServerContext* context, const boat::v1::StreamMetricsRequest* request,
                             grpc::ServerWriter<boat::v1::MetricPoint>* writer) override;

 private:
  boat::core::EventBus& event_bus_;
  boat::core::SimStateMachine& sim_state_machine_;
  boat::core::PluginManager& plugin_manager_;
  boat::core::SignalRouter& signal_router_;
};

}  // namespace boat::ipc

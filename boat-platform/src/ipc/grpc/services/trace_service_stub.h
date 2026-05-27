#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/trace.grpc.pb.h"
#include "core/event/event_bus.h"
#include "core/plugin/plugin_manager.h"
#include "core/signal/signal_router.h"
#include "core/state/sim_state_machine.h"

namespace boat::ipc {

class TraceServiceStub final : public boat::v1::TraceService::Service {
 public:
  TraceServiceStub(boat::core::EventBus& event_bus, boat::core::SimStateMachine& sim_state_machine,
                   boat::core::PluginManager& plugin_manager, boat::core::SignalRouter& signal_router);

  grpc::Status GetTrace(grpc::ServerContext* context, const boat::v1::GetTraceRequest* request,
                        boat::v1::TraceResponse* response) override;
  grpc::Status ListTraces(grpc::ServerContext* context, const boat::v1::ListTracesRequest* request,
                          boat::v1::TraceResponse* response) override;
  grpc::Status StreamTrace(grpc::ServerContext* context, const boat::v1::StreamTraceRequest* request,
                           grpc::ServerWriter<boat::v1::TraceEvent>* writer) override;

 private:
  boat::core::EventBus& event_bus_;
  boat::core::SimStateMachine& sim_state_machine_;
  boat::core::PluginManager& plugin_manager_;
  boat::core::SignalRouter& signal_router_;
};

}  // namespace boat::ipc

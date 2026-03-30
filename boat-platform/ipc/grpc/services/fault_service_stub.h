#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/fault.grpc.pb.h"
#include "core/event/event_bus.h"
#include "core/plugin/plugin_manager.h"
#include "core/signal/signal_router.h"
#include "core/state/sim_state_machine.h"

namespace boat::ipc {

class FaultServiceStub final : public boat::v1::FaultService::Service {
 public:
  FaultServiceStub(boat::core::EventBus& event_bus, boat::core::SimStateMachine& sim_state_machine,
                   boat::core::PluginManager& plugin_manager, boat::core::SignalRouter& signal_router);

  grpc::Status InjectFault(grpc::ServerContext* context, const boat::v1::InjectFaultRequest* request,
                           boat::v1::InjectFaultResponse* response) override;
  grpc::Status ListFaults(grpc::ServerContext* context, const boat::v1::ListFaultsRequest* request,
                          boat::v1::ListFaultsResponse* response) override;

 private:
  boat::core::EventBus& event_bus_;
  boat::core::SimStateMachine& sim_state_machine_;
  boat::core::PluginManager& plugin_manager_;
  boat::core::SignalRouter& signal_router_;
};

}  // namespace boat::ipc

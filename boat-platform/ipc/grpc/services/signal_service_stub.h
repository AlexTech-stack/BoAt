#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/signal.grpc.pb.h"
#include "core/event/event_bus.h"
#include "core/plugin/plugin_manager.h"
#include "core/signal/signal_router.h"
#include "core/state/sim_state_machine.h"

namespace boat::ipc {

class SignalServiceStub final : public boat::v1::SignalService::Service {
 public:
  SignalServiceStub(boat::core::EventBus& event_bus, boat::core::SimStateMachine& sim_state_machine,
                    boat::core::PluginManager& plugin_manager, boat::core::SignalRouter& signal_router);

  grpc::Status InjectSignal(grpc::ServerContext* context, const boat::v1::InjectSignalRequest* request,
                            boat::v1::InjectSignalResponse* response) override;
  grpc::Status SubscribeSignals(grpc::ServerContext* context, const boat::v1::SubscribeSignalsRequest* request,
                                grpc::ServerWriter<boat::v1::SignalValue>* writer) override;
  grpc::Status GetSignalHistory(grpc::ServerContext* context, const boat::v1::GetSignalHistoryRequest* request,
                                boat::v1::SignalHistoryResponse* response) override;

 private:
  boat::core::EventBus& event_bus_;
  boat::core::SimStateMachine& sim_state_machine_;
  boat::core::PluginManager& plugin_manager_;
  boat::core::SignalRouter& signal_router_;
};

}  // namespace boat::ipc

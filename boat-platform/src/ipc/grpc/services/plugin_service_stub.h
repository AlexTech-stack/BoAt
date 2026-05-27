#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/plugin.grpc.pb.h"
#include "core/event/event_bus.h"
#include "core/plugin/plugin_manager.h"
#include "core/signal/signal_router.h"
#include "core/state/sim_state_machine.h"

namespace boat::ipc {

class PluginServiceStub final : public boat::v1::PluginService::Service {
 public:
  PluginServiceStub(boat::core::EventBus& event_bus, boat::core::SimStateMachine& sim_state_machine,
                    boat::core::PluginManager& plugin_manager, boat::core::SignalRouter& signal_router);

  grpc::Status RegisterPlugin(grpc::ServerContext* context, const boat::v1::RegisterPluginRequest* request,
                              boat::v1::PluginResponse* response) override;
  grpc::Status ListPlugins(grpc::ServerContext* context, const boat::v1::ListPluginsRequest* request,
                           boat::v1::ListPluginsResponse* response) override;
  grpc::Status GetPluginInfo(grpc::ServerContext* context, const boat::v1::GetPluginInfoRequest* request,
                             boat::v1::PluginResponse* response) override;
  grpc::Status UnloadPlugin(grpc::ServerContext* context, const boat::v1::UnloadPluginRequest* request,
                            boat::v1::UnloadPluginResponse* response) override;

 private:
  boat::core::EventBus& event_bus_;
  boat::core::SimStateMachine& sim_state_machine_;
  boat::core::PluginManager& plugin_manager_;
  boat::core::SignalRouter& signal_router_;
};

}  // namespace boat::ipc

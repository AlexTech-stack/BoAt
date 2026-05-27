#include "ipc/grpc/services/plugin_service_stub.h"

namespace boat::ipc {

PluginServiceStub::PluginServiceStub(boat::core::EventBus& event_bus, boat::core::SimStateMachine& sim_state_machine,
                                     boat::core::PluginManager& plugin_manager,
                                     boat::core::SignalRouter& signal_router)
    : event_bus_(event_bus),
      sim_state_machine_(sim_state_machine),
      plugin_manager_(plugin_manager),
      signal_router_(signal_router) {}

grpc::Status PluginServiceStub::RegisterPlugin(grpc::ServerContext*, const boat::v1::RegisterPluginRequest*,
                                               boat::v1::PluginResponse*) {
  return grpc::Status::OK;
}
grpc::Status PluginServiceStub::ListPlugins(grpc::ServerContext*, const boat::v1::ListPluginsRequest*,
                                            boat::v1::ListPluginsResponse*) {
  return grpc::Status::OK;
}
grpc::Status PluginServiceStub::GetPluginInfo(grpc::ServerContext*, const boat::v1::GetPluginInfoRequest*,
                                              boat::v1::PluginResponse*) {
  return grpc::Status::OK;
}
grpc::Status PluginServiceStub::UnloadPlugin(grpc::ServerContext*, const boat::v1::UnloadPluginRequest*,
                                             boat::v1::UnloadPluginResponse*) {
  return grpc::Status::OK;
}

}  // namespace boat::ipc

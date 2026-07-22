#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/node_plugin.grpc.pb.h"
#include "gateway_context.h"

namespace boat::gateway {

// Mirrors PluginServiceImpl's shape, but operates on ctx_.plugin_manager --
// the always-on "node" PluginManager (BOAT_NODE_PLUGINS), not the
// simulation-scoped one PluginServiceImpl uses. See node_plugin.proto.
class NodePluginServiceImpl final : public boat::v1::NodePluginService::Service {
 public:
  explicit NodePluginServiceImpl(GatewayContext& ctx);

  grpc::Status ListNodePlugins(grpc::ServerContext* context,
                               const boat::v1::ListNodePluginsRequest* request,
                               boat::v1::ListNodePluginsResponse* response) override;
  grpc::Status GetNodePluginInfo(grpc::ServerContext* context,
                                 const boat::v1::GetNodePluginInfoRequest* request,
                                 boat::v1::PluginResponse* response) override;
  grpc::Status UnloadNodePlugin(grpc::ServerContext* context,
                                const boat::v1::UnloadNodePluginRequest* request,
                                boat::v1::UnloadNodePluginResponse* response) override;

 private:
  GatewayContext& ctx_;
};

}  // namespace boat::gateway

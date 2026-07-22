#include "node_plugin_service_impl.h"

#include <algorithm>
#include <chrono>
#include <exception>
#include <string>

#include "rpc_audit_log.h"

namespace boat::gateway {
namespace {
std::size_t ParseToken(const std::string& token) {
  if (token.empty()) {
    return 0;
  }
  return static_cast<std::size_t>(std::stoull(token));
}

uint64_t NowNsNodePlugin() {
  return static_cast<uint64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::system_clock::now().time_since_epoch()).count());
}
}  // namespace

NodePluginServiceImpl::NodePluginServiceImpl(GatewayContext& ctx) : ctx_(ctx) {}

grpc::Status NodePluginServiceImpl::ListNodePlugins(
    grpc::ServerContext*, const boat::v1::ListNodePluginsRequest* request,
    boat::v1::ListNodePluginsResponse* response) {
  const auto plugins = ctx_.plugin_manager.List();
  const std::size_t offset = ParseToken(request->page().page_token());
  const std::size_t page_size = request->page().page_size() == 0 ? plugins.size() : request->page().page_size();
  const std::size_t end = std::min(plugins.size(), offset + page_size);
  for (std::size_t i = offset; i < end; ++i) {
    auto* plugin = response->add_plugins();
    plugin->set_plugin_id(plugins[i]);
    plugin->set_name(plugins[i]);
    plugin->set_version("unknown");
    plugin->set_loaded(true);
    plugin->set_config_json(ctx_.plugin_manager.GetConfigJson(plugins[i]));
  }
  response->mutable_page()->set_total_size(static_cast<std::uint32_t>(plugins.size()));
  if (end < plugins.size()) {
    response->mutable_page()->set_next_page_token(std::to_string(end));
  }
  return grpc::Status::OK;
}

grpc::Status NodePluginServiceImpl::GetNodePluginInfo(
    grpc::ServerContext*, const boat::v1::GetNodePluginInfoRequest* request,
    boat::v1::PluginResponse* response) {
  const auto plugins = ctx_.plugin_manager.List();
  const auto it = std::find(plugins.begin(), plugins.end(), request->plugin_id());
  if (it == plugins.end()) {
    return grpc::Status(grpc::StatusCode::NOT_FOUND, "node plugin not found");
  }
  auto* plugin = response->mutable_plugin();
  plugin->set_plugin_id(*it);
  plugin->set_name(*it);
  plugin->set_version("unknown");
  plugin->set_loaded(true);
  plugin->set_config_json(ctx_.plugin_manager.GetConfigJson(*it));
  return grpc::Status::OK;
}

grpc::Status NodePluginServiceImpl::UnloadNodePlugin(
    grpc::ServerContext* context, const boat::v1::UnloadNodePluginRequest* request,
    boat::v1::UnloadNodePluginResponse* response) {
  if (!request->confirm()) {
    return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                        "unloading a node plugin is immediate and gateway-wide, not scoped "
                        "to any simulation -- set confirm=true to proceed");
  }
  const auto plugins = ctx_.plugin_manager.List();
  if (std::find(plugins.begin(), plugins.end(), request->plugin_id()) == plugins.end()) {
    return grpc::Status(grpc::StatusCode::NOT_FOUND, "node plugin not found");
  }
  ctx_.plugin_manager.Unload(request->plugin_id());
  response->set_unloaded(true);

  {
    RpcEvent ev;
    ev.timestamp_ns = NowNsNodePlugin();
    ev.method     = "NodePluginService/UnloadNodePlugin";
    ev.peer       = context->peer();
    ev.event_type = "DATA";
    ev.call_type  = "UNARY";
    ev.summary    = "unloaded node plugin '" + request->plugin_id() + "'";
    ctx_.audit_log.Push(std::move(ev));
  }

  return grpc::Status::OK;
}

}  // namespace boat::gateway

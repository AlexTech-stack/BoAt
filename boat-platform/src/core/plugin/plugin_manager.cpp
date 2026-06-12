#include "plugin/plugin_manager.h"

#include <stdexcept>

#ifdef _WIN32
#include <windows.h>
#else
#include <dlfcn.h>
#endif

namespace boat::core {

void PluginManager::SetPublisher(SignalPublishFn fn) {
  publisher_fn_ = std::move(fn);
}

void PluginManager::SetCanPublisher(CanPublishFn fn) {
  can_publisher_fn_ = std::move(fn);
}

void PluginManager::SetEthPublisher(EthPublishFn fn) {
  eth_publisher_fn_ = std::move(fn);
}

void PluginManager::SetBusPublisher(BusPublishFn fn) {
  bus_publisher_fn_ = std::move(fn);
}

void PluginManager::SetPduPublisher(PduPublishFn fn) {
  pdu_publisher_fn_ = std::move(fn);
}

PluginHandle PluginManager::Load(const std::string& so_path, const std::string& config_json) {
#ifdef _WIN32
  (void)so_path;
  (void)config_json;
  throw std::runtime_error("Plugin loading via dlopen/dlsym is not supported on Windows");
#else
  void* dl_handle = dlopen(so_path.c_str(), RTLD_NOW | RTLD_LOCAL);
  if (dl_handle == nullptr) {
    throw std::runtime_error(dlerror());
  }

  auto create_fn = reinterpret_cast<boat_plugin_create_fn>(dlsym(dl_handle, "boat_plugin_create"));
  auto destroy_fn = reinterpret_cast<boat_plugin_destroy_fn>(dlsym(dl_handle, "boat_plugin_destroy"));
  auto abi_fn = reinterpret_cast<boat_plugin_abi_version_fn>(dlsym(dl_handle, "boat_plugin_abi_version"));
  if (create_fn == nullptr || destroy_fn == nullptr || abi_fn == nullptr) {
    dlclose(dl_handle);
    throw std::runtime_error("Missing required plugin symbols");
  }

  const std::uint32_t abi_version = abi_fn();
  if (abi_version != BOAT_PLUGIN_ABI_VERSION) {
    dlclose(dl_handle);
    throw std::runtime_error("Plugin ABI version mismatch");
  }

  BoatPlugin* plugin = create_fn();
  if (plugin == nullptr || plugin->vtable == nullptr || plugin->vtable->initialize == nullptr ||
      plugin->vtable->on_tick == nullptr || plugin->vtable->shutdown == nullptr) {
    if (plugin != nullptr) {
      destroy_fn(plugin);
    }
    dlclose(dl_handle);
    throw std::runtime_error("Invalid plugin instance");
  }

  if (plugin->vtable->initialize(plugin->ctx, config_json.c_str()) != 0) {
    destroy_fn(plugin);
    dlclose(dl_handle);
    throw std::runtime_error("Plugin initialize() failed");
  }

  // Wire the signal publisher if the plugin supports it.
  if (plugin->vtable->set_publisher != nullptr && publisher_fn_) {
    auto* fn_copy = new SignalPublishFn(publisher_fn_);
    plugin->vtable->set_publisher(
        plugin->ctx,
        [](void* pctx, const char* signal_id, uint64_t tick, double value) {
          (*static_cast<SignalPublishFn*>(pctx))(signal_id, tick, value);
        },
        fn_copy);
  }

  // Wire the CAN publisher if the plugin supports it.
  if (plugin->vtable->set_can_publisher != nullptr && can_publisher_fn_) {
    auto* fn_copy = new CanPublishFn(can_publisher_fn_);
    plugin->vtable->set_can_publisher(
        plugin->ctx,
        [](void* pctx, const BoatCanFrame* frame) {
          if (frame != nullptr) (*static_cast<CanPublishFn*>(pctx))(*frame);
        },
        fn_copy);
  }

  // Wire the Ethernet publisher if the plugin supports it.
  if (plugin->vtable->set_eth_publisher != nullptr && eth_publisher_fn_) {
    auto* fn_copy = new EthPublishFn(eth_publisher_fn_);
    plugin->vtable->set_eth_publisher(
        plugin->ctx,
        [](void* pctx, const BoatEthFrame* frame) {
          if (frame != nullptr) (*static_cast<EthPublishFn*>(pctx))(*frame);
        },
        fn_copy);
  }

  // Wire the bus-signal publisher if the plugin supports it.
  if (plugin->vtable->set_bus_publisher != nullptr && bus_publisher_fn_) {
    auto* fn_copy = new BusPublishFn(bus_publisher_fn_);
    plugin->vtable->set_bus_publisher(
        plugin->ctx,
        [](void* pctx, const char* name, double value) {
          (*static_cast<BusPublishFn*>(pctx))(name, value);
        },
        fn_copy);
  }

  // Wire the PDU publisher if the plugin supports it.
  if (plugin->vtable->set_pdu_publisher != nullptr && pdu_publisher_fn_) {
    auto* fn_copy = new PduPublishFn(pdu_publisher_fn_);
    plugin->vtable->set_pdu_publisher(
        plugin->ctx,
        [](void* pctx, const BoatPduFrame* frame) {
          if (frame != nullptr) (*static_cast<PduPublishFn*>(pctx))(*frame);
        },
        fn_copy);
  }

  PluginHandle handle{dl_handle, plugin, so_path, abi_version, destroy_fn};
  plugins_[handle.name] = handle;
  return handle;
#endif
}

void PluginManager::Unload(const std::string& name) {
  auto it = plugins_.find(name);
  if (it == plugins_.end()) {
    return;
  }

#ifndef _WIN32
  if (it->second.plugin != nullptr) {
    it->second.destroy_fn(it->second.plugin);
  }
  if (it->second.dl_handle != nullptr) {
    dlclose(it->second.dl_handle);
  }
#endif
  plugins_.erase(it);
}

void PluginManager::TickAll(std::uint64_t tick) {
  for (auto& [name, handle] : plugins_) {
    (void)name;
    handle.plugin->vtable->on_tick(handle.plugin->ctx, tick);
  }
}

void PluginManager::DispatchCanFrame(const BoatCanFrame& frame, const std::string& iface) {
  for (auto& [name, handle] : plugins_) {
    (void)name;
    if (handle.plugin->vtable->on_can_frame != nullptr) {
      handle.plugin->vtable->on_can_frame(handle.plugin->ctx, &frame, iface.c_str());
    }
  }
}

void PluginManager::DispatchEthFrame(const BoatEthFrame& frame, const std::string& iface) {
  for (auto& [name, handle] : plugins_) {
    (void)name;
    if (handle.plugin->vtable->on_eth_frame != nullptr) {
      handle.plugin->vtable->on_eth_frame(handle.plugin->ctx, &frame, iface.c_str());
    }
  }
}

void PluginManager::ShutdownAll() {
  std::vector<std::string> names;
  names.reserve(plugins_.size());
  for (const auto& [name, handle] : plugins_) {
    (void)handle;
    names.push_back(name);
  }
  for (const auto& name : names) {
    Unload(name);
  }
}

std::vector<std::string> PluginManager::List() const {
  std::vector<std::string> names;
  names.reserve(plugins_.size());
  for (const auto& [name, handle] : plugins_) {
    (void)handle;
    names.push_back(name);
  }
  return names;
}

}  // namespace boat::core

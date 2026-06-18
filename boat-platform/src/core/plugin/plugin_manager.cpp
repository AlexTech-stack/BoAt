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

  PluginHandle handle{dl_handle, plugin, so_path, abi_version, destroy_fn, {}};

  // Wire the signal publisher if the plugin supports it.
  if (plugin->vtable->set_publisher != nullptr && publisher_fn_) {
    auto fn_shared = std::make_shared<SignalPublishFn>(publisher_fn_);
    plugin->vtable->set_publisher(
        plugin->ctx,
        [](void* pctx, const char* signal_id, uint64_t tick, double value) {
          (*static_cast<SignalPublishFn*>(pctx))(signal_id, tick, value);
        },
        fn_shared.get());
    handle.publisher_contexts.push_back(std::static_pointer_cast<void>(fn_shared));
  }

  // Wire the CAN publisher if the plugin supports it.
  if (plugin->vtable->set_can_publisher != nullptr && can_publisher_fn_) {
    // Parse the plugin's configured interface from its config JSON.
    std::string plugin_iface;
    auto key_pos = config_json.find("\"iface\"");
    if (key_pos != std::string::npos) {
      auto val_pos = config_json.find('"', key_pos + 7);
      if (val_pos != std::string::npos) {
        auto end_pos = config_json.find('"', val_pos + 1);
        if (end_pos != std::string::npos) {
          plugin_iface = config_json.substr(val_pos + 1, end_pos - val_pos - 1);
        }
      }
    }
    struct CanPublishBinding {
      CanPublishFn fn;
      std::string  iface;
    };
    auto binding = std::make_shared<CanPublishBinding>(
        CanPublishBinding{can_publisher_fn_, std::move(plugin_iface)});
    plugin->vtable->set_can_publisher(
        plugin->ctx,
        [](void* pctx, const BoatCanFrame* frame) {
          if (frame == nullptr) return;
          auto* b = static_cast<CanPublishBinding*>(pctx);
          b->fn(*frame, b->iface);
        },
        static_cast<void*>(binding.get()));
    handle.publisher_contexts.push_back(std::static_pointer_cast<void>(binding));
  }

  // Wire the Ethernet publisher if the plugin supports it.
  if (plugin->vtable->set_eth_publisher != nullptr && eth_publisher_fn_) {
    auto fn_shared = std::make_shared<EthPublishFn>(eth_publisher_fn_);
    plugin->vtable->set_eth_publisher(
        plugin->ctx,
        [](void* pctx, const BoatEthFrame* frame) {
          if (frame != nullptr) (*static_cast<EthPublishFn*>(pctx))(*frame);
        },
        fn_shared.get());
    handle.publisher_contexts.push_back(std::static_pointer_cast<void>(fn_shared));
  }

  // Wire the bus-signal publisher if the plugin supports it.
  if (plugin->vtable->set_bus_publisher != nullptr && bus_publisher_fn_) {
    auto fn_shared = std::make_shared<BusPublishFn>(bus_publisher_fn_);
    plugin->vtable->set_bus_publisher(
        plugin->ctx,
        [](void* pctx, const char* name, double value) {
          (*static_cast<BusPublishFn*>(pctx))(name, value);
        },
        fn_shared.get());
    handle.publisher_contexts.push_back(std::static_pointer_cast<void>(fn_shared));
  }

  // Wire the PDU publisher if the plugin supports it.
  if (plugin->vtable->set_pdu_publisher != nullptr && pdu_publisher_fn_) {
    auto fn_shared = std::make_shared<PduPublishFn>(pdu_publisher_fn_);
    plugin->vtable->set_pdu_publisher(
        plugin->ctx,
        [](void* pctx, const BoatPduFrame* frame) {
          if (frame != nullptr) (*static_cast<PduPublishFn*>(pctx))(*frame);
        },
        fn_shared.get());
    handle.publisher_contexts.push_back(std::static_pointer_cast<void>(fn_shared));
  }
  {
    std::lock_guard<std::mutex> lock(mutex_);
    plugins_[handle.name] = handle;
  }
  return handle;
#endif
}

void PluginManager::Unload(const std::string& name) {
  PluginHandle handle;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = plugins_.find(name);
    if (it == plugins_.end()) {
      return;
    }
    handle = std::move(it->second);
    plugins_.erase(it);
  }

#ifndef _WIN32
  if (handle.plugin != nullptr) {
    handle.destroy_fn(handle.plugin);
  }
  if (handle.dl_handle != nullptr) {
    dlclose(handle.dl_handle);
  }
  // publisher_contexts freed automatically when handle goes out of scope
#endif
}

void PluginManager::TickAll(std::uint64_t tick) {
  std::vector<BoatPlugin*> snapshot;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    snapshot.reserve(plugins_.size());
    for (auto& [name, handle] : plugins_) {
      (void)name;
      snapshot.push_back(handle.plugin);
    }
  }
  for (auto* plugin : snapshot) {
    plugin->vtable->on_tick(plugin->ctx, tick);
  }
}

void PluginManager::DispatchCanFrame(const BoatCanFrame& frame, const std::string& iface) {
  std::vector<BoatPlugin*> snapshot;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    snapshot.reserve(plugins_.size());
    for (auto& [name, handle] : plugins_) {
      (void)name;
      snapshot.push_back(handle.plugin);
    }
  }
  for (auto* plugin : snapshot) {
    if (plugin->vtable->on_can_frame != nullptr) {
      plugin->vtable->on_can_frame(plugin->ctx, &frame, iface.c_str());
    }
  }
}

void PluginManager::DispatchEthFrame(const BoatEthFrame& frame, const std::string& iface) {
  std::vector<BoatPlugin*> snapshot;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    snapshot.reserve(plugins_.size());
    for (auto& [name, handle] : plugins_) {
      (void)name;
      snapshot.push_back(handle.plugin);
    }
  }
  for (auto* plugin : snapshot) {
    if (plugin->vtable->on_eth_frame != nullptr) {
      plugin->vtable->on_eth_frame(plugin->ctx, &frame, iface.c_str());
    }
  }
}

void PluginManager::ShutdownAll() {
  std::vector<std::string> names;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    names.reserve(plugins_.size());
    for (const auto& [name, handle] : plugins_) {
      (void)handle;
      names.push_back(name);
    }
  }
  for (const auto& name : names) {
    Unload(name);
  }
}

std::vector<std::string> PluginManager::List() const {
  std::lock_guard<std::mutex> lock(mutex_);
  std::vector<std::string> names;
  names.reserve(plugins_.size());
  for (const auto& [name, handle] : plugins_) {
    (void)handle;
    names.push_back(name);
  }
  return names;
}

}  // namespace boat::core

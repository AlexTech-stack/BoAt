#include "plugin/plugin_manager.h"

#include <cstring>
#include <stdexcept>
#include <string>

#ifdef _WIN32
#include <windows.h>
#else
#include <dlfcn.h>
#endif

namespace boat::core {

void PluginManager::SetPublisher(SignalPublishFn fn) {
  publisher_fn_ = std::move(fn);
}

void PluginManager::SetBusPublisher(BusPublishFn fn) {
  bus_publisher_fn_ = std::move(fn);
}

void PluginManager::SetPduPublisher(PduPublishFn fn) {
  pdu_publisher_fn_ = std::move(fn);
}

void PluginManager::SetFramePublisher(FramePublishFn fn) {
  frame_publisher_fn_ = std::move(fn);
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
    throw std::runtime_error(
        "Plugin ABI version mismatch (" + std::to_string(abi_version) +
        " != " + std::to_string(BOAT_PLUGIN_ABI_VERSION) + ")");
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

  // Wire signal publisher.
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

  // Wire bus-signal publisher.
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

  // Wire PDU publisher.
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

  // v8: Wire unified frame publisher.
  if (plugin->vtable->set_frame_publisher != nullptr && frame_publisher_fn_) {
    auto fn_shared = std::make_shared<FramePublishFn>(frame_publisher_fn_);
    plugin->vtable->set_frame_publisher(
        plugin->ctx,
        [](void* pctx, const BoatFrame* frame) {
          if (frame != nullptr) (*static_cast<FramePublishFn*>(pctx))(*frame);
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
    if (it == plugins_.end()) return;
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

void PluginManager::DispatchFrame(const BoatFrame& frame) {
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
    if (plugin->vtable->on_frame != nullptr) {
      plugin->vtable->on_frame(plugin->ctx, &frame);
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

void PluginManager::RegisterService(const std::string& name, void* service) {
  std::lock_guard<std::mutex> lock(services_mutex_);
  services_[name] = service;
}

void* PluginManager::FindService(const std::string& name) const {
  std::lock_guard<std::mutex> lock(services_mutex_);
  auto it = services_.find(name);
  return (it != services_.end()) ? it->second : nullptr;
}

}  // namespace boat::core

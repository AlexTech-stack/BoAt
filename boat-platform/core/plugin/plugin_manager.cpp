#include "plugin/plugin_manager.h"

#include <stdexcept>

#ifdef _WIN32
#include <windows.h>
#else
#include <dlfcn.h>
#endif

namespace boat::core {

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
    it->second.plugin->vtable->shutdown(it->second.plugin->ctx);
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

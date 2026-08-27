// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

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

namespace {

constexpr std::uint32_t kAllBusMask = 0xFFFFFFFFu;

// Parse a plugin's declared_buses() string (a JSON array of bus-type names,
// e.g. ["can","eth"]) into a bitmask of BOAT_BUS_* values. A CAN handler is
// assumed to accept both classic and FD frames. Unrecognized / empty input is
// treated as "accept all" so a plugin is never silently starved of frames.
std::uint32_t ParseDeclaredBusMask(const char* decl) {
  if (decl == nullptr) return kAllBusMask;
  const std::string s(decl);
  const auto has = [&](const char* tok) {
    return s.find(tok) != std::string::npos;
  };
  std::uint32_t mask = 0;
  if (has("\"canfd\"")) mask |= (1u << BOAT_BUS_CANFD);
  if (has("\"can\""))   mask |= (1u << BOAT_BUS_CAN) | (1u << BOAT_BUS_CANFD);
  if (has("\"eth\""))   mask |= (1u << BOAT_BUS_ETHERNET);
  if (has("\"tcp\""))   mask |= (1u << BOAT_BUS_TCP);
  if (has("\"pdu\""))   mask |= (1u << BOAT_BUS_PDU);
  return mask == 0 ? kAllBusMask : mask;
}

// Extract the "iface" value from a plugin's config JSON (e.g.
// {"iface":"vcan0"}), using the same manual substring-scan style
// ParseDeclaredBusMask already uses rather than pulling in a JSON library
// for this one field. Returns "" if absent/malformed.
std::string ExtractConfigIface(const std::string& config_json) {
  const char* key = "\"iface\"";
  const auto key_pos = config_json.find(key);
  if (key_pos == std::string::npos) return "";
  auto pos = config_json.find('"', key_pos + std::strlen(key));
  if (pos == std::string::npos) return "";
  ++pos;
  const auto end = config_json.find('"', pos);
  if (end == std::string::npos) return "";
  return config_json.substr(pos, end - pos);
}

// Key a loaded plugin by so_path plus (if present) its config's iface, so
// two instances of the same .so with different ifaces (e.g. CanTp on
// vcan0 and vcan1) get distinct entries in plugins_ instead of silently
// overwriting each other. Two instances with a genuinely identical iface
// still collide on this key -- intentional: that's a real bus-level
// conflict (both instances would process the same wire traffic), not
// just a bookkeeping one, so rejecting it here is correct. Falls back to
// bare so_path when no "iface" key is present, preserving prior behavior
// for plugins that don't use per-instance config.
std::string PluginKey(const std::string& so_path, const std::string& config_json) {
  const std::string iface = ExtractConfigIface(config_json);
  return iface.empty() ? so_path : (so_path + "?iface=" + iface);
}

}  // namespace

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

  PluginHandle handle{dl_handle, plugin, PluginKey(so_path, config_json),
                      abi_version, destroy_fn, config_json, {}};

  // Cache the plugin's declared bus types so DispatchFrame can pre-filter
  // instead of calling on_frame for every plugin on every frame.
  if (plugin->vtable->declared_buses != nullptr) {
    handle.declared_bus_mask =
        ParseDeclaredBusMask(plugin->vtable->declared_buses(plugin->ctx));
  }

  // Optional: plugin exposes a named C++ service pointer (e.g. pdu_router's
  // IPduRouter) for gRPC service implementations to look up via
  // FindService(). Independent of the vtable ABI -- both symbols are
  // simply absent for plugins that don't need this.
  auto service_name_fn = reinterpret_cast<boat_plugin_service_name_fn>(
      dlsym(dl_handle, "boat_plugin_service_name"));
  auto service_ptr_fn = reinterpret_cast<boat_plugin_service_ptr_fn>(
      dlsym(dl_handle, "boat_plugin_service_ptr"));
  if (service_name_fn != nullptr && service_ptr_fn != nullptr) {
    const char* service_name = service_name_fn(plugin->ctx);
    void* service_ptr = service_ptr_fn(plugin->ctx);
    if (service_name != nullptr && service_name[0] != '\0' && service_ptr != nullptr) {
      RegisterService(service_name, service_ptr);
      handle.registered_services.emplace_back(service_name, service_ptr);
    }
  }

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
  // Remove any services this plugin registered before destroying it --
  // otherwise FindService() would keep handing out a dangling pointer.
  // Compare-and-erase: only remove services_[name] if it still points at
  // *this* handle's registered pointer. A newer plugin instance may have
  // since registered the same service name (overwriting this handle's
  // entry) -- erasing unconditionally by name would delete that newer,
  // still-live registration instead of this handle's stale one.
  if (!handle.registered_services.empty()) {
    std::lock_guard<std::mutex> lock(services_mutex_);
    for (const auto& [service_name, service_ptr] : handle.registered_services) {
      auto it = services_.find(service_name);
      if (it != services_.end() && it->second == service_ptr) {
        services_.erase(it);
      }
    }
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
  struct Target {
    BoatPlugin* plugin;
    std::uint32_t bus_mask;
  };
  std::vector<Target> snapshot;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    snapshot.reserve(plugins_.size());
    for (auto& [name, handle] : plugins_) {
      (void)name;
      snapshot.push_back({handle.plugin, handle.declared_bus_mask});
    }
  }
  const std::uint32_t bus_bit =
      (frame.bus_type < 32) ? (1u << frame.bus_type) : 0u;
  for (auto& t : snapshot) {
    // Skip plugins that didn't declare this bus type — avoids O(N) fan-out.
    if ((t.bus_mask & bus_bit) == 0) continue;
    if (t.plugin->vtable->on_frame != nullptr) {
      t.plugin->vtable->on_frame(t.plugin->ctx, &frame);
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

std::string PluginManager::GetConfigJson(const std::string& name) const {
  std::lock_guard<std::mutex> lock(mutex_);
  auto it = plugins_.find(name);
  return (it != plugins_.end()) ? it->second.config_json : std::string();
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

std::vector<std::string> PluginManager::ListServices() const {
  std::lock_guard<std::mutex> lock(services_mutex_);
  std::vector<std::string> names;
  names.reserve(services_.size());
  for (const auto& [name, service] : services_) {
    (void)service;
    names.push_back(name);
  }
  return names;
}

}  // namespace boat::core

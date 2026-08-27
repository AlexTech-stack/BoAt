// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

#include "boat/plugin.h"

namespace boat::core {

struct PluginHandle {
  void* dl_handle;
  BoatPlugin* plugin;
  std::string name;
  std::uint32_t abi_version;
  boat_plugin_destroy_fn destroy_fn;
  // The config JSON this plugin instance was loaded with (e.g.
  // {"iface":"vcan0"}), so introspection (GetPluginInfo/ListPlugins) can
  // show what a loaded instance actually is.
  std::string config_json;
  std::vector<std::shared_ptr<void>> publisher_contexts;
  // Bitmask of bus types this plugin handles (bit N = BOAT_BUS_* value N),
  // parsed once from declared_buses() at load. All-ones = accept every bus
  // type (plugin declared nothing). Used by DispatchFrame to pre-filter.
  std::uint32_t declared_bus_mask = 0xFFFFFFFFu;
  // Services this plugin registered via the optional
  // boat_plugin_service_name/boat_plugin_service_ptr symbols, so Unload()
  // can remove them and avoid leaving a dangling pointer in services_.
  // Stores the registered pointer alongside each name so Unload() can
  // compare-and-erase -- a later plugin instance may have since registered
  // the same service name (overwriting this handle's entry in services_),
  // and unconditionally erasing by name alone would delete that newer,
  // still-live registration instead of this handle's stale one.
  std::vector<std::pair<std::string, void*>> registered_services;
};

/* Signature for routing a signal value from a plugin. */
using SignalPublishFn =
    std::function<void(const char* signal_id, uint64_t tick, double value)>;

/* Signature for publishing a named value to the always-on signal bus. */
using BusPublishFn = std::function<void(const char* name, double value)>;

/* Signature for delivering a PDU frame from a plugin into the frame bus. */
using PduPublishFn = std::function<void(const BoatPduFrame& frame)>;

/* v8: Signature for delivering a unified BoatFrame from a plugin to the bus. */
using FramePublishFn = std::function<void(const BoatFrame& frame)>;

class PluginManager {
 public:
  void SetPublisher(SignalPublishFn fn);
  void SetBusPublisher(BusPublishFn fn);
  void SetPduPublisher(PduPublishFn fn);
  void SetFramePublisher(FramePublishFn fn);

  PluginHandle Load(const std::string& so_path, const std::string& config_json);
  void Unload(const std::string& name);
  void TickAll(std::uint64_t tick);

  /* v8: Deliver a unified BoatFrame to every plugin with on_frame. */
  void DispatchFrame(const BoatFrame& frame);

  void ShutdownAll();
  [[nodiscard]] std::vector<std::string> List() const;
  /* The config JSON a loaded plugin was Load()'d with, or "" if name isn't
     currently loaded. Lets introspection (GetPluginInfo/ListPlugins) show
     what a loaded instance actually is (e.g. which iface) without exposing
     the full PluginHandle. */
  [[nodiscard]] std::string GetConfigJson(const std::string& name) const;

  /* Service provider registry */
  void RegisterService(const std::string& name, void* service);
  [[nodiscard]] void* FindService(const std::string& name) const;
  /* All currently-registered service names (e.g. "can_tp:vcan0"). Lets
     callers prefix-scan for "the set of loaded instances of plugin X"
     without a dedicated enumeration API per plugin type. */
  [[nodiscard]] std::vector<std::string> ListServices() const;

 private:
  mutable std::mutex mutex_;
  std::map<std::string, PluginHandle> plugins_;
  SignalPublishFn publisher_fn_;
  BusPublishFn bus_publisher_fn_;
  PduPublishFn pdu_publisher_fn_;
  FramePublishFn frame_publisher_fn_;

  mutable std::mutex services_mutex_;
  std::map<std::string, void*> services_;
};

}  // namespace boat::core

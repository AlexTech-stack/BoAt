// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <condition_variable>
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

class PluginManager;

/* RAII handle to a service pointer exported by a plugin (see FindService).
 *
 * Holding one blocks Unload() from running the plugin's destroy_fn and
 * dlclose until it is released, which is what stops a caller from executing
 * inside an unmapped .so. Because it is returned by value, the natural
 * call shape is already safe:
 *
 *     GetRouter()->SendPdu(id, payload);   // guard lives to end of statement
 *
 * Acquire and release one on the same thread. Unload() distinguishes "wait
 * for another thread's callback" from "I am inside a callback myself" with a
 * per-thread counter, and handing a live ref to a different thread would
 * confuse that bookkeeping. In practice they are function locals.
 *
 * Keep them SHORT-LIVED. A ServiceRef held for the duration of a streaming
 * RPC would block Unload for as long as the stream is open, trading a
 * use-after-free for a hang. Long-lived callers must re-acquire per use and
 * treat a null ref as "the plugin went away", rather than caching the raw
 * pointer across iterations.
 */
template <typename T>
class ServiceRef {
 public:
  ServiceRef() = default;
  ~ServiceRef() { Release(); }

  ServiceRef(ServiceRef&& other) noexcept : mgr_(other.mgr_), ptr_(other.ptr_) {
    other.mgr_ = nullptr;
    other.ptr_ = nullptr;
  }
  ServiceRef& operator=(ServiceRef&& other) noexcept {
    if (this != &other) {
      Release();
      mgr_ = other.mgr_;
      ptr_ = other.ptr_;
      other.mgr_ = nullptr;
      other.ptr_ = nullptr;
    }
    return *this;
  }
  ServiceRef(const ServiceRef&) = delete;
  ServiceRef& operator=(const ServiceRef&) = delete;

  explicit operator bool() const noexcept { return ptr_ != nullptr; }
  T* operator->() const noexcept { return ptr_; }
  // Deliberately no operator*: it would be ill-formed for ServiceRef<void>,
  // which is the natural type for "pin this plugin, I am not calling into it".
  [[nodiscard]] T* get() const noexcept { return ptr_; }

 private:
  friend class PluginManager;
  ServiceRef(PluginManager* mgr, T* ptr) : mgr_(mgr), ptr_(ptr) {}
  void Release() noexcept;

  PluginManager* mgr_ = nullptr;
  T* ptr_ = nullptr;
};

class PluginManager {
 public:
  void SetPublisher(SignalPublishFn fn);
  void SetBusPublisher(BusPublishFn fn);
  void SetPduPublisher(PduPublishFn fn);
  void SetFramePublisher(FramePublishFn fn);

  /* v9: the clock plugins read instead of calling a system clock. Set this
     before Load() -- it is wired into each plugin as it loads. */
  using TimeSourceFn = std::function<std::uint64_t()>;
  void SetTimeSource(TimeSourceFn fn);

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
  /* Raw lookup with NO lifetime tie: the returned pointer is only valid
     while nothing unloads the plugin that exported it. Safe in
     single-threaded contexts (tests, startup wiring). Concurrent callers --
     anything reachable from a gRPC handler -- must use AcquireService()
     instead, which holds the plugin alive for as long as the ref lives. */
  [[nodiscard]] void* FindService(const std::string& name) const;

  /* Look a service up and pin its plugin against concurrent Unload for as
     long as the returned ref is alive. Returns an empty ref (false) when no
     such service is registered. See ServiceRef for the lifetime rules. */
  template <typename T>
  [[nodiscard]] ServiceRef<T> AcquireService(const std::string& name);
  /* All currently-registered service names (e.g. "can_tp:vcan0"). Lets
     callers prefix-scan for "the set of loaded instances of plugin X"
     without a dedicated enumeration API per plugin type. */
  [[nodiscard]] std::vector<std::string> ListServices() const;

 private:
  template <typename> friend class ServiceRef;
  class BusyRelease;

  /* Busy-count bookkeeping. busy_count_ is the number of plugin callbacks
     currently in flight plus the number of live ServiceRefs. Unload() will
     not destroy a plugin while it is non-zero, which is how the host honors
     plugin.h's "no concurrent callbacks after return" for shutdown().
     AcquireBusyLocked() is for callers already holding mutex_ (the dispatch
     snapshot, which must pin atomically with the snapshot itself). */
  void AcquireBusy();
  void AcquireBusyLocked();
  void ReleaseBusy() noexcept;
  static void DestroyHandle(PluginHandle& handle) noexcept;

  mutable std::mutex mutex_;
  std::map<std::string, PluginHandle> plugins_;
  std::condition_variable busy_cv_;
  int busy_count_ = 0;                        // guarded by mutex_
  /* Handles whose destruction had to be deferred because the thread calling
     Unload() was itself inside a plugin callback (waiting would deadlock on
     its own reference). Drained by whoever drops the last reference. */
  std::vector<PluginHandle> pending_destroy_;  // guarded by mutex_
  SignalPublishFn publisher_fn_;
  BusPublishFn bus_publisher_fn_;
  PduPublishFn pdu_publisher_fn_;
  FramePublishFn frame_publisher_fn_;
  TimeSourceFn   time_source_fn_;

  mutable std::mutex services_mutex_;
  std::map<std::string, void*> services_;
};

template <typename T>
void ServiceRef<T>::Release() noexcept {
  if (mgr_ != nullptr) {
    mgr_->ReleaseBusy();
    mgr_ = nullptr;
  }
  ptr_ = nullptr;
}

template <typename T>
ServiceRef<T> PluginManager::AcquireService(const std::string& name) {
  // Pin first, then look up: if an Unload is in progress it has already
  // erased the service entry before waiting on busy_count_, so the lookup
  // below correctly misses rather than handing back a doomed pointer.
  AcquireBusy();
  void* raw = FindService(name);
  if (raw == nullptr) {
    ReleaseBusy();
    return ServiceRef<T>();
  }
  return ServiceRef<T>(this, static_cast<T*>(raw));
}

}  // namespace boat::core

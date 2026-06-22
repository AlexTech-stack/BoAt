#include "ethernet_bus_registry.h"

#include <chrono>
#include <cstdio>
#include <utility>

namespace boat::hil {

EthernetBusRegistry::~EthernetBusRegistry() {
  StopAll();
}

bool EthernetBusRegistry::Add(const std::string& iface,
                               std::unique_ptr<IEthernetDriver> driver) {
  if (!driver->Open()) {
    return false;
  }

  std::lock_guard<std::mutex> lock(ifaces_mutex_);
  auto& entry   = ifaces_[iface];
  entry.iface   = iface;
  entry.driver  = std::move(driver);
  entry.running.store(true);

  // Capture raw pointer — safe because the entry lives in the map for the
  // lifetime of the registry, and StopAll() joins before destroying entries.
  IEthernetDriver* drv_ptr = entry.driver.get();

  entry.rx_thread = std::thread([this, iface, drv_ptr, &entry]() {
    while (entry.running.load()) {
      EthernetFrame frame;
      if (drv_ptr->ReadFrame(frame)) {
        if (frame.timestamp_ns == 0) {
          frame.timestamp_ns = static_cast<uint64_t>(
              std::chrono::duration_cast<std::chrono::nanoseconds>(
                  std::chrono::system_clock::now().time_since_epoch())
                  .count());
        }
        DispatchRx(frame, iface);
      }
    }
  });

  return true;
}

bool EthernetBusRegistry::SendFrame(const std::string& iface,
                                    const EthernetFrame& frame) {
  bool written = false;
  {
    std::lock_guard<std::mutex> lock(ifaces_mutex_);
    const auto it = ifaces_.find(iface);
    if (it == ifaces_.end()) {
      std::fprintf(stderr, "[EthRegistry] SendFrame: iface '%s' not registered\n",
                   iface.c_str());
      return false;
    }
    written = it->second.driver->WriteFrame(frame);
  }
  if (!written) {
    std::fprintf(stderr, "[EthRegistry] WriteFrame failed on '%s'\n", iface.c_str());
  }
  // Always dispatch locally so gRPC subscribers receive the frame.
  // Virtual drivers (UDP multicast) have IP_MULTICAST_LOOP=1, so the rx_thread
  // also picks up the frame — gRPC subscribers may receive it twice.
  DispatchRx(frame, iface);
  return written;
}

void EthernetBusRegistry::SendFrameAll(const EthernetFrame& frame) {
  // Collect iface names while holding the lock, then dispatch without it.
  std::vector<std::string> dispatched_ifaces;
  {
    std::lock_guard<std::mutex> lock(ifaces_mutex_);
    for (auto& [name, entry] : ifaces_) {
      entry.driver->WriteFrame(frame);
      dispatched_ifaces.push_back(name);
    }
  }
  for (const auto& iface : dispatched_ifaces) {
    DispatchRx(frame, iface);
  }
}

EthernetBusRegistry::RxCallbackId EthernetBusRegistry::Subscribe(
    const std::string& iface_filter,
    uint32_t           ethertype_filter,
    RxCallback         cb) {
  std::lock_guard<std::mutex> lock(subs_mutex_);
  const RxCallbackId id = next_id_++;
  subscriptions_[id] = Subscription{iface_filter, ethertype_filter,
                                    std::move(cb)};
  return id;
}

void EthernetBusRegistry::Unsubscribe(RxCallbackId id) {
  std::lock_guard<std::mutex> lock(subs_mutex_);
  subscriptions_.erase(id);
}

std::vector<std::string> EthernetBusRegistry::Interfaces() const {
  std::lock_guard<std::mutex> lock(ifaces_mutex_);
  std::vector<std::string> names;
  names.reserve(ifaces_.size());
  for (const auto& [name, _] : ifaces_) {
    names.push_back(name);
  }
  return names;
}

bool EthernetBusRegistry::Has(const std::string& iface) const {
  std::lock_guard<std::mutex> lock(ifaces_mutex_);
  return ifaces_.find(iface) != ifaces_.end();
}

void EthernetBusRegistry::StopAll() {
  std::lock_guard<std::mutex> lock(ifaces_mutex_);
  for (auto& [name, entry] : ifaces_) {
    (void)name;
    entry.running.store(false);
    entry.driver->Close();
    if (entry.rx_thread.joinable()) {
      entry.rx_thread.join();
    }
  }
}

void EthernetBusRegistry::DispatchRx(const EthernetFrame& frame,
                                     const std::string& iface) {
  // Snapshot to avoid holding the lock during callbacks.
  std::vector<RxCallback> to_call;
  {
    std::lock_guard<std::mutex> lock(subs_mutex_);
    for (const auto& [id, sub] : subscriptions_) {
      (void)id;
      if (!sub.iface_filter.empty() && sub.iface_filter != iface) {
        continue;
      }
      if (sub.ethertype_filter != 0 &&
          sub.ethertype_filter != frame.ethertype) {
        continue;
      }
      to_call.push_back(sub.cb);
    }
  }
  for (const auto& cb : to_call) {
    cb(frame, iface);
  }
}

}  // namespace boat::hil

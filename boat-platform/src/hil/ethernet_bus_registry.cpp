// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "ethernet_bus_registry.h"

#include <boat/plugin.h>

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
  entry.driver  = std::shared_ptr<IEthernetDriver>(std::move(driver));
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

namespace {

// Matches the RX fallback just above (Add()'s rx_thread) -- wall-clock
// nanoseconds since epoch. Used only when the producer supplied no timestamp;
// a replay frame already carries a trace timestamp and preserving it keeps the
// self-sent echo reproducible rather than wall-clock dependent.
std::uint64_t NowNs() {
  return static_cast<std::uint64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::system_clock::now().time_since_epoch())
          .count());
}

}  // namespace

bool EthernetBusRegistry::SendFrame(const std::string& iface,
                                    const EthernetFrame& frame) {
  std::shared_ptr<IEthernetDriver> driver;
  {
    std::lock_guard<std::mutex> lock(ifaces_mutex_);
    const auto it = ifaces_.find(iface);
    if (it == ifaces_.end()) {
      std::fprintf(stderr, "[EthRegistry] SendFrame: iface '%s' not registered\n",
                   iface.c_str());
      return false;
    }
    // Snapshot the driver, then release ifaces_mutex_ before sendto().
    driver = it->second.driver;
  }

  if (!driver->WriteFrame(frame)) {
    std::fprintf(stderr, "[EthRegistry] WriteFrame failed on '%s'\n", iface.c_str());
    return false;
  }

  // The local echo is the loopback path, not a second transmission attempt:
  // deliver it only after the wire accepted the frame.
  EthernetFrame local = frame;
  local.flags |= BOAT_ETH_FLAG_SELF_SENT;  // single loopback-prevention marker
  if (local.timestamp_ns == 0) {
    local.timestamp_ns = NowNs();
  }
  DispatchRx(local, iface);
  return true;
}

bool EthernetBusRegistry::SendFrameAll(const EthernetFrame& frame) {
  // Snapshot the drivers while holding the lock, then write without it.
  std::vector<std::pair<std::string, std::shared_ptr<IEthernetDriver>>> drivers;
  {
    std::lock_guard<std::mutex> lock(ifaces_mutex_);
    drivers.reserve(ifaces_.size());
    for (auto& [name, entry] : ifaces_) {
      drivers.emplace_back(name, entry.driver);
    }
  }

  if (drivers.empty()) {
    return false;
  }

  bool all_written = true;
  EthernetFrame local = frame;
  local.flags |= BOAT_ETH_FLAG_SELF_SENT;  // single loopback-prevention marker
  if (local.timestamp_ns == 0) {
    local.timestamp_ns = NowNs();
  }
  for (const auto& [iface, driver] : drivers) {
    if (!driver->WriteFrame(frame)) {
      std::fprintf(stderr, "[EthRegistry] WriteFrame failed on '%s'\n", iface.c_str());
      all_written = false;
      continue;
    }
    DispatchRx(local, iface);
  }
  return all_written;
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

EthernetBusRegistry::RxCallbackId EthernetBusRegistry::SubscribeFrame(FrameRxCallback cb) {
  std::lock_guard<std::mutex> lock(frame_subs_mutex_);
  const RxCallbackId id = next_frame_id_++;
  frame_subscriptions_[id] = std::move(cb);
  return id;
}

void EthernetBusRegistry::UnsubscribeFrame(RxCallbackId id) {
  std::lock_guard<std::mutex> lock(frame_subs_mutex_);
  frame_subscriptions_.erase(id);
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
  // Stop and join outside the mutex: an RX callback can call SendFrame, which
  // takes ifaces_mutex_. Entries are never erased, so their addresses remain
  // valid. Close the driver only after the RX thread has exited, so no
  // ReadFrame can race a close of the socket.
  std::vector<IfaceEntry*> entries;
  {
    std::lock_guard<std::mutex> lock(ifaces_mutex_);
    entries.reserve(ifaces_.size());
    for (auto& [name, entry] : ifaces_) {
      (void)name;
      entry.running.store(false);
      entries.push_back(&entry);
    }
  }
  for (auto* entry : entries) {
    if (entry->rx_thread.joinable()) {
      entry->rx_thread.join();
    }
  }
  for (auto* entry : entries) {
    entry->driver->Close();
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

  // Deliver to unified-frame subscribers.
  std::vector<FrameRxCallback> frame_cbs;
  {
    std::lock_guard<std::mutex> lock(frame_subs_mutex_);
    frame_cbs.reserve(frame_subscriptions_.size());
    for (const auto& [id, cb] : frame_subscriptions_) {
      (void)id;
      frame_cbs.push_back(cb);
    }
  }
  if (!frame_cbs.empty()) {
    uint8_t ip_version = 0;
    if (!frame.src_ip.empty()) {
      ip_version = (frame.src_ip.size() == 4) ? 4U : 6U;
    }
    auto core_frame = boat::core::Frame::FromEthernet(
        iface,
        const_cast<uint8_t*>(frame.dst_mac),
        const_cast<uint8_t*>(frame.src_mac),
        frame.ethertype, frame.vlan_id,
        frame.src_ip.empty() ? nullptr : frame.src_ip.data(), ip_version,
        frame.dst_ip.empty() ? nullptr : frame.dst_ip.data(),
        frame.payload, frame.flags);
    core_frame.set_timestamp_ns(frame.timestamp_ns);
    for (const auto& cb : frame_cbs) {
      cb(core_frame);
    }
  }
}

}  // namespace boat::hil

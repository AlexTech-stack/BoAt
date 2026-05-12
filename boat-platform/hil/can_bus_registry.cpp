#include "can_bus_registry.h"

#include <utility>

namespace boat::hil {

bool CanBusRegistry::Add(const std::string& iface, std::shared_ptr<IHalDriver> driver,
                         boat::core::EventBus& bus) {
  if (!driver->Open()) {
    return false;
  }

  auto bridge = std::make_unique<HilBridge>(std::move(driver), bus);

  // Capture iface by value so the lambda stays valid after Add() returns.
  bridge->SetOnReceive([this, iface](const CanFrame& frame) {
    DispatchRx(frame, iface);
  });

  bridge->Start();

  std::lock_guard<std::mutex> lock(bridges_mutex_);
  bridges_[iface] = BridgeEntry{iface, std::move(bridge)};
  return true;
}

bool CanBusRegistry::SendFrame(const std::string& iface, const CanFrame& frame) {
  {
    std::lock_guard<std::mutex> lock(bridges_mutex_);
    const auto it = bridges_.find(iface);
    if (it == bridges_.end()) {
      return false;
    }
    it->second.bridge->SendFrame(frame);
  }
  // Dispatch directly so gRPC subscribers see sent frames without socket loopback.
  // Lock must be released first: subscriber callbacks may re-enter SendFrame/SendFrameAll
  // (e.g. a C++ plugin that reacts and sends a response).
  DispatchRx(frame, iface);
  return true;
}

void CanBusRegistry::SendFrameAll(const CanFrame& frame) {
  // Collect iface names while holding the lock, then dispatch without it.
  std::vector<std::string> dispatched_ifaces;
  {
    std::lock_guard<std::mutex> lock(bridges_mutex_);
    for (auto& [name, entry] : bridges_) {
      entry.bridge->SendFrame(frame);
      dispatched_ifaces.push_back(name);
    }
  }
  for (const auto& iface : dispatched_ifaces) {
    DispatchRx(frame, iface);
  }
}

CanBusRegistry::RxCallbackId CanBusRegistry::Subscribe(const std::string& iface_filter,
                                                       RxCallback cb) {
  std::lock_guard<std::mutex> lock(subs_mutex_);
  const RxCallbackId id = next_id_++;
  subscriptions_[id] = Subscription{iface_filter, std::move(cb)};
  return id;
}

void CanBusRegistry::Unsubscribe(RxCallbackId id) {
  std::lock_guard<std::mutex> lock(subs_mutex_);
  subscriptions_.erase(id);
}

std::vector<std::string> CanBusRegistry::Interfaces() const {
  std::lock_guard<std::mutex> lock(bridges_mutex_);
  std::vector<std::string> names;
  names.reserve(bridges_.size());
  for (const auto& [name, _] : bridges_) {
    names.push_back(name);
  }
  return names;
}

bool CanBusRegistry::Has(const std::string& iface) const {
  std::lock_guard<std::mutex> lock(bridges_mutex_);
  return bridges_.find(iface) != bridges_.end();
}

void CanBusRegistry::StopAll() {
  std::lock_guard<std::mutex> lock(bridges_mutex_);
  for (auto& [name, entry] : bridges_) {
    (void)name;
    entry.bridge->Stop();
  }
}

void CanBusRegistry::DispatchRx(const CanFrame& frame, const std::string& iface) {
  // Snapshot subscriptions to avoid holding the lock during callbacks.
  std::vector<RxCallback> to_call;
  {
    std::lock_guard<std::mutex> lock(subs_mutex_);
    for (const auto& [id, sub] : subscriptions_) {
      (void)id;
      if (sub.iface_filter.empty() || sub.iface_filter == iface) {
        to_call.push_back(sub.cb);
      }
    }
  }
  for (const auto& cb : to_call) {
    cb(frame, iface);
  }
}

}  // namespace boat::hil

#pragma once

#include <cstddef>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

#include "event/event_bus.h"
#include "hal/hal_driver.h"
#include "hil_bridge.h"

namespace boat::hil {

/* Manages a set of named CAN bridges (one per interface).
   Thread-safe: Add/Send/Subscribe/Unsubscribe may be called from any thread. */
class CanBusRegistry {
 public:
  using RxCallbackId = std::size_t;
  /* Callback receives the frame and the interface it arrived on. */
  using RxCallback = std::function<void(const CanFrame&, const std::string& iface)>;

  /* Open driver, start bridge, register it under iface.
     Returns true on success; false if the driver fails to open (entry not added). */
  bool Add(const std::string& iface, std::shared_ptr<IHalDriver> driver,
           boat::core::EventBus& bus);

  /* Send a frame on the named interface. Returns false if iface is unknown. */
  bool SendFrame(const std::string& iface, const CanFrame& frame);

  /* Send a frame on every registered interface. */
  void SendFrameAll(const CanFrame& frame);

  /* Subscribe to incoming frames.
     iface_filter = "" receives frames from ALL interfaces.
     iface_filter = "vcan0" receives only frames from that interface.
     Returns an ID that must be passed to Unsubscribe when done. */
  RxCallbackId Subscribe(const std::string& iface_filter, RxCallback cb);
  void Unsubscribe(RxCallbackId id);

  std::vector<std::string> Interfaces() const;
  bool Has(const std::string& iface) const;

  void StopAll();

 private:
  void DispatchRx(const CanFrame& frame, const std::string& iface);

  struct BridgeEntry {
    std::string iface;
    std::unique_ptr<HilBridge> bridge;
  };

  struct Subscription {
    std::string iface_filter;
    RxCallback cb;
  };

  mutable std::mutex bridges_mutex_;
  std::unordered_map<std::string, BridgeEntry> bridges_;

  std::mutex subs_mutex_;
  std::unordered_map<RxCallbackId, Subscription> subscriptions_;
  RxCallbackId next_id_{0};
};

}  // namespace boat::hil

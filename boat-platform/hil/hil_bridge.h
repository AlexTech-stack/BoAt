#pragma once

#include <atomic>
#include <cstdint>
#include <memory>
#include <optional>
#include <thread>

#include "event/event_bus.h"
#include "hal/hal_driver.h"

namespace boat::hil {

class HilBridge {
 public:
  HilBridge(std::shared_ptr<IHalDriver> driver, boat::core::EventBus& bus);

  void Start();
  void Stop();
  void SendFrame(const CanFrame& frame);

 private:
  static constexpr std::uint32_t kEventTypeCanFrameRx = 0xCA1F0001u;
  static constexpr std::uint32_t kEventTypeCanFrameTx = 0xCA1F0002u;

  std::shared_ptr<IHalDriver> driver_;
  boat::core::EventBus& bus_;
  std::thread rx_thread_;
  std::atomic<bool> running_{false};
  std::optional<boat::core::EventBus::SubscriptionHandle> tx_subscription_;
};

}  // namespace boat::hil

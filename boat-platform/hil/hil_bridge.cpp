#include "hil_bridge.h"

#include <any>
#include <utility>

namespace boat::hil {

HilBridge::HilBridge(std::shared_ptr<IHalDriver> driver, boat::core::EventBus& bus)
    : driver_(std::move(driver)), bus_(bus) {}

void HilBridge::Start() {
  if (running_.exchange(true)) {
    return;
  }

  tx_subscription_ = bus_.Subscribe(kEventTypeCanFrameTx, [this](const boat::core::BusEvent& event) {
    if (driver_ == nullptr) {
      return;
    }
    const CanFrame* frame = std::any_cast<CanFrame>(&event.payload);
    if (frame == nullptr) {
      return;
    }
    (void)driver_->WriteFrame(*frame);
  });

  rx_thread_ = std::thread([this]() {
    while (running_.load()) {
      CanFrame frame {};
      if (driver_ != nullptr && driver_->ReadFrame(frame)) {
        bus_.Publish(boat::core::BusEvent{kEventTypeCanFrameRx, frame, 0});
      }
    }
  });
}

void HilBridge::Stop() {
  running_.store(false);
  if (rx_thread_.joinable()) {
    rx_thread_.join();
  }
  if (tx_subscription_.has_value()) {
    bus_.Unsubscribe(*tx_subscription_);
    tx_subscription_.reset();
  }
  if (driver_ != nullptr) {
    driver_->Close();
  }
}

void HilBridge::SendFrame(const CanFrame& frame) {
  if (driver_ != nullptr) {
    driver_->WriteFrame(frame);
  }
}

}  // namespace boat::hil

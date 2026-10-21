// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "hil_bridge.h"

#include <cstdio>
#include <cstring>
#include <utility>

namespace boat::hil {
namespace {

std::vector<std::uint8_t> EncodeCanFrame(const CanFrame& frame) {
  std::vector<std::uint8_t> data(sizeof(frame));
  std::memcpy(data.data(), &frame, sizeof(frame));
  return data;
}

bool DecodeCanFrame(const std::vector<std::uint8_t>& data, CanFrame& frame) {
  if (data.size() < sizeof(frame)) return false;
  std::memcpy(&frame, data.data(), sizeof(frame));
  return true;
}

}  // namespace

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
    const auto* unknown = std::get_if<boat::core::UnknownPayload>(&event.payload);
    if (unknown == nullptr) {
      return;
    }
    CanFrame frame{};
    if (!DecodeCanFrame(unknown->data, frame)) {
      return;
    }
    if (!driver_->WriteFrame(frame)) {
      std::fprintf(stderr, "[HilBridge] CAN TX failed (id=0x%X, dlc=%u)\n",
                   frame.can_id, static_cast<unsigned>(frame.dlc));
    }
  });

  rx_thread_ = std::thread([this]() {
    while (running_.load()) {
      CanFrame frame {};
      if (driver_ != nullptr && driver_->ReadFrame(frame)) {
        // Skip the encode and the queued event when no handler can receive
        // it; EventBus::Publish would drop it at publish time anyway.
        if (bus_.HasSubscribers(kEventTypeCanFrameRx)) {
          bus_.Publish(boat::core::BusEvent{
              kEventTypeCanFrameRx,
              boat::core::UnknownPayload{kEventTypeCanFrameRx, EncodeCanFrame(frame)},
              0});
        }
        std::function<void(const CanFrame&)> cb;
        {
          std::lock_guard<std::mutex> lock(on_receive_mutex_);
          cb = on_receive_cb_;
        }
        if (cb) cb(frame);
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

bool HilBridge::SendFrame(const CanFrame& frame) {
  return driver_ != nullptr && driver_->WriteFrame(frame);
}

void HilBridge::SetOnReceive(std::function<void(const CanFrame&)> cb) {
  std::lock_guard<std::mutex> lock(on_receive_mutex_);
  on_receive_cb_ = std::move(cb);
}

}  // namespace boat::hil

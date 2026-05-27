#include "ethernet_hil_bridge.h"

#include <any>
#include <memory>
#include <utility>

#include "ethernet/raw_socket_ethernet_driver.h"

namespace boat::hil {

EthernetHilBridge::EthernetHilBridge(EthernetBusRegistry& registry,
                                     boat::core::EventBus& bus)
    : registry_(registry), bus_(bus) {
  // Forward every received frame (any interface) into the EventBus.
  rx_sub_id_ = registry_.Subscribe(
      "", 0,
      [this](const EthernetFrame& frame, const std::string& /*iface*/) {
        bus_.Publish(boat::core::BusEvent{kEventTypeEthRx, frame, 0});
      });
  subscribed_ = true;

  // Forward EventBus TX events onto all registered physical interfaces.
  tx_sub_ = bus_.Subscribe(
      kEventTypeEthTx,
      [this](const boat::core::BusEvent& event) {
        const auto* frame = std::any_cast<EthernetFrame>(&event.payload);
        if (frame) registry_.SendFrameAll(*frame);
      });
}

EthernetHilBridge::~EthernetHilBridge() { Stop(); }

bool EthernetHilBridge::AddPhysicalInterface(const std::string& iface) {
  return registry_.Add(iface, std::make_unique<RawSocketEthernetDriver>(iface));
}

void EthernetHilBridge::Stop() {
  if (subscribed_) {
    registry_.Unsubscribe(rx_sub_id_);
    subscribed_ = false;
  }
  if (tx_sub_.has_value()) {
    bus_.Unsubscribe(*tx_sub_);
    tx_sub_.reset();
  }
}

}  // namespace boat::hil

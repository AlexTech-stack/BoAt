// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "frame_sink.h"

#include <algorithm>
#include <cstddef>
#include <cstring>

#include "can_bus_registry.h"
#include "ethernet_bus_registry.h"

namespace boat::gateway {

bool FrameSink::Publish(const boat::core::Frame& frame) {
  using BusType = boat::core::Frame::BusType;

  switch (frame.bus_type()) {
    case BusType::kCan:
    case BusType::kCanFd: {
      const auto& cm = frame.can_meta();
      const std::size_t payload_len = frame.payload().size();

      // Never clamp a frame into a shape it did not ask for. A classic CAN
      // frame with 9+ bytes, a CAN FD frame with 65+, a DLC that disagrees
      // with the payload, or a remote frame carrying data are all rejected
      // rather than silently transmitted as something else (#75).
      if (payload_len > 64 || cm.dlc > 64 || cm.can_id > 0x1FFFFFFF) {
        return false;
      }
      const bool is_fd = frame.bus_type() == BusType::kCanFd;
      const bool is_rtr = (cm.flags & boat::hil::kCanFlagRtr) != 0;
      if (is_fd && is_rtr) {
        return false;
      }
      if (is_rtr) {
        if (cm.dlc > 8 || !frame.payload().empty()) {
          return false;
        }
      } else {
        if (cm.dlc != payload_len) {
          return false;
        }
        if (!is_fd && cm.dlc > 8) {
          return false;
        }
      }

      boat::hil::CanFrame cf{};
      cf.can_id       = cm.can_id;
      cf.flags        = cm.flags;
      cf.timestamp_ns = frame.timestamp_ns();
      // The bus type is authoritative for the wire format; keep the FDF bit
      // in sync even when a caller only set bus_type=CANFD.
      if (is_fd) {
        cf.flags |= boat::hil::kCanFdFlagFdf;
      } else {
        cf.flags &= static_cast<std::uint8_t>(~boat::hil::kCanFdFlagFdf);
      }
      if (cm.can_id > 0x7FF) {
        cf.flags |= boat::hil::kCanFlagExtended;
      }
      cf.dlc = cm.dlc;
      if (!is_rtr && payload_len > 0) {
        std::memcpy(cf.data, frame.payload().data(), payload_len);
      }
      if (!frame.iface().empty()) {
        return can_registry_.SendFrame(frame.iface(), cf);
      }
      return can_registry_.SendFrameAll(cf);
    }

    case BusType::kEthernet: {
      const auto& em = frame.eth_meta();
      if (frame.payload().size() > 1500) {
        return false;  // no silent truncation at the raw driver
      }
      boat::hil::EthernetFrame ef{};
      std::memcpy(ef.dst_mac, em.dst_mac, 6);
      std::memcpy(ef.src_mac, em.src_mac, 6);
      ef.ethertype    = em.ethertype;
      ef.vlan_id      = em.vlan_id;
      ef.flags        = em.flags;
      ef.timestamp_ns = frame.timestamp_ns();
      ef.payload      = frame.payload();
      if (!frame.iface().empty()) {
        return eth_registry_.SendFrame(frame.iface(), ef);
      }
      return eth_registry_.SendFrameAll(ef);
    }

    case BusType::kTcp:
    case BusType::kPdu:
    case BusType::kUnspecified:
    default:
      // Not wire buses — TCP/PDU are stateful conversations dispatched to
      // plugins via PluginManager::DispatchFrame, never transmitted here.
      return false;
  }
}

}  // namespace boat::gateway

#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace boat::hil {

enum class PduTransport { kUnspecified = 0, kCan = 1, kEthernet = 2 };

// Routing rule: maps a PDU ID to a transport interface.
struct PduRoute {
  uint32_t     pdu_id{0};
  PduTransport transport{PduTransport::kUnspecified};
  std::string  iface;
  uint16_t     vlan_id{0};         // 0 = untagged (Ethernet)
  uint32_t     can_id{0};          // 0 = use pdu_id as CAN ID
  uint16_t     ethertype{0x88B5};  // Ethernet PDU ethertype (default: custom test)
};

// A PDU as received or about to be sent.
struct PduFrame {
  uint32_t             pdu_id{0};
  std::vector<uint8_t> payload;
  uint64_t             timestamp_ns{0};
  PduTransport         source{PduTransport::kUnspecified};
  std::string          iface;
};

}  // namespace boat::hil

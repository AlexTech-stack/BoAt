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
  uint16_t     ethertype{0x88B5};  // used only when dst_ip is empty (sim-only path)

  // IP/UDP/IpduM path — active when dst_ip is non-empty.
  // EtherType is then set automatically: 0x0800 (IPv4) or 0x86DD (IPv6).
  std::vector<uint8_t> src_ip;    // 4 bytes = IPv4, 16 bytes = IPv6
  std::vector<uint8_t> dst_ip;    // 4 bytes = IPv4, 16 bytes = IPv6
  uint16_t             src_port{0};
  uint16_t             dst_port{0};
  uint8_t              ttl{64};   // IPv4 TTL / IPv6 Hop Limit
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

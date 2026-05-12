#pragma once

#include <cstdint>
#include <vector>

namespace boat::hil {

struct EthernetFrame {
  uint8_t              src_mac[6]{};   // source MAC address
  uint8_t              dst_mac[6]{};   // destination MAC address
  uint16_t             ethertype{0};   // e.g. 0x0800=IPv4, 0x86DD=IPv6
  std::vector<uint8_t> payload;        // frame payload (≤1500 bytes typical)
  uint64_t             timestamp_ns{0};
};

class IEthernetDriver {
 public:
  virtual ~IEthernetDriver() = default;

  virtual bool Open()  = 0;
  virtual void Close() = 0;

  /* Block until a frame is received.  Returns false on error or close. */
  virtual bool ReadFrame(EthernetFrame& out) = 0;

  /* Send a frame.  Returns false on error. */
  virtual bool WriteFrame(const EthernetFrame& frame) = 0;
};

}  // namespace boat::hil

#pragma once

#include <cstdint>
#include <string>

namespace boat::hil {

struct CanFrame {
  std::uint32_t can_id;
  std::uint8_t dlc;
  std::uint8_t data[8];
  std::uint64_t timestamp_ns;
};

class IHalDriver {
 public:
  virtual bool Open() = 0;
  virtual bool ReadFrame(CanFrame& out_frame) = 0;
  virtual bool WriteFrame(const CanFrame& frame) = 0;
  virtual void Close() = 0;
  virtual ~IHalDriver() = default;
};

}  // namespace boat::hil

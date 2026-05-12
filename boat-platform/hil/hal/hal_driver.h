#pragma once

#include <cstdint>
#include <string>

namespace boat::hil {

/* Flags matching Linux CANFD_* constants (linux/can/raw.h). */
static constexpr std::uint8_t kCanFdFlagFdf = 0x04;  // FD frame
static constexpr std::uint8_t kCanFdFlagBrs = 0x01;  // bit-rate switch
static constexpr std::uint8_t kCanFdFlagEsi = 0x02;  // error state indicator

struct CanFrame {
  std::uint32_t can_id;
  std::uint8_t  dlc;           // actual byte count (0-8 classic, 0-64 FD)
  std::uint8_t  flags;         // kCanFdFlag* bits; 0 = classic CAN
  std::uint8_t  data[64];
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

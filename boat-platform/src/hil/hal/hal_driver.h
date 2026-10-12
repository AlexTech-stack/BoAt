// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

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

/* Metadata about a CAN interface, populated by the driver from sysfs. */
struct CanInterfaceInfo {
  std::string driver_name;   // e.g. "peak_usb", "vcan", "socketcan"
  bool         fd_support;   // whether the interface supports CAN FD frames
  std::string  state;        // "up", "down", "unknown"
  std::uint32_t bitrate;     // nominal bitrate in bit/s, 0 if unknown
};

/* Runtime health of a CAN bus, as far as the driver can see it. Counters are
 * monotonic since the interface was opened; the state and error counters are
 * a snapshot (netlink for physical CAN, error frames recorded at runtime).
 *
 * Before this existed, a bus that had gone bus-off and a bus that was merely
 * idle looked identical from the outside: frames stopped arriving and nothing
 * said why. */
struct CanBusHealth {
  std::uint64_t rx_dropped = 0;         // per-socket RX overflow (SO_RXQ_OVFL), best-effort
  std::uint64_t device_rx_dropped = 0;  // device-level rx_dropped (netlink IFLA_STATS64)
  std::uint64_t device_rx_errors = 0;   // device-level rx_errors (netlink IFLA_STATS64)
  std::uint64_t error_frames = 0;       // CAN error frames observed on this socket
  std::uint64_t bus_off_events = 0;     // transitions into bus-off observed at runtime
  std::uint32_t tec = 0;                // controller TX error counter (netlink)
  std::uint32_t rec = 0;                // controller RX error counter (netlink)
  std::uint32_t state_code = 0xFFFFFFFFu;  // Linux CAN_STATE_* value; 0xFFFFFFFF = unreported
  std::string   state = "unknown";         // "active" | "error-warning" | "error-passive" | "busoff" | ...
};

class IHalDriver {
 public:
  virtual bool Open() = 0;
  virtual bool ReadFrame(CanFrame& out_frame) = 0;
  virtual bool WriteFrame(const CanFrame& frame) = 0;
  virtual void Close() = 0;
  virtual CanInterfaceInfo GetInfo() const = 0;

  /* Snapshot of bus health. The default is "nothing known", which is what a
     driver that cannot observe its bus should report rather than zeros that
     look like a healthy bus. */
  [[nodiscard]] virtual CanBusHealth GetBusHealth() const { return {}; }

  virtual ~IHalDriver() = default;
};

}  // namespace boat::hil

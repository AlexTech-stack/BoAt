// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <mutex>
#include <string>

#include "hal/hal_driver.h"

namespace boat::hil {

class SocketCanDriver : public IHalDriver {
 public:
  explicit SocketCanDriver(std::string interface_name = "can0");

  bool Open() override;
  bool ReadFrame(CanFrame& out_frame) override;
  bool WriteFrame(const CanFrame& frame) override;
  void Close() override;
  CanInterfaceInfo GetInfo() const override;
  CanBusHealth GetBusHealth() const override;

  /* SO_RCVBUF bytes requested for every CAN socket: BOAT_CAN_RCVBUF, or
     kDefaultRxBufBytes when unset or empty. The value 0 means "leave the
     kernel default". A malformed value falls back to the default and appends
     a warning to `warning_out`. Exposed so main() can record the same value
     in the effective-config artifact the driver actually applies. */
  static constexpr int kDefaultRxBufBytes = 4 * 1024 * 1024;
  static int RequestedRxBufBytes(std::string* warning_out);

 private:
  void HandleErrorFrame(std::uint32_t can_id, const std::uint8_t* data, std::size_t len);

  int socket_fd_{-1};
  std::string iface_;
  std::atomic<std::uint64_t> rx_dropped_{0};
  std::atomic<std::uint64_t> rx_error_frames_{0};
  std::atomic<std::uint64_t> bus_off_events_{0};
  std::atomic<std::uint64_t> last_drop_counter_{0};
  mutable std::mutex health_mutex_;
  mutable std::string last_bus_state_{"unknown"};
};

}  // namespace boat::hil

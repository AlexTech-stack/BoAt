// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace boat::hil {

/* What one SocketCAN error frame says about the controller. Error frames are
   delivered with CAN_ERR_FLAG set in can_id; they are not data and must not
   be dispatched to plugins as if they were. */
struct CanErrorInfo {
  bool bus_off = false;        // the controller went bus-off
  bool error_passive = false;  // RX or TX error counter crossed 127
  bool error_warning = false;  // an error counter crossed 95
  bool restarted = false;      // the controller restarted after bus-off
};

/* Classify an error frame. `can_id` must already carry CAN_ERR_FLAG; `data` is
   the frame payload (details live there for some classes). On non-Linux this
   returns a default-constructed (all-false) result. */
CanErrorInfo ClassifyCanError(std::uint32_t can_id, const std::uint8_t* data, std::size_t len);

/* One interface's link status as reported by RTM_GETLINK. */
struct CanLinkStatus {
  bool found = false;         // the message described the requested interface
  bool has_can_info = false;  // IFLA_INFO_KIND was "can" with state/counters
  std::string kind;           // "can", "vcan", ...
  std::string state = "unknown";
  std::uint32_t state_code = 0xFFFFFFFFu;  // Linux CAN_STATE_*
  std::uint32_t tec = 0;                   // CAN_BERR_COUNTER txerr
  std::uint32_t rec = 0;                   // CAN_BERR_COUNTER rxerr
  bool has_stats = false;                  // IFLA_STATS64 was present
  std::uint64_t rx_dropped = 0;
  std::uint64_t rx_errors = 0;
};

/* Parse a single netlink message buffer (struct nlmsghdr + payload, as
   returned by recv()). Returns true and fills `out` when the message is an
   RTM_NEWLINK for `iface`; false for anything else. Exposed for tests. */
bool ParseCanLinkMessage(const std::uint8_t* message, std::size_t length,
                         const std::string& iface, CanLinkStatus* out);

/* Ask the kernel (RTM_GETLINK over NETLINK_ROUTE) for `iface`'s link status.
   Returns false if netlink is unavailable or the interface was not reported;
   `out` is only meaningful when true. */
bool QueryCanLinkStatus(const std::string& iface, CanLinkStatus* out);

}  // namespace boat::hil

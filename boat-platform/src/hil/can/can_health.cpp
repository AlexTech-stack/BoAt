// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "can/can_health.h"

#ifndef __linux__
// The gateway is Linux-only (CMake enforces it); these are stubs so the file
// still compiles for tooling that parses the tree on other platforms.
namespace boat::hil {
CanErrorInfo ClassifyCanError(std::uint32_t, const std::uint8_t*, std::size_t) { return {}; }
bool ParseCanLinkMessage(const std::uint8_t*, std::size_t, const std::string&, CanLinkStatus*) { return false; }
bool QueryCanLinkStatus(const std::string&, CanLinkStatus*) { return false; }
}  // namespace boat::hil
#else

#include <cerrno>
#include <cstring>
#include <vector>

#include <linux/can.h>
#include <linux/can/error.h>
#include <linux/can/netlink.h>
#include <linux/if_link.h>
#include <linux/netlink.h>
#include <linux/rtnetlink.h>
#include <net/if.h>
#include <sys/socket.h>
#include <unistd.h>

namespace boat::hil {

namespace {

std::string StateName(std::uint32_t code) {
  switch (code) {
    case CAN_STATE_ERROR_ACTIVE:  return "active";
    case CAN_STATE_ERROR_WARNING: return "error-warning";
    case CAN_STATE_ERROR_PASSIVE: return "error-passive";
    case CAN_STATE_BUS_OFF:       return "busoff";
    case CAN_STATE_STOPPED:       return "stopped";
    case CAN_STATE_SLEEPING:      return "sleeping";
    default:                      return "unknown";
  }
}

}  // namespace

CanErrorInfo ClassifyCanError(std::uint32_t can_id, const std::uint8_t* data, std::size_t len) {
  CanErrorInfo info{};
  if ((can_id & CAN_ERR_FLAG) == 0) {
    return info;
  }
  if (can_id & CAN_ERR_BUSOFF) info.bus_off = true;
  if (can_id & CAN_ERR_RESTARTED) info.restarted = true;
  if ((can_id & CAN_ERR_CRTL) != 0 && data != nullptr && len >= 2) {
    const std::uint8_t ctrl = data[1];
    if (ctrl & (CAN_ERR_CRTL_RX_PASSIVE | CAN_ERR_CRTL_TX_PASSIVE)) info.error_passive = true;
    if (ctrl & (CAN_ERR_CRTL_RX_WARNING | CAN_ERR_CRTL_TX_WARNING)) info.error_warning = true;
  }
  return info;
}

bool ParseCanLinkMessage(const std::uint8_t* message, std::size_t length,
                         const std::string& iface, CanLinkStatus* out) {
  if (message == nullptr || out == nullptr || length < sizeof(struct nlmsghdr)) {
    return false;
  }
  const auto* header = reinterpret_cast<const struct nlmsghdr*>(message);
  if (header->nlmsg_type != RTM_NEWLINK) {
    return false;
  }
  const std::size_t message_length = header->nlmsg_len;
  if (message_length < NLMSG_LENGTH(sizeof(struct ifinfomsg)) || message_length > length) {
    return false;
  }
  const auto* info = static_cast<const struct ifinfomsg*>(NLMSG_DATA(header));
  if (info->ifi_family != AF_UNSPEC) {
    return false;
  }

  CanLinkStatus status;
  bool name_matches = iface.empty();
  bool saw_state = false;
  bool saw_counters = false;

  int remaining = static_cast<int>(message_length) - NLMSG_LENGTH(sizeof(struct ifinfomsg));
  for (const struct rtattr* attr = IFLA_RTA(info); RTA_OK(attr, remaining);
       attr = RTA_NEXT(attr, remaining)) {
    switch (attr->rta_type) {
      case IFLA_IFNAME:
        name_matches =
            name_matches || iface == reinterpret_cast<const char*>(RTA_DATA(attr));
        break;

      case IFLA_STATS64:
        if (RTA_PAYLOAD(attr) >= sizeof(struct rtnl_link_stats64)) {
          const auto* stats = static_cast<const struct rtnl_link_stats64*>(RTA_DATA(attr));
          status.has_stats = true;
          status.rx_dropped = stats->rx_dropped;
          status.rx_errors = stats->rx_errors;
        }
        break;

      case IFLA_LINKINFO: {
        int linkinfo_remaining = static_cast<int>(RTA_PAYLOAD(attr));
        for (const struct rtattr* linkinfo =
                 static_cast<const struct rtattr*>(RTA_DATA(attr));
             RTA_OK(linkinfo, linkinfo_remaining);
             linkinfo = RTA_NEXT(linkinfo, linkinfo_remaining)) {
          if (linkinfo->rta_type == IFLA_INFO_KIND) {
            status.kind = reinterpret_cast<const char*>(RTA_DATA(linkinfo));
          } else if (linkinfo->rta_type == IFLA_INFO_DATA) {
            int data_remaining = static_cast<int>(RTA_PAYLOAD(linkinfo));
            for (const struct rtattr* can =
                     static_cast<const struct rtattr*>(RTA_DATA(linkinfo));
                 RTA_OK(can, data_remaining); can = RTA_NEXT(can, data_remaining)) {
              if (can->rta_type == IFLA_CAN_STATE &&
                  RTA_PAYLOAD(can) >= sizeof(std::uint32_t)) {
                std::uint32_t code = 0;
                std::memcpy(&code, RTA_DATA(can), sizeof(code));
                status.state_code = code;
                status.state = StateName(code);
                saw_state = true;
              } else if (can->rta_type == IFLA_CAN_BERR_COUNTER &&
                         RTA_PAYLOAD(can) >= sizeof(struct can_berr_counter)) {
                const auto* counter =
                    static_cast<const struct can_berr_counter*>(RTA_DATA(can));
                status.tec = counter->txerr;
                status.rec = counter->rxerr;
                saw_counters = true;
              }
            }
          }
        }
        break;
      }

      default:
        break;
    }
  }

  if (!name_matches) {
    return false;
  }
  status.found = true;
  // IFLA_CAN_STATE / IFLA_CAN_BERR_COUNTER only exist for a real `type can`
  // link; a vcan or non-CAN link has no controller to report.
  status.has_can_info = status.kind == "can" && (saw_state || saw_counters);
  *out = status;
  return true;
}

bool QueryCanLinkStatus(const std::string& iface, CanLinkStatus* out) {
  if (out == nullptr) {
    return false;
  }

  const int fd = ::socket(AF_NETLINK, SOCK_RAW | SOCK_CLOEXEC, NETLINK_ROUTE);
  if (fd < 0) {
    return false;
  }

  struct timeval timeout {};
  timeout.tv_sec = 1;
  (void)setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

  struct sockaddr_nl local {};
  local.nl_family = AF_NETLINK;
  if (::bind(fd, reinterpret_cast<struct sockaddr*>(&local), sizeof(local)) < 0) {
    ::close(fd);
    return false;
  }

  struct {
    struct nlmsghdr header;
    struct ifinfomsg info;
  } request {};
  request.header.nlmsg_len = NLMSG_LENGTH(sizeof(struct ifinfomsg));
  request.header.nlmsg_type = RTM_GETLINK;
  request.header.nlmsg_flags = NLM_F_REQUEST | NLM_F_DUMP;
  request.header.nlmsg_seq = 1;
  request.info.ifi_family = AF_UNSPEC;

  if (::send(fd, &request, request.header.nlmsg_len, 0) < 0) {
    ::close(fd);
    return false;
  }

  bool found = false;
  std::vector<std::uint8_t> buffer(32768);
  while (!found) {
    const ssize_t received = ::recv(fd, buffer.data(), buffer.size(), 0);
    if (received <= 0) {
      break;
    }
    int remaining = static_cast<int>(received);
    for (const struct nlmsghdr* header =
             reinterpret_cast<const struct nlmsghdr*>(buffer.data());
         NLMSG_OK(header, remaining); header = NLMSG_NEXT(header, remaining)) {
      if (header->nlmsg_type == NLMSG_DONE) {
        remaining = 0;
        break;
      }
      if (header->nlmsg_type != RTM_NEWLINK) {
        continue;
      }
      if (ParseCanLinkMessage(reinterpret_cast<const std::uint8_t*>(header),
                              header->nlmsg_len, iface, out)) {
        found = true;
        break;
      }
    }
    if (remaining == 0) {
      break;
    }
  }

  ::close(fd);
  return found;
}

}  // namespace boat::hil

#endif  // __linux__

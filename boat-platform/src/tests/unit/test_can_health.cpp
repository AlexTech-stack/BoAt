// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* Tests for the CAN health path: error-frame classification, netlink link
   parsing, and the SO_RCVBUF knob.

   These exist because a bus that has gone bus-off used to look exactly like a
   bus that is merely idle (issue #22) and RX overflow dropped frames with no
   counter and no log line (issue #20). The classification and parsing are pure
   functions precisely so the interesting cases can be tested without hardware
   or a live netlink socket. */

#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include <linux/can.h>
#include <linux/can/error.h>
#include <linux/can/netlink.h>
#include <linux/if_link.h>
#include <linux/netlink.h>
#include <linux/rtnetlink.h>
#include <sys/socket.h>

#include "can/can_health.h"
#include "can/socket_can_driver.h"

using boat::hil::CanErrorInfo;
using boat::hil::CanLinkStatus;
using boat::hil::ClassifyCanError;
using boat::hil::ParseCanLinkMessage;
using boat::hil::SocketCanDriver;

namespace {

void AppendAttr(std::vector<std::uint8_t>& out, std::uint16_t type,
                const void* data, std::size_t len) {
  const std::size_t offset = out.size();
  const std::size_t attr_len = RTA_LENGTH(len);
  out.resize(offset + RTA_ALIGN(attr_len), 0);
  auto* attr = reinterpret_cast<struct rtattr*>(out.data() + offset);
  attr->rta_type = type;
  attr->rta_len = static_cast<unsigned short>(attr_len);
  if (len > 0) {
    std::memcpy(RTA_DATA(attr), data, len);
  }
}

std::vector<std::uint8_t> BuildLinkMessage(const std::string& iface, bool with_stats,
                                           bool with_can_info, std::uint32_t state_code,
                                           std::uint32_t tec, std::uint32_t rec,
                                           const char* kind) {
  std::vector<std::uint8_t> message;
  message.resize(NLMSG_LENGTH(sizeof(struct ifinfomsg)), 0);
  auto* header = reinterpret_cast<struct nlmsghdr*>(message.data());
  header->nlmsg_type = RTM_NEWLINK;
  header->nlmsg_flags = NLM_F_MULTI;
  auto* info = reinterpret_cast<struct ifinfomsg*>(NLMSG_DATA(header));
  info->ifi_family = AF_UNSPEC;

  AppendAttr(message, IFLA_IFNAME, iface.c_str(), iface.size() + 1);

  if (with_stats) {
    struct rtnl_link_stats64 stats {};
    stats.rx_dropped = 7;
    stats.rx_errors = 3;
    AppendAttr(message, IFLA_STATS64, &stats, sizeof(stats));
  }

  if (with_can_info) {
    std::vector<std::uint8_t> linkinfo;
    AppendAttr(linkinfo, IFLA_INFO_KIND, kind, std::strlen(kind) + 1);
    std::vector<std::uint8_t> data;
    AppendAttr(data, IFLA_CAN_STATE, &state_code, sizeof(state_code));
    struct can_berr_counter counter {};
    counter.txerr = static_cast<std::uint16_t>(tec);
    counter.rxerr = static_cast<std::uint16_t>(rec);
    AppendAttr(data, IFLA_CAN_BERR_COUNTER, &counter, sizeof(counter));
    AppendAttr(linkinfo, IFLA_INFO_DATA, data.data(), data.size());
    AppendAttr(message, IFLA_LINKINFO, linkinfo.data(), linkinfo.size());
  }

  header = reinterpret_cast<struct nlmsghdr*>(message.data());
  header->nlmsg_len = static_cast<std::uint32_t>(message.size());
  return message;
}

}  // namespace

TEST_CASE("Error frames are classified into bus-off, passive and warning", "[unit][can-health]") {
  const std::uint8_t ctrl_passive[8] = {0, CAN_ERR_CRTL_TX_PASSIVE, 0, 0, 0, 0, 0, 0};
  const std::uint8_t ctrl_warning[8] = {0, CAN_ERR_CRTL_RX_WARNING, 0, 0, 0, 0, 0, 0};

  const CanErrorInfo bus_off = ClassifyCanError(CAN_ERR_FLAG | CAN_ERR_BUSOFF, nullptr, 0);
  REQUIRE(bus_off.bus_off);
  REQUIRE_FALSE(bus_off.error_passive);

  const CanErrorInfo passive =
      ClassifyCanError(CAN_ERR_FLAG | CAN_ERR_CRTL, ctrl_passive, sizeof(ctrl_passive));
  REQUIRE(passive.error_passive);
  REQUIRE_FALSE(passive.bus_off);

  const CanErrorInfo warning =
      ClassifyCanError(CAN_ERR_FLAG | CAN_ERR_CRTL, ctrl_warning, sizeof(ctrl_warning));
  REQUIRE(warning.error_warning);
  REQUIRE_FALSE(warning.error_passive);

  const CanErrorInfo restarted =
      ClassifyCanError(CAN_ERR_FLAG | CAN_ERR_RESTARTED, nullptr, 0);
  REQUIRE(restarted.restarted);

  // A data frame is not an error frame.
  const CanErrorInfo data = ClassifyCanError(0x123, nullptr, 0);
  REQUIRE_FALSE(data.bus_off);
  REQUIRE_FALSE(data.restarted);
}

TEST_CASE("Netlink link parsing extracts CAN state, counters and stats",
          "[unit][can-health]") {
  const auto message = BuildLinkMessage("can0", /*with_stats=*/true,
                                        /*with_can_info=*/true, CAN_STATE_BUS_OFF,
                                        /*tec=*/42, /*rec=*/17, "can");
  CanLinkStatus status;
  REQUIRE(ParseCanLinkMessage(message.data(), message.size(), "can0", &status));
  REQUIRE(status.found);
  REQUIRE(status.has_stats);
  REQUIRE(status.rx_dropped == 7);
  REQUIRE(status.rx_errors == 3);
  REQUIRE(status.has_can_info);
  REQUIRE(status.kind == "can");
  REQUIRE(status.state_code == CAN_STATE_BUS_OFF);
  REQUIRE(status.state == "busoff");
  REQUIRE(status.tec == 42);
  REQUIRE(status.rec == 17);

  // A different interface name must not match.
  REQUIRE_FALSE(ParseCanLinkMessage(message.data(), message.size(), "can1", &status));
}

TEST_CASE("vcan is not reported as having CAN controller info", "[unit][can-health]") {
  const auto message = BuildLinkMessage("vcan0", /*with_stats=*/false,
                                        /*with_can_info=*/true, CAN_STATE_ERROR_ACTIVE,
                                        /*tec=*/0, /*rec=*/0, "vcan");
  CanLinkStatus status;
  REQUIRE(ParseCanLinkMessage(message.data(), message.size(), "vcan0", &status));
  REQUIRE(status.found);
  REQUIRE(status.kind == "vcan");
  REQUIRE_FALSE(status.has_can_info);
}

TEST_CASE("Non-link netlink messages are ignored", "[unit][can-health]") {
  const auto message = BuildLinkMessage("can0", false, false, 0, 0, 0, "can");
  std::vector<std::uint8_t> other = message;
  reinterpret_cast<struct nlmsghdr*>(other.data())->nlmsg_type = RTM_NEWADDR;
  CanLinkStatus status;
  REQUIRE_FALSE(ParseCanLinkMessage(other.data(), other.size(), "can0", &status));
}

TEST_CASE("BOAT_CAN_RCVBUF resolves with a clear fallback", "[unit][can-health]") {
  ::unsetenv("BOAT_CAN_RCVBUF");
  std::string warning = "untouched";
  REQUIRE(SocketCanDriver::RequestedRxBufBytes(&warning) == SocketCanDriver::kDefaultRxBufBytes);
  REQUIRE(warning == "untouched");

  ::setenv("BOAT_CAN_RCVBUF", "1048576", 1);
  REQUIRE(SocketCanDriver::RequestedRxBufBytes(&warning) == 1048576);

  ::setenv("BOAT_CAN_RCVBUF", "0", 1);
  REQUIRE(SocketCanDriver::RequestedRxBufBytes(&warning) == 0);

  ::setenv("BOAT_CAN_RCVBUF", "banana", 1);
  warning.clear();
  REQUIRE(SocketCanDriver::RequestedRxBufBytes(&warning) == SocketCanDriver::kDefaultRxBufBytes);
  REQUIRE(warning.find("BOAT_CAN_RCVBUF") != std::string::npos);

  ::setenv("BOAT_CAN_RCVBUF", "-5", 1);
  warning.clear();
  REQUIRE(SocketCanDriver::RequestedRxBufBytes(&warning) == SocketCanDriver::kDefaultRxBufBytes);
  REQUIRE_FALSE(warning.empty());

  ::unsetenv("BOAT_CAN_RCVBUF");
}

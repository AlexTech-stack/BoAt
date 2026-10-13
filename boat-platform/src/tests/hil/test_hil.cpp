// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include <catch2/catch_test_macros.hpp>

#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "virtual/virtual_can_driver.h"

TEST_CASE("Virtual CAN HIL smoke flow", "[hil]") {
  const char* enabled = std::getenv("BOAT_HIL_ENABLED");
  if (enabled == nullptr || *enabled == '\0') {
    SKIP("BOAT_HIL_ENABLED not set");
  }

  const char* iface_env = std::getenv("BOAT_VCAN_IFACE");
  const char* iface = (iface_env != nullptr && *iface_env != '\0') ? iface_env : "vcan0";
  boat::hil::VirtualCanDriver tx_driver(iface);
  boat::hil::VirtualCanDriver rx_driver(iface);

  REQUIRE(tx_driver.Open());
  REQUIRE(rx_driver.Open());

  boat::hil::CanFrame frame {};
  frame.can_id = 0x123;
  frame.dlc = 4;
  frame.data[0] = 0xDE;
  frame.data[1] = 0xAD;
  frame.data[2] = 0xBE;
  frame.data[3] = 0xEF;
  frame.timestamp_ns = 0;
  REQUIRE(tx_driver.WriteFrame(frame));

  boat::hil::CanFrame received {};
  REQUIRE(rx_driver.ReadFrame(received));
  REQUIRE(received.can_id == 0x123);

  tx_driver.Close();
  rx_driver.Close();
  SUCCEED("HIL smoke: PASS");
}

TEST_CASE("Virtual CAN HIL: CAN FD payload over 8 bytes is rounded to a valid ISO 11898-1 length", "[hil]") {
  // A raw 9-byte payload is not a valid CAN FD wire length -- valid lengths
  // jump straight from 8 to 12. Sending dlc=9 verbatim would put an
  // unrepresentable length nibble on the wire; it must come back out at the
  // next valid length (12) with the extra bytes zero-padded, not as a
  // literal 9.
  const char* enabled = std::getenv("BOAT_HIL_ENABLED");
  if (enabled == nullptr || *enabled == '\0') {
    SKIP("BOAT_HIL_ENABLED not set");
  }

  const char* iface_env = std::getenv("BOAT_VCAN_IFACE");
  const char* iface = (iface_env != nullptr && *iface_env != '\0') ? iface_env : "vcan0";
  boat::hil::VirtualCanDriver tx_driver(iface);
  boat::hil::VirtualCanDriver rx_driver(iface);

  REQUIRE(tx_driver.Open());
  REQUIRE(rx_driver.Open());

  boat::hil::CanFrame frame {};
  frame.can_id = 0x200;
  frame.flags = boat::hil::kCanFdFlagFdf;
  frame.dlc = 9;
  const std::uint8_t payload[9] = {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x00, 0x11, 0x22};
  std::memcpy(frame.data, payload, sizeof(payload));
  frame.timestamp_ns = 0;
  REQUIRE(tx_driver.WriteFrame(frame));

  boat::hil::CanFrame received {};
  REQUIRE(rx_driver.ReadFrame(received));
  REQUIRE(received.can_id == 0x200);
  REQUIRE(received.dlc == 12);  // next valid ISO 11898-1 CAN FD length above 9
  REQUIRE(std::memcmp(received.data, payload, sizeof(payload)) == 0);
  REQUIRE(received.data[9] == 0);
  REQUIRE(received.data[10] == 0);
  REQUIRE(received.data[11] == 0);

  tx_driver.Close();
  rx_driver.Close();
  SUCCEED("HIL CAN FD DLC rounding: PASS");
}

TEST_CASE("Virtual CAN HIL: a small extended identifier keeps its IDE bit", "[hil]") {
  // The 29-bit range includes values below 0x800. Inferring the frame format
  // from `can_id > 0x7FF` meant an extended frame with ID 0x123 came back as
  // a *standard* frame -- same numeric ID, silently different frame on the
  // wire (issue #34). The format is carried explicitly now.
  const char* enabled = std::getenv("BOAT_HIL_ENABLED");
  if (enabled == nullptr || *enabled == '\0') {
    SKIP("BOAT_HIL_ENABLED not set");
  }

  const char* iface_env = std::getenv("BOAT_VCAN_IFACE");
  const char* iface = (iface_env != nullptr && *iface_env != '\0') ? iface_env : "vcan0";
  boat::hil::VirtualCanDriver tx_driver(iface);
  boat::hil::VirtualCanDriver rx_driver(iface);

  REQUIRE(tx_driver.Open());
  REQUIRE(rx_driver.Open());

  // Extended format with an identifier the old magnitude heuristic would
  // have called standard.
  boat::hil::CanFrame frame {};
  frame.can_id = 0x123;
  frame.flags = boat::hil::kCanFlagExtended;
  frame.dlc = 2;
  frame.data[0] = 0xAB;
  frame.data[1] = 0xCD;
  REQUIRE(tx_driver.WriteFrame(frame));

  boat::hil::CanFrame received {};
  REQUIRE(rx_driver.ReadFrame(received));
  REQUIRE(received.can_id == 0x123);
  REQUIRE((received.flags & boat::hil::kCanFlagExtended) != 0);

  // A standard frame with the same numeric ID is still a standard frame.
  boat::hil::CanFrame standard {};
  standard.can_id = 0x123;
  standard.flags = 0;
  standard.dlc = 1;
  standard.data[0] = 0x01;
  REQUIRE(tx_driver.WriteFrame(standard));
  REQUIRE(rx_driver.ReadFrame(received));
  REQUIRE(received.can_id == 0x123);
  REQUIRE((received.flags & boat::hil::kCanFlagExtended) == 0);

  // The magnitude fallback still covers a producer that never set the bit.
  boat::hil::CanFrame large {};
  large.can_id = 0x18DA1234;
  large.dlc = 1;
  large.data[0] = 0x02;
  REQUIRE(tx_driver.WriteFrame(large));
  REQUIRE(rx_driver.ReadFrame(received));
  REQUIRE(received.can_id == 0x18DA1234);
  REQUIRE((received.flags & boat::hil::kCanFlagExtended) != 0);

  tx_driver.Close();
  rx_driver.Close();
  SUCCEED("HIL CAN extended-ID format: PASS");
}

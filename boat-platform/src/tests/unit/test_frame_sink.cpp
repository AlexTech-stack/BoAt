// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* FrameSink is the final validation point before a core::Frame reaches a
   registry/driver. These tests pin the "never silently mangle" contract from
   issue #75: a classic CAN frame over 8 bytes, a CAN FD frame over 64, an
   oversized Ethernet payload, or an RTR frame carrying data is rejected
   rather than truncated. */

#include <catch2/catch_test_macros.hpp>

#include <array>
#include <chrono>
#include <cstdint>
#include <memory>
#include <thread>
#include <vector>

#include "can_bus_registry.h"
#include "core/frame.h"
#include "ethernet_bus_registry.h"
#include "ethernet/ethernet_frame.h"
#include "gateway/grpc_gateway/frame_sink.h"
#include "hal/hal_driver.h"

using namespace boat::hil;

namespace {

class RecordingCanDriver : public IHalDriver {
 public:
  bool Open() override { return true; }
  void Close() override {}
  bool ReadFrame(CanFrame&) override {
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    return false;
  }
  bool WriteFrame(const CanFrame& f) override {
    written.push_back(f);
    return !fail;
  }
  CanInterfaceInfo GetInfo() const override { return {}; }

  bool fail{false};
  std::vector<CanFrame> written;
};

class RecordingEthernetDriver : public IEthernetDriver {
 public:
  bool Open() override { return true; }
  void Close() override {}
  bool ReadFrame(EthernetFrame&) override {
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    return false;
  }
  bool WriteFrame(const EthernetFrame& f) override {
    written.push_back(f);
    return !fail;
  }

  bool fail{false};
  std::vector<EthernetFrame> written;
};

struct Fixture {
  boat::core::EventBus bus;
  CanBusRegistry can_registry;
  EthernetBusRegistry eth_registry;
  RecordingCanDriver* can_driver = nullptr;
  RecordingEthernetDriver* eth_driver = nullptr;
  boat::gateway::FrameSink sink{can_registry, eth_registry};

  Fixture() {
    auto can = std::make_shared<RecordingCanDriver>();
    can_driver = can.get();
    REQUIRE(can_registry.Add("vcan0", std::move(can), bus));

    auto eth = std::make_unique<RecordingEthernetDriver>();
    eth_driver = eth.get();
    REQUIRE(eth_registry.Add("veth0", std::move(eth)));
  }
};

}  // namespace

TEST_CASE("FrameSink rejects classic CAN frames and FD frames that do not fit",
          "[unit][frame_sink][can]") {
  Fixture f;

  auto classic_too_long = boat::core::Frame::FromCan(
      "vcan0", 0x123, 9, 0, std::vector<std::uint8_t>(9, 0xAA));
  REQUIRE_FALSE(f.sink.Publish(classic_too_long));
  REQUIRE(f.can_driver->written.empty());

  auto classic = boat::core::Frame::FromCan(
      "vcan0", 0x123, 8, 0, std::vector<std::uint8_t>(8, 0xAA));
  REQUIRE(f.sink.Publish(classic));
  REQUIRE(f.can_driver->written.size() == 1);
  REQUIRE(f.can_driver->written[0].dlc == 8);

  auto fd = boat::core::Frame::FromCan(
      "vcan0", 0x123, 16, 0, std::vector<std::uint8_t>(16, 0x55),
      /*is_fd=*/true);
  REQUIRE(f.sink.Publish(fd));
  REQUIRE(f.can_driver->written.size() == 2);
  REQUIRE(f.can_driver->written[1].dlc == 16);
  REQUIRE((f.can_driver->written[1].flags & kCanFdFlagFdf) != 0);

  auto fd_too_long = boat::core::Frame::FromCan(
      "vcan0", 0x123, 65, 0, std::vector<std::uint8_t>(65, 0x11),
      /*is_fd=*/true);
  REQUIRE_FALSE(f.sink.Publish(fd_too_long));
  REQUIRE(f.can_driver->written.size() == 2);
}

TEST_CASE("FrameSink preserves extended identifiers and handles RTR without data",
          "[unit][frame_sink][can]") {
  Fixture f;

  auto extended = boat::core::Frame::FromCan(
      "vcan0", 0x123, 1, kCanFlagExtended, std::vector<std::uint8_t>{0xAA});
  REQUIRE(f.sink.Publish(extended));
  REQUIRE(f.can_driver->written.size() == 1);
  REQUIRE(f.can_driver->written[0].can_id == 0x123);
  REQUIRE((f.can_driver->written[0].flags & kCanFlagExtended) != 0);

  auto rtr = boat::core::Frame::FromCan(
      "vcan0", 0x321, 8, kCanFlagRtr, {}, /*is_fd=*/false,
      /*preserve_dlc=*/true);
  REQUIRE(f.sink.Publish(rtr));
  REQUIRE(f.can_driver->written.size() == 2);
  REQUIRE(f.can_driver->written[1].dlc == 8);
  REQUIRE((f.can_driver->written[1].flags & kCanFlagRtr) != 0);

  auto rtr_with_data = boat::core::Frame::FromCan(
      "vcan0", 0x321, 1, kCanFlagRtr, std::vector<std::uint8_t>{0xAA});
  REQUIRE_FALSE(f.sink.Publish(rtr_with_data));

  auto rtr_fd = boat::core::Frame::FromCan(
      "vcan0", 0x321, 0, kCanFlagRtr | kCanFdFlagFdf, {}, /*is_fd=*/true);
  REQUIRE_FALSE(f.sink.Publish(rtr_fd));
  REQUIRE(f.can_driver->written.size() == 2);
}

TEST_CASE("FrameSink rejects oversized Ethernet payloads", "[unit][frame_sink][ethernet]") {
  Fixture f;
  std::array<std::uint8_t, 6> dm{0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
  std::array<std::uint8_t, 6> sm{0x02, 0, 0, 0, 0, 1};

  auto ok = boat::core::Frame::FromEthernet(
      "veth0", dm.data(), sm.data(), 0x0800, 0, nullptr, 0, nullptr,
      std::vector<std::uint8_t>(1500, 0xAB));
  REQUIRE(f.sink.Publish(ok));
  REQUIRE(f.eth_driver->written.size() == 1);

  auto too_long = boat::core::Frame::FromEthernet(
      "veth0", dm.data(), sm.data(), 0x0800, 0, nullptr, 0, nullptr,
      std::vector<std::uint8_t>(1501, 0xAB));
  REQUIRE_FALSE(f.sink.Publish(too_long));
  REQUIRE(f.eth_driver->written.size() == 1);
}

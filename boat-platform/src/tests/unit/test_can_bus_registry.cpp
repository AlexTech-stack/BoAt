// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* Shutdown tests for CanBusRegistry: StopAll must not join RX threads while
   holding the registry mutex, because an RX callback may call SendFrame (CanTp
   flow control does) and would then deadlock against the join. */

#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <future>
#include <memory>
#include <string>
#include <thread>

#include "can_bus_registry.h"
#include "core/event/event_bus.h"
#include "hal/hal_driver.h"

using namespace boat::hil;

namespace {

class OneFrameCanDriver : public IHalDriver {
 public:
  bool Open() override { return true; }
  void Close() override {}
  bool ReadFrame(CanFrame& frame) override {
    if (!produced.exchange(true)) {
      frame = CanFrame{};
      frame.can_id = 0x123;
      frame.dlc = 1;
      frame.data[0] = 0xAA;
      return true;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    return false;
  }
  bool WriteFrame(const CanFrame&) override { return true; }
  CanInterfaceInfo GetInfo() const override { return {}; }

 private:
  std::atomic<bool> produced{false};
};

class BlockingCanDriver : public IHalDriver {
 public:
  bool Open() override { return true; }
  void Close() override {}
  bool ReadFrame(CanFrame&) override {
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    return false;
  }
  bool WriteFrame(const CanFrame&) override {
    entered.store(true);
    while (!release.load(std::memory_order_relaxed)) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return true;
  }
  CanInterfaceInfo GetInfo() const override { return {}; }

  std::atomic<bool> entered{false};
  std::atomic<bool> release{false};
};

class FailingCanDriver : public IHalDriver {
 public:
  bool Open() override { return true; }
  void Close() override {}
  bool ReadFrame(CanFrame&) override {
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    return false;
  }
  bool WriteFrame(const CanFrame&) override { return false; }
  CanInterfaceInfo GetInfo() const override { return {}; }
};

/* No RX frames: the metadata test is about the synchronous self-sent echo,
   and must not race an RX callback against its stack-local assertions. */
class SilentCanDriver : public IHalDriver {
 public:
  bool Open() override { return true; }
  void Close() override {}
  bool ReadFrame(CanFrame&) override {
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    return false;
  }
  bool WriteFrame(const CanFrame&) override { return true; }
  CanInterfaceInfo GetInfo() const override { return {}; }
};

}  // namespace

TEST_CASE("CanBusRegistry StopAll joins without holding the send mutex",
          "[unit][can_registry]") {
  boat::core::EventBus bus;
  CanBusRegistry registry;

  std::atomic<bool> entered{false};
  std::atomic<bool> release{false};
  std::atomic<bool> sent{false};

  registry.SubscribeFrame([&](const boat::core::Frame&) {
    entered.store(true);
    while (!release.load(std::memory_order_relaxed)) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    if (!sent.exchange(true)) {
      CanFrame frame{};
      frame.can_id = 0x321;
      frame.dlc = 1;
      frame.data[0] = 0x55;
      // This is what CanTp's FC send does from on_frame.
      (void)registry.SendFrame("can0", frame);
    }
  });

  // Subscribe before Add. Add starts the RX thread, which can deliver the
  // driver's single frame before a subscription made afterwards exists.
  REQUIRE(registry.Add("can0", std::make_shared<OneFrameCanDriver>(), bus));

  for (int i = 0; i < 500 && !entered.load(); ++i) {
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
  }
  REQUIRE(entered.load());

  // StopAll enters the join first; the callback is released only afterwards.
  auto stopping = std::async(std::launch::async, [&]() { registry.StopAll(); });
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  release.store(true);

  REQUIRE(stopping.wait_for(std::chrono::seconds(5)) == std::future_status::ready);
  stopping.get();

  // Idempotent: the destructor calls it again.
  registry.StopAll();
}

TEST_CASE("CanBusRegistry releases its lock while a write is blocked",
          "[unit][can_registry][tx]") {
  boat::core::EventBus bus;
  CanBusRegistry registry;

  auto blocker = std::make_shared<BlockingCanDriver>();
  auto* blocker_ptr = blocker.get();
  REQUIRE(registry.Add("can0", std::move(blocker), bus));
  REQUIRE(registry.Add("can1", std::make_shared<OneFrameCanDriver>(), bus));

  CanFrame frame{};
  frame.can_id = 0x123;
  frame.dlc = 1;

  auto sending = std::async(std::launch::async, [&]() {
    return registry.SendFrame("can0", frame);
  });

  for (int i = 0; i < 500 && !blocker_ptr->entered.load(); ++i) {
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  REQUIRE(blocker_ptr->entered.load());

  // These all take bridges_mutex_. They must return while write() is parked.
  REQUIRE(registry.Has("can1"));
  REQUIRE(registry.Interfaces().size() == 2);

  blocker_ptr->release.store(true);
  REQUIRE(sending.wait_for(std::chrono::seconds(5)) == std::future_status::ready);
  REQUIRE(sending.get());
}

TEST_CASE("CanBusRegistry SubscribeFrame preserves iface and self-sent flags",
          "[unit][can_registry][tx]") {
  boat::core::EventBus bus;
  CanBusRegistry registry;
  REQUIRE(registry.Add("can0", std::make_shared<SilentCanDriver>(), bus));

  bool got = false;
  std::string received_iface;
  std::uint8_t received_flags = 0;
  std::uint32_t received_id = 0;
  std::uint64_t received_timestamp = 0;
  registry.SubscribeFrame([&](const boat::core::Frame& f) {
    got = true;
    received_iface = f.iface();
    received_flags = f.can_meta().flags;
    received_id = f.can_meta().can_id;
    received_timestamp = f.timestamp_ns();
  });

  CanFrame frame{};
  frame.can_id = 0x123;
  frame.dlc = 2;
  frame.data[0] = 0xAA;
  frame.data[1] = 0xBB;
  frame.timestamp_ns = 123456;
  REQUIRE(registry.SendFrame("can0", frame));
  REQUIRE(got);
  REQUIRE(received_iface == "can0");
  REQUIRE((received_flags & 0x08) != 0);  // BOAT_CAN_FLAG_SELF_SENT
  REQUIRE(received_id == 0x123);
  REQUIRE(received_timestamp == 123456);  // preserved source time, not wall clock

  // Join the RX thread while the callback's captured locals are still alive.
  registry.StopAll();
}

TEST_CASE("CanBusRegistry failed writes are rejected and not echoed",
          "[unit][can_registry][tx]") {
  boat::core::EventBus bus;
  CanBusRegistry registry;
  REQUIRE(registry.Add("can0", std::make_shared<FailingCanDriver>(), bus));

  int echoes = 0;
  registry.SubscribeFrame([&](const boat::core::Frame&) { ++echoes; });

  CanFrame frame{};
  frame.can_id = 0x123;
  frame.dlc = 1;
  REQUIRE_FALSE(registry.SendFrame("can0", frame));
  REQUIRE(echoes == 0);
}

// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* Shutdown tests for CanBusRegistry: StopAll must not join RX threads while
   holding the registry mutex, because an RX callback may call SendFrame (CanTp
   flow control does) and would then deadlock against the join. */

#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <chrono>
#include <future>
#include <memory>
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

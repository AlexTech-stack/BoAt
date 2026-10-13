// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <future>
#include <thread>

#include "signal/signal_router.h"

TEST_CASE("SignalRouter subscribe publish and unsubscribe", "[unit][signal_router]") {
  boat::core::SignalRouter router;

  SECTION("Subscribe and publish round-trip with filtering") {
    std::atomic<int> hits{0};
    boat::core::FilterPredicate filter{
        .signal_id = 10,
        .tick_min = 5,
        .tick_max = 20,
        .comparator =
            [](const boat::core::SignalEvent& event) {
              return std::holds_alternative<double>(event.value) && std::get<double>(event.value) > 1.0;
            },
    };

    const auto handle =
        router.Subscribe(10, filter, [&](const boat::core::SignalEvent&) { hits.fetch_add(1); });

    router.Publish(boat::core::SignalEvent{.signal_id = 10, .tick = 6, .value = 2.5});
    for (int i = 0; i < 100 && hits.load() < 1; ++i) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    REQUIRE(hits.load() == 1);

    router.Publish(boat::core::SignalEvent{.signal_id = 10, .tick = 7, .value = 0.5});
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    REQUIRE(hits.load() == 1);

    router.Unsubscribe(handle);
    router.Publish(boat::core::SignalEvent{.signal_id = 10, .tick = 8, .value = 3.0});
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    REQUIRE(hits.load() == 1);
  }
}

TEST_CASE("SignalRouter reports ring overflow in one aggregate",
          "[unit][signal_router]") {
  // Each subscription has a 4096-slot lock-free ring that drops the newest
  // event when full. The per-subscription count is erased with the
  // subscription, so a metric needs an aggregate; before this the count was
  // written and then never read anywhere (issue #55).
  // Declared before the router so the router is destroyed *first* at scope
  // exit: its destructor joins the dispatcher thread, which is still holding
  // the callback that references these. The other order let a parked callback
  // touch them after they were gone (TSan: data race in operator delete).
  std::atomic<bool> blocked{false};
  std::promise<void> gate;
  const auto gate_future = gate.get_future().share();

  boat::core::SignalRouter router;

  const auto handle = router.Subscribe(
      1, boat::core::FilterPredicate{.signal_id = 1},
      [&](const boat::core::SignalEvent&) {
        blocked.store(true);
        gate_future.wait();  // park the dispatcher so the ring can fill
      });

  router.Publish(boat::core::SignalEvent{.signal_id = 1, .tick = 0, .value = 1.0});
  for (int i = 0; i < 200 && !blocked.load(); ++i) {
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  const bool did_block = blocked.load();

  if (did_block) {
    // The dispatcher is inside the callback; the next 5000 publishes overflow
    // the ~4095 free slots.
    for (std::uint64_t i = 0; i < 5000; ++i) {
      router.Publish(boat::core::SignalEvent{.signal_id = 1, .tick = i + 1, .value = 1.0});
    }
  }
  const std::uint64_t dropped = router.DroppedEventCount();
  gate.set_value();  // never leave the dispatcher parked

  REQUIRE(did_block);
  REQUIRE(dropped > 0);

  // Cumulative, and it must survive the unsubscribe -- that is the whole
  // reason for an aggregate rather than the per-subscription counter.
  router.Unsubscribe(handle);
  REQUIRE(router.DroppedEventCount() >= dropped);
}

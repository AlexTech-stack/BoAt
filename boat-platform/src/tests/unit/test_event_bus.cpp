// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <cstdint>
#include <string>
#include <thread>
#include <vector>

#include "event/event_bus.h"

TEST_CASE("EventBus publish dispatch and unsubscribe behavior", "[unit][event_bus]") {
  boat::core::EventBus bus;

  int type_a_hits = 0;
  int type_b_hits = 0;

  const auto a_handle = bus.Subscribe(1, [&](const boat::core::BusEvent& event) {
    REQUIRE(event.type == 1);
    REQUIRE(std::get<std::string>(event.payload) == "payload-a");
    ++type_a_hits;
  });

  bus.Subscribe(2, [&](const boat::core::BusEvent& event) {
    REQUIRE(event.type == 2);
    REQUIRE(std::get<std::int64_t>(event.payload) == 99);
    ++type_b_hits;
  });

  bus.Publish(boat::core::BusEvent{.type = 1, .payload = std::string("payload-a"), .tick = 1});
  bus.Publish(boat::core::BusEvent{.type = 2, .payload = std::int64_t{99}, .tick = 1});
  bus.Dispatch();

  REQUIRE(type_a_hits == 1);
  REQUIRE(type_b_hits == 1);

  bus.Unsubscribe(a_handle);
  bus.Publish(boat::core::BusEvent{.type = 1, .payload = std::string("payload-a"), .tick = 2});
  bus.Dispatch();
  REQUIRE(type_a_hits == 1);
}

TEST_CASE("EventBus Clear drops queued events but keeps subscriptions", "[unit][event_bus]") {
  boat::core::EventBus bus;
  int hits = 0;
  bus.Subscribe(7, [&](const boat::core::BusEvent&) { ++hits; });

  bus.Publish(boat::core::BusEvent{.type = 7, .payload = std::int64_t{1}, .tick = 1});
  bus.Clear();
  bus.Dispatch();
  REQUIRE(hits == 0);

  bus.Publish(boat::core::BusEvent{.type = 7, .payload = std::int64_t{2}, .tick = 2});
  bus.Dispatch();
  REQUIRE(hits == 1);
}

TEST_CASE("EventBus drops events for types with no subscriber at publish time",
          "[unit][event_bus]") {
  boat::core::EventBus bus;
  int hits = 0;

  bus.Publish(boat::core::BusEvent{.type = 1, .payload = std::int64_t{1}, .tick = 1});
  bus.Subscribe(1, [&](const boat::core::BusEvent&) { ++hits; });
  bus.Dispatch();

  REQUIRE(hits == 0);
}

TEST_CASE("EventBus unsubscribed types do not starve subscribed ones",
          "[unit][event_bus]") {
  boat::core::EventBus bus;
  std::vector<std::int64_t> seen;
  bus.Subscribe(1, [&](const boat::core::BusEvent& event) {
    seen.push_back(std::get<std::int64_t>(event.payload));
  });

  bus.Publish(boat::core::BusEvent{.type = 1, .payload = std::int64_t{1}, .tick = 1});
  bus.Publish(boat::core::BusEvent{.type = 2, .payload = std::int64_t{2}, .tick = 2});
  bus.Publish(boat::core::BusEvent{.type = 1, .payload = std::int64_t{3}, .tick = 3});
  bus.Dispatch();

  REQUIRE(seen == std::vector<std::int64_t>{1, 3});
}

TEST_CASE("EventBus HasSubscribers tracks subscribe and unsubscribe",
          "[unit][event_bus]") {
  boat::core::EventBus bus;
  REQUIRE_FALSE(bus.HasSubscribers(42));
  const auto handle = bus.Subscribe(42, [](const boat::core::BusEvent&) {});
  REQUIRE(bus.HasSubscribers(42));
  bus.Unsubscribe(handle);
  REQUIRE_FALSE(bus.HasSubscribers(42));
}

TEST_CASE("EventBus caps its queue and drops the oldest events",
          "[unit][event_bus]") {
  boat::core::EventBus bus;
  bus.SetMaxQueued(4);
  std::vector<std::int64_t> seen;
  bus.Subscribe(5, [&](const boat::core::BusEvent& event) {
    seen.push_back(std::get<std::int64_t>(event.payload));
  });

  for (std::int64_t i = 0; i < 10; ++i) {
    bus.Publish(boat::core::BusEvent{.type = 5, .payload = i, .tick = static_cast<std::uint64_t>(i)});
  }
  bus.Dispatch();

  REQUIRE(seen == std::vector<std::int64_t>{6, 7, 8, 9});
  REQUIRE(bus.DroppedEventCount() == 6);

  // Dispatch drained the queue, so the next burst fits without further drops.
  for (std::int64_t i = 10; i < 14; ++i) {
    bus.Publish(boat::core::BusEvent{.type = 5, .payload = i, .tick = static_cast<std::uint64_t>(i)});
  }
  bus.Dispatch();
  REQUIRE(bus.DroppedEventCount() == 6);
  REQUIRE(seen.size() == 8);
}

TEST_CASE("EventBus Clear keeps the drop counter and subscriptions",
          "[unit][event_bus]") {
  boat::core::EventBus bus;
  bus.SetMaxQueued(2);
  int hits = 0;
  bus.Subscribe(6, [&](const boat::core::BusEvent&) { ++hits; });

  for (std::int64_t i = 0; i < 5; ++i) {
    bus.Publish(boat::core::BusEvent{.type = 6, .payload = i, .tick = static_cast<std::uint64_t>(i)});
  }
  REQUIRE(bus.DroppedEventCount() == 3);
  bus.Clear();
  REQUIRE(bus.DroppedEventCount() == 3);
  bus.Dispatch();
  REQUIRE(hits == 0);

  bus.Publish(boat::core::BusEvent{.type = 6, .payload = std::int64_t{9}, .tick = 9});
  bus.Dispatch();
  REQUIRE(hits == 1);
}

TEST_CASE("EventBus accounts for every event under concurrent publish and dispatch",
          "[unit][event_bus]") {
  boat::core::EventBus bus;
  bus.SetMaxQueued(64);
  std::atomic<std::int64_t> delivered{0};
  bus.Subscribe(8, [&](const boat::core::BusEvent&) { delivered.fetch_add(1); });

  std::atomic<std::int64_t> published{0};
  auto publish = [&]() {
    for (std::int64_t i = 0; i < 1000; ++i) {
      bus.Publish(boat::core::BusEvent{.type = 8, .payload = i, .tick = static_cast<std::uint64_t>(i)});
      published.fetch_add(1);
    }
  };
  std::thread first(publish);
  std::thread second(publish);

  while (published.load() < 2000) {
    bus.Dispatch();
    std::this_thread::yield();
  }
  first.join();
  second.join();
  bus.Dispatch();

  REQUIRE(delivered.load() + static_cast<std::int64_t>(bus.DroppedEventCount()) == 2000);
}

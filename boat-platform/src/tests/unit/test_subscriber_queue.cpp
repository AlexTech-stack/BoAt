// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include <catch2/catch_test_macros.hpp>

#include <cstddef>
#include <string>

#include "subscriber_queue.h"

using boat::gateway::MakeSubscriberQueue;
using boat::gateway::StreamDropRegistry;

namespace {

struct TestEvent {
  std::string key;
  int value = 0;
};

}  // namespace

TEST_CASE("SubscriberQueue drops the oldest events at capacity",
          "[unit][subscriber_queue]") {
  StreamDropRegistry drops;
  auto queue = MakeSubscriberQueue<TestEvent>(&drops, "grpc.test.dropped", 3);

  std::size_t dropped = 0;
  {
    std::lock_guard<std::mutex> lock(queue->mutex);
    for (int i = 0; i < 5; ++i) {
      dropped += queue->Push(TestEvent{"e", i});
    }
  }
  queue->ReportDrop(dropped);

  REQUIRE(dropped == 2);
  REQUIRE(queue->queue.size() == 3);
  REQUIRE(queue->queue.front().value == 2);
  REQUIRE(queue->queue.back().value == 4);
  REQUIRE(drops.Count("grpc.test.dropped") == 2);
}

TEST_CASE("SubscriberQueue conflates by key when asked to",
          "[unit][subscriber_queue]") {
  StreamDropRegistry drops;
  auto queue = MakeSubscriberQueue<TestEvent>(
      &drops, "grpc.test.dropped", 3,
      [](const TestEvent& e) { return e.key; });

  std::size_t dropped = 0;
  {
    std::lock_guard<std::mutex> lock(queue->mutex);
    dropped += queue->Push(TestEvent{"a", 1});
    dropped += queue->Push(TestEvent{"b", 1});
    dropped += queue->Push(TestEvent{"c", 1});

    // Full, and "a" already has an entry: replace it in place (latest wins)
    // rather than evicting someone else's current value.
    dropped += queue->Push(TestEvent{"a", 2});
  }
  queue->ReportDrop(dropped);

  REQUIRE(dropped == 1);
  REQUIRE(queue->queue.size() == 3);
  REQUIRE(queue->queue[0].key == "a");
  REQUIRE(queue->queue[0].value == 2);
  REQUIRE(queue->queue[1].key == "b");
  REQUIRE(queue->queue[2].key == "c");
  REQUIRE(drops.Count("grpc.test.dropped") == 1);

  std::size_t more_dropped = 0;
  {
    std::lock_guard<std::mutex> lock(queue->mutex);
    // A key that is not queued still evicts the oldest entry.
    more_dropped += queue->Push(TestEvent{"d", 1});
  }
  queue->ReportDrop(more_dropped);

  REQUIRE(more_dropped == 1);
  REQUIRE(queue->queue.size() == 3);
  REQUIRE(queue->queue[0].key == "b");
  REQUIRE(queue->queue[1].key == "c");
  REQUIRE(queue->queue[2].key == "d");
  REQUIRE(drops.Count("grpc.test.dropped") == 2);
}

TEST_CASE("SubscriberQueue with capacity 0 is unbounded",
          "[unit][subscriber_queue]") {
  StreamDropRegistry drops;
  auto queue = MakeSubscriberQueue<TestEvent>(&drops, "grpc.test.dropped", 0);

  std::size_t dropped = 0;
  {
    std::lock_guard<std::mutex> lock(queue->mutex);
    for (int i = 0; i < 10000; ++i) {
      dropped += queue->Push(TestEvent{"e", i});
    }
  }
  queue->ReportDrop(dropped);

  REQUIRE(dropped == 0);
  REQUIRE(queue->queue.size() == 10000);
  REQUIRE(drops.Count("grpc.test.dropped") == 0);
}

TEST_CASE("SubscriberQueue reports drops without a registry",
          "[unit][subscriber_queue]") {
  auto queue = MakeSubscriberQueue<TestEvent>(nullptr, "grpc.test.dropped", 1);

  std::size_t dropped = 0;
  {
    std::lock_guard<std::mutex> lock(queue->mutex);
    dropped += queue->Push(TestEvent{"a", 1});
    dropped += queue->Push(TestEvent{"b", 2});
  }
  queue->ReportDrop(dropped);

  REQUIRE(dropped == 1);
  REQUIRE(queue->queue.size() == 1);
  REQUIRE(queue->queue.front().value == 2);
}

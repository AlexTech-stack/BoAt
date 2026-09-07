// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <atomic>
#include <mutex>
#include <thread>
#include <vector>

#include "tick_authority.h"

#include "determinism/determinism_engine.h"
#include "event/event_bus.h"
#include "scheduler/sim_clock.h"
#include "scheduler/tick_scheduler.h"

TEST_CASE("TickScheduler lifecycle and stepping", "[unit][tick_scheduler]") {
  boat::core::SimClock clock(42);
  boat::core::EventBus event_bus;
  boat::core::DeterminismEngine determinism(42);
  boat::core::TickScheduler scheduler(clock, event_bus, determinism, 2);

  SECTION("Step advances clock by n when stopped") {
    scheduler.Step(5);
    REQUIRE(clock.tick() == 5);
  }

  SECTION("Start pause resume stop sequence is callable") {
    scheduler.Start();
    scheduler.Pause();
    const std::uint64_t tick_before_step = clock.tick();
    scheduler.Step(3);
    REQUIRE(clock.tick() == tick_before_step + 3);
    scheduler.Resume();
    scheduler.Stop();
    const std::uint64_t tick_at_stop = clock.tick();
    scheduler.Step(3);
    REQUIRE(clock.tick() == tick_at_stop + 3);
  }
}

/* TickScheduler no longer owns a thread. The gateway drives it from a
   TickAuthority phase, so scenario-scoped plugins advance on the same clock as
   the node plugins and replay delivery. These cover that contract. */

TEST_CASE("TickIfRunning only ticks a running, unpaused simulation",
          "[unit][tick_scheduler]") {
  boat::core::SimClock clock(7);
  boat::core::EventBus event_bus;
  boat::core::DeterminismEngine determinism(7);
  boat::core::TickScheduler scheduler(clock, event_bus, determinism);

  // Stopped: the phase runs every tick of the gateway's life and must do
  // nothing while no simulation is active.
  for (int i = 0; i < 10; ++i) scheduler.TickIfRunning();
  REQUIRE(clock.tick() == 0);

  scheduler.Start();
  for (int i = 0; i < 5; ++i) scheduler.TickIfRunning();
  REQUIRE(clock.tick() == 5);

  scheduler.Pause();
  for (int i = 0; i < 10; ++i) scheduler.TickIfRunning();
  REQUIRE(clock.tick() == 5);

  scheduler.Resume();
  for (int i = 0; i < 3; ++i) scheduler.TickIfRunning();
  REQUIRE(clock.tick() == 8);

  scheduler.Stop();
  for (int i = 0; i < 10; ++i) scheduler.TickIfRunning();
  REQUIRE(clock.tick() == 8);
}

TEST_CASE("The tick hook sees every simulation tick exactly once",
          "[unit][tick_scheduler]") {
  boat::core::SimClock clock(1);
  boat::core::EventBus event_bus;
  boat::core::DeterminismEngine determinism(1);
  boat::core::TickScheduler scheduler(clock, event_bus, determinism);

  std::vector<std::uint64_t> ticks;
  scheduler.SetOnTickHook([&ticks](std::uint64_t t) { ticks.push_back(t); });

  scheduler.Start();
  for (int i = 0; i < 4; ++i) scheduler.TickIfRunning();
  scheduler.Step(2);  // manual steps share the same sequence

  REQUIRE(ticks == std::vector<std::uint64_t>{1, 2, 3, 4, 5, 6});
}

TEST_CASE("Manual stepping cannot collide with the tick phase",
          "[unit][tick_scheduler][determinism]") {
  // DeterminismEngine::BeforeTick throws on a non-increasing tick, so two
  // threads computing clock_.tick() + 1 concurrently used to be able to claim
  // the same tick number and throw. tick_mutex_ makes read-use-advance atomic.
  boat::core::SimClock clock(3);
  boat::core::EventBus event_bus;
  boat::core::DeterminismEngine determinism(3);
  boat::core::TickScheduler scheduler(clock, event_bus, determinism);

  std::mutex log_mutex;
  std::vector<std::uint64_t> ticks;
  scheduler.SetOnTickHook([&](std::uint64_t t) {
    std::lock_guard<std::mutex> lock(log_mutex);
    ticks.push_back(t);
  });

  scheduler.Start();
  std::atomic<bool> threw{false};
  auto hammer = [&](bool stepping) {
    try {
      for (int i = 0; i < 500; ++i) {
        if (stepping) scheduler.Step(1);
        else          scheduler.TickIfRunning();
      }
    } catch (...) {
      threw.store(true);
    }
  };
  std::thread a(hammer, true);
  std::thread b(hammer, false);
  a.join();
  b.join();
  scheduler.Stop();

  REQUIRE_FALSE(threw.load());
  REQUIRE(ticks.size() == 1000);
  // Strictly increasing with no duplicates and no gaps.
  std::sort(ticks.begin(), ticks.end());
  for (std::size_t i = 0; i < ticks.size(); ++i) {
    REQUIRE(ticks[i] == i + 1);
  }
  REQUIRE(clock.tick() == 1000);
}

TEST_CASE("A TickAuthority phase drives the simulation pipeline",
          "[unit][tick_scheduler]") {
  boat::core::SimClock clock(5);
  boat::core::EventBus event_bus;
  boat::core::DeterminismEngine determinism(5);
  boat::core::TickScheduler scheduler(clock, event_bus, determinism);

  boat::hil::TickAuthority authority;
  authority.AddPhase("sim_plugins", [&scheduler](std::uint64_t) {
    scheduler.TickIfRunning();
  });

  authority.StepFor(10);
  REQUIRE(clock.tick() == 0);  // no simulation running yet

  scheduler.Start();
  authority.StepFor(10);
  REQUIRE(clock.tick() == 10);

  scheduler.Stop();
  authority.StepFor(10);
  REQUIRE(clock.tick() == 10);
}

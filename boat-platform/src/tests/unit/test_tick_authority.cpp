// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* TickAuthority is the ordering guarantee Tier 2 is built on: one clock, one
   ordered phase list, each phase run to completion before the tick advances.
   The gateway's node plugins and its replay engine become two phases here
   instead of two independent wall-clock threads.

   The load-bearing test is the last one -- same phases, same steps, identical
   interleaving, with a contending thread running alongside. That is exactly
   the comparison boat_determinism_replay currently cannot make. */

#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <chrono>
#include <string>
#include <thread>
#include <vector>

#include "tick_authority.h"

using boat::hil::TickAuthority;
using boat::hil::TimeSource;
using namespace std::chrono;

TEST_CASE("Phases run in registration order every tick", "[unit][tick-authority]") {
  TickAuthority ta;
  std::vector<std::string> log;

  ta.AddPhase("first",  [&log](std::uint64_t t) { log.push_back("a" + std::to_string(t)); });
  ta.AddPhase("second", [&log](std::uint64_t t) { log.push_back("b" + std::to_string(t)); });
  ta.AddPhase("third",  [&log](std::uint64_t t) { log.push_back("c" + std::to_string(t)); });

  REQUIRE(ta.PhaseNames() == std::vector<std::string>{"first", "second", "third"});

  ta.StepFor(3);

  REQUIRE(log == std::vector<std::string>{
      "a1", "b1", "c1",
      "a2", "b2", "c2",
      "a3", "b3", "c3"});
  REQUIRE(ta.CurrentTick() == 3);
}

TEST_CASE("A phase sees every tick exactly once", "[unit][tick-authority]") {
  TickAuthority ta;
  std::vector<std::uint64_t> ticks;
  ta.AddPhase("collect", [&ticks](std::uint64_t t) { ticks.push_back(t); });

  ta.StepFor(5);
  ta.StepFor(5);  // resumes from where it left off

  std::vector<std::uint64_t> expected{1, 2, 3, 4, 5, 6, 7, 8, 9, 10};
  REQUIRE(ticks == expected);
  REQUIRE(ta.CurrentTick() == 10);
}

TEST_CASE("A throwing phase does not stop the clock", "[unit][tick-authority]") {
  TickAuthority ta;
  int good = 0;

  ta.AddPhase("throws", [](std::uint64_t) { throw std::runtime_error("plugin blew up"); });
  ta.AddPhase("good",   [&good](std::uint64_t) { ++good; });

  ta.StepFor(4);

  // The later phase still ran on every tick, and the failures are visible
  // rather than silent.
  REQUIRE(good == 4);
  REQUIRE(ta.PhaseErrorCount() == 4);
  REQUIRE(ta.CurrentTick() == 4);
}

TEST_CASE("Phases cannot be added under a running clock", "[unit][tick-authority]") {
  TickAuthority ta;
  std::atomic<int> ran{0};
  ta.AddPhase("counted", [&ran](std::uint64_t) { ran.fetch_add(1); });

  REQUIRE(ta.Start(milliseconds(1), TimeSource::kRealTime));
  REQUIRE(ta.IsRunning());
  REQUIRE_FALSE(ta.Start(milliseconds(1), TimeSource::kRealTime));  // already running

  ta.AddPhase("late", [](std::uint64_t) {});
  REQUIRE(ta.PhaseNames() == std::vector<std::string>{"counted"});

  std::this_thread::sleep_for(milliseconds(30));
  ta.Stop();
  ta.Stop();  // idempotent

  REQUIRE_FALSE(ta.IsRunning());
  REQUIRE(ran.load() > 0);
  REQUIRE(ta.CurrentTick() == static_cast<std::uint64_t>(ran.load()));
}

TEST_CASE("A virtual clock advances the authority without wall time",
          "[unit][tick-authority]") {
  TickAuthority ta;
  std::atomic<std::uint64_t> seen{0};
  ta.AddPhase("count", [&seen](std::uint64_t t) { seen.store(t); });

  const auto wall_start = steady_clock::now();
  REQUIRE(ta.Start(milliseconds(10), TimeSource::kVirtual));
  while (seen.load() < 500) std::this_thread::yield();
  ta.Stop();
  const auto wall = steady_clock::now() - wall_start;

  // 500 ticks of 10 ms is five seconds of simulated time.
  REQUIRE(seen.load() >= 500);
  REQUIRE(wall < seconds(3));
}

TEST_CASE("Phase interleaving is identical across runs under contention",
          "[unit][tick-authority][determinism]") {
  // Two phases standing in for the node plugins and the replay engine. Under
  // the old design these were separate threads and their interleaving was
  // decided by the host scheduler; here it is decided by the phase order.
  auto run = [] {
    TickAuthority ta;
    std::vector<std::string> log;
    ta.AddPhase("plugins", [&log](std::uint64_t t) { log.push_back("tick" + std::to_string(t)); });
    ta.AddPhase("replay",  [&log](std::uint64_t t) {
      if (t % 3 == 0) log.push_back("frame@" + std::to_string(t));
    });
    ta.StepFor(50);
    return log;
  };

  const auto a = run();

  std::atomic<bool> stop{false};
  std::vector<std::thread> noise;
  for (int i = 0; i < 4; ++i) {
    noise.emplace_back([&stop] { while (!stop.load()) { /* contend */ } });
  }
  const auto b = run();
  stop.store(true);
  for (auto& t : noise) t.join();

  REQUIRE(a == b);
  REQUIRE(a.size() == 50 + 16);          // 50 ticks + every third carrying a frame
  REQUIRE(a[2] == "tick3");
  REQUIRE(a[3] == "frame@3");            // replay always lands after plugins
}

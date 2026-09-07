// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* Tier 1 of the clock-coupling work: a tick timer that advances logically
   instead of against wall time, selectable per deployment.

   Nothing is wired to it yet -- ReplayController and the node tick thread
   still call the env-driven factory, which still yields a real-time timer by
   default. These tests pin the seam itself: the virtual backend advances
   without consuming wall time, and the selection fails open to real time so a
   HIL bench can't be made virtual by a typo. */

#include <catch2/catch_test_macros.hpp>

#include <chrono>
#include <cstdlib>
#include <thread>

#include "pdu/tick_timer.h"

using boat::hil::TickTimer;
using boat::hil::TimeSource;
using boat::hil::TimerfdTickTimer;
using boat::hil::VirtualTickTimer;
using namespace std::chrono;

namespace {

struct EnvGuard {
  explicit EnvGuard(const char* value) {
    if (value) ::setenv("BOAT_TIME_SOURCE", value, 1);
    else       ::unsetenv("BOAT_TIME_SOURCE");
  }
  ~EnvGuard() { ::unsetenv("BOAT_TIME_SOURCE"); }
};

}  // namespace

TEST_CASE("BOAT_TIME_SOURCE selects the backend", "[unit][tick-timer]") {
  SECTION("unset defaults to real time") {
    EnvGuard g(nullptr);
    REQUIRE(TickTimer::TimeSourceFromEnv() == TimeSource::kRealTime);
    auto t = TickTimer::Create(milliseconds(1));
    REQUIRE(dynamic_cast<TimerfdTickTimer*>(t.get()) != nullptr);
  }

  SECTION("\"virtual\" selects the logical clock") {
    EnvGuard g("virtual");
    REQUIRE(TickTimer::TimeSourceFromEnv() == TimeSource::kVirtual);
    auto t = TickTimer::Create(milliseconds(1));
    REQUIRE(dynamic_cast<VirtualTickTimer*>(t.get()) != nullptr);
  }

  SECTION("unrecognised values fail open to real time") {
    // A typo must never quietly stop a HIL run from pacing real hardware.
    for (const char* v : {"", "realtime", "Virtual", "virtual ", "1", "yes"}) {
      EnvGuard g(v);
      INFO("BOAT_TIME_SOURCE=" << v);
      REQUIRE(TickTimer::TimeSourceFromEnv() == TimeSource::kRealTime);
    }
  }

  SECTION("explicit source ignores the environment") {
    EnvGuard g("virtual");
    auto t = TickTimer::Create(milliseconds(1), TimeSource::kRealTime);
    REQUIRE(dynamic_cast<TimerfdTickTimer*>(t.get()) != nullptr);
  }
}

TEST_CASE("VirtualTickTimer advances without consuming wall time",
          "[unit][tick-timer]") {
  auto t = TickTimer::Create(milliseconds(1), TimeSource::kVirtual);

  const auto wall_start = steady_clock::now();
  for (int i = 0; i < 10000; ++i) REQUIRE(t->WaitForNextTick());
  const auto wall_elapsed = steady_clock::now() - wall_start;

  // 10,000 ticks of 1 ms is ten seconds of virtual time...
  REQUIRE(t->TickCount() == 10000);
  REQUIRE(duration_cast<milliseconds>(t->Elapsed()) == milliseconds(10000));
  // ...that cost essentially no real time. A real timer would have taken 10 s.
  REQUIRE(wall_elapsed < seconds(2));
}

TEST_CASE("VirtualTickTimer WaitUntil jumps to the deadline",
          "[unit][tick-timer]") {
  auto t = TickTimer::Create(milliseconds(1), TimeSource::kVirtual);
  const auto anchor = steady_clock::now();

  REQUIRE(t->WaitUntil(anchor + milliseconds(500)));
  REQUIRE(t->Elapsed() >= milliseconds(499));

  SECTION("a deadline in the past fires without rewinding the clock") {
    const auto before = t->Elapsed();
    REQUIRE(t->WaitUntil(anchor - milliseconds(100)));
    REQUIRE(t->Elapsed() >= before);
  }
}

TEST_CASE("VirtualTickTimer Stop halts waiting", "[unit][tick-timer]") {
  auto t = TickTimer::Create(milliseconds(1), TimeSource::kVirtual);
  REQUIRE(t->WaitForNextTick());
  t->Stop();
  REQUIRE_FALSE(t->WaitForNextTick());
  REQUIRE_FALSE(t->WaitUntil(steady_clock::now() + seconds(1)));
}

TEST_CASE("VirtualTickTimer produces identical timelines across runs",
          "[unit][tick-timer][determinism]") {
  // The property the whole tier exists for: same call sequence, same clock,
  // regardless of what else the host is doing.
  auto run = [] {
    auto t = TickTimer::Create(microseconds(250), TimeSource::kVirtual);
    for (int i = 0; i < 400; ++i) t->WaitForNextTick();
    return std::pair{t->TickCount(), t->Elapsed().count()};
  };
  const auto a = run();
  std::thread noise([] {
    const auto until = steady_clock::now() + milliseconds(20);
    while (steady_clock::now() < until) { /* contend */ }
  });
  const auto b = run();
  noise.join();

  REQUIRE(a == b);
  REQUIRE(a.first == 400);
  REQUIRE(a.second == duration_cast<nanoseconds>(milliseconds(100)).count());
}

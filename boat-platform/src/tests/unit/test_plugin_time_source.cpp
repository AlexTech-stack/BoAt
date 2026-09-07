// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* ABI v9 hands plugins the host's clock (BoatNowNsFn) instead of leaving each
   one to call a system clock and infer elapsed time for itself. Three separate
   components got that inference wrong before this existed -- the PDU router
   read a tick counter as milliseconds, replay multiplied milliseconds by
   nanoseconds-per-tick, and the C++ test harness assumed 10 ms per tick -- so
   the point of the vtable entry is to leave nothing to infer.

   The probe plugin republishes what it is given on the signal bus, which is
   what makes the wiring observable from here. */

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>

#include <atomic>
#include <chrono>
#include <string>
#include <vector>

#include "core/plugin/plugin_manager.h"
#include "boat/plugin.h"
#include "tick_authority.h"

using namespace std::chrono;

TEST_CASE("The host clock reaches a loaded plugin", "[unit][plugin][time-source]") {
  boat::core::PluginManager pm;

  std::atomic<std::uint64_t> supplied{0};
  pm.SetTimeSource([&supplied]() { return supplied.load(); });

  std::vector<double> observed;
  pm.SetBusPublisher([&observed](const char* name, double value) {
    if (std::string(name) == "probe.now_ns") observed.push_back(value);
  });

  pm.Load(PROBE_SO, "{}");

  supplied.store(1'000'000);
  pm.TickAll(1);
  supplied.store(7'500'000);
  pm.TickAll(2);

  pm.ShutdownAll();

  REQUIRE(observed.size() == 2);
  REQUIRE(observed[0] == 1'000'000.0);
  REQUIRE(observed[1] == 7'500'000.0);
}

TEST_CASE("A plugin loads fine when the host supplies no clock",
          "[unit][plugin][time-source]") {
  // set_time_source is optional in both directions: a host that never calls
  // SetTimeSource must still be able to load a plugin that implements it.
  boat::core::PluginManager pm;
  std::vector<std::string> names;
  pm.SetBusPublisher([&names](const char* name, double) { names.emplace_back(name); });

  REQUIRE_NOTHROW(pm.Load(PROBE_SO, "{}"));
  pm.TickAll(1);
  pm.ShutdownAll();

  for (const auto& n : names) REQUIRE(n != "probe.now_ns");
}

TEST_CASE("A TickAuthority clock advances and is monotonic",
          "[unit][plugin][time-source]") {
  boat::hil::TickAuthority authority;

  // Before Start(), NowNs derives from completed ticks.
  REQUIRE(authority.NowNs() == 0);
  authority.StepFor(5);
  REQUIRE(authority.NowNs() == 5 * duration_cast<nanoseconds>(milliseconds(1)).count());

  // A virtual clock advances without wall time passing.
  boat::hil::TickAuthority virt;
  std::atomic<std::uint64_t> seen{0};
  virt.AddPhase("watch", [&seen](std::uint64_t t) { seen.store(t); });
  REQUIRE(virt.Start(milliseconds(10), boat::hil::TimeSource::kVirtual));
  while (seen.load() < 200) std::this_thread::yield();
  const auto now = virt.NowNs();
  virt.Stop();

  // 200 ticks of 10 ms is at least two seconds of simulated time.
  REQUIRE(now >= duration_cast<nanoseconds>(seconds(2)).count());
}

TEST_CASE("A plugin built against the previous ABI is refused",
          "[unit][plugin][abi]") {
  // v9 added set_time_source to the vtable. A v8 plugin's vtable is a field
  // short, so loading one would read past the end of what it allocated --
  // the version check has to catch it at the door.
  boat::core::PluginManager pm;

  REQUIRE_THROWS_WITH(pm.Load(STALE_ABI_PLUGIN_SO, "{}"),
                      Catch::Matchers::ContainsSubstring("ABI version mismatch") &&
                      Catch::Matchers::ContainsSubstring(
                          std::to_string(BOAT_PLUGIN_ABI_VERSION - 1)) &&
                      Catch::Matchers::ContainsSubstring(
                          std::to_string(BOAT_PLUGIN_ABI_VERSION)));

  // ...and nothing is left registered behind it.
  REQUIRE(pm.List().empty());
}

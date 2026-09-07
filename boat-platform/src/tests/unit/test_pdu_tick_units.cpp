// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* The node tick thread hands PluginManager::TickAll a raw tick counter, while
   PduRouter::OnTick / TransmissionEngine::OnTick interpret their argument as a
   count of *milliseconds* (see test_pdu_router.cpp, which drives the engine
   with OnTick(100) / OnTick(199) / OnTick(200) against cycle_ms = 100).

   At the compiled-in 1 ms tick the two coincide, so the confusion is invisible.
   Set BOAT_NODE_TICK_US and they diverge: the counter advances once per tick
   interval, not once per millisecond, and every cyclic schedule and deadline
   monitor in the router fires early by exactly that ratio.

   These tests pin the contract in wall-clock terms: N ticks of D microseconds
   is N*D/1000 milliseconds of elapsed time, whatever D happens to be. */

#include <catch2/catch_test_macros.hpp>

#include <cstdlib>
#include <string>
#include <vector>

#include "core/plugin/plugin_manager.h"
#include "core/pdu_router_interface.h"
#include "pdu/pdu_types.h"

namespace {

/* Load pdu_router.so, drive it for `ticks` ticks with the node tick interval
   set to `tick_us`, and return how many CAN frames the cyclic schedule
   produced. */
int CountCyclicSends(const char* tick_us_env, std::uint64_t ticks,
                     std::uint32_t cycle_ms) {
  if (tick_us_env != nullptr) {
    ::setenv("BOAT_NODE_TICK_US", tick_us_env, 1);
  } else {
    ::unsetenv("BOAT_NODE_TICK_US");
  }
  ::unsetenv("BOAT_NODE_TICK_MS");

  int sends = 0;
  {
    boat::core::PluginManager pm;
    pm.SetFramePublisher([&sends](const BoatFrame& f) {
      if (f.bus_type == BOAT_BUS_CAN || f.bus_type == BOAT_BUS_CANFD) ++sends;
    });
    pm.Load(PDU_ROUTER_SO, "{}");

    auto* router = static_cast<boat::core::IPduRouter*>(pm.FindService("pdu_router"));
    REQUIRE(router != nullptr);

    boat::hil::PduRoute route;
    route.pdu_id    = 0x321;
    route.transport = boat::hil::PduTransport::kCan;
    route.iface     = "vcan0";
    route.can_id    = 0x321;
    route.schedule.send_type = boat::hil::SendType::kCyclic;
    route.schedule.cycle_ms  = cycle_ms;
    router->AddRoute(route);
    router->SendPdu(0x321, std::vector<std::uint8_t>{0xDE, 0xAD});
    sends = 0;  // ignore the immediate send; count only scheduled ones

    for (std::uint64_t t = 0; t <= ticks; ++t) pm.TickAll(t);
    pm.ShutdownAll();
  }
  ::unsetenv("BOAT_NODE_TICK_US");
  return sends;
}

}  // namespace

TEST_CASE("Cyclic PDU cadence is correct at the default 1 ms tick",
          "[unit][pdu][tick-units]") {
  // 1000 ticks x 1000 us = 1000 ms elapsed; a 100 ms cycle fires 10 times.
  REQUIRE(CountCyclicSends(nullptr, 1000, 100) == 10);
}

TEST_CASE("Cyclic PDU cadence is independent of the configured tick interval",
          "[unit][pdu][tick-units]") {
  // Same 1000 ms of elapsed time, expressed at four different tick rates.
  // The cadence is a wall-clock property, so all four must agree.
  REQUIRE(CountCyclicSends("100",   10000, 100) == 10);  // 100 us ticks
  REQUIRE(CountCyclicSends("250",    4000, 100) == 10);  // 250 us ticks
  REQUIRE(CountCyclicSends("500",    2000, 100) == 10);  // 500 us ticks
  REQUIRE(CountCyclicSends("1000",   1000, 100) == 10);  // 1 ms ticks
}

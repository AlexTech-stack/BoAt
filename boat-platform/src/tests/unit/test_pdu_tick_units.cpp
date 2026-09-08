// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* The node tick thread hands PluginManager::TickAll a raw tick counter, while
   PduRouter::OnTick / TransmissionEngine::OnTick interpret their argument as a
   count of *milliseconds* (see test_pdu_router.cpp, which drives the engine
   with OnTick(100) / OnTick(199) / OnTick(200) against cycle_ms = 100).

   At the compiled-in 1 ms tick the two coincide, so the confusion is invisible.
   Change the tick interval and they diverge: the counter advances once per tick
   interval, not once per millisecond, and every cyclic schedule and deadline
   monitor in the router fires early by exactly that ratio.

   The plugin used to bridge the gap by parsing BOAT_NODE_TICK_US itself and
   multiplying the counter -- guessing the tick interval rather than being told
   it, and guessing wrong for an instance loaded into the simulation-scoped
   PluginManager, which is ticked on a different interval than the node one.
   ABI v9 hands the plugin the host's clock instead (set_time_source), so these
   tests drive that clock rather than the environment.

   Either way the contract they pin is the same, and it is a wall-clock one:
   N ticks of D microseconds is N*D/1000 milliseconds of elapsed time, whatever
   D happens to be. */

#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <vector>

#include "core/plugin/plugin_manager.h"
#include "core/pdu_router_interface.h"
#include "pdu/pdu_types.h"

namespace {

/* Load pdu_router.so against a clock the test owns, drive it for `ticks` ticks
   of `tick_us` each, and return how many CAN frames the cyclic schedule
   produced. */
int CountCyclicSends(std::uint64_t tick_us, std::uint64_t ticks,
                     std::uint32_t cycle_ms) {
  int sends = 0;
  std::uint64_t now_ns = 0;
  {
    boat::core::PluginManager pm;
    /* v9: the plugin reads elapsed time here instead of inferring it from the
       tick counter, so advancing this clock is what makes time pass for it.
       Must be set before Load() -- it is wired in as the plugin loads. */
    pm.SetTimeSource([&now_ns] { return now_ns; });
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

    for (std::uint64_t t = 0; t <= ticks; ++t) {
      pm.TickAll(t);
      now_ns += tick_us * 1000ULL;
    }
    pm.ShutdownAll();
  }
  return sends;
}

}  // namespace

TEST_CASE("Cyclic PDU cadence is correct at the default 1 ms tick",
          "[unit][pdu][tick-units]") {
  // 1000 ticks x 1000 us = 1000 ms elapsed; a 100 ms cycle fires 10 times.
  REQUIRE(CountCyclicSends(1000, 1000, 100) == 10);
}

TEST_CASE("Cyclic PDU cadence is independent of the configured tick interval",
          "[unit][pdu][tick-units]") {
  // Same 1000 ms of elapsed time, expressed at four different tick rates.
  // The cadence is a wall-clock property, so all four must agree.
  REQUIRE(CountCyclicSends(100,  10000, 100) == 10);  // 100 us ticks
  REQUIRE(CountCyclicSends(250,   4000, 100) == 10);  // 250 us ticks
  REQUIRE(CountCyclicSends(500,   2000, 100) == 10);  // 500 us ticks
  REQUIRE(CountCyclicSends(1000,  1000, 100) == 10);  // 1 ms ticks
}

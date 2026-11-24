// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "simulation/simulation_context.h"

namespace boat::core {

SimulationContext::SimulationContext(std::uint64_t seed,
                                     std::size_t worker_threads)
    : clock_(0),
      determinism_(seed),
      event_bus_(),
      signal_router_(),
      fault_injector_(determinism_),
      state_machine_(),
      plugin_manager_(),
      scheduler_(clock_, event_bus_, determinism_, worker_threads) {
  /* Faults are applied where signal events are routed. Without this call the
     gateway accepted and listed scheduled faults but never applied one: only
     the gateway integration test wired the two together manually. It is
     harmless to attach once, here -- FaultInjector::Clear() (called on
     create/reset) is the reset half. */
  signal_router_.SetFaultInjector(&fault_injector_);
}

}  // namespace boat::core

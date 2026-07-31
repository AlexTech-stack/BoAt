# SimulationService Backlog

Gaps found in `SimulationService` while driving it from the Android companion app
against a live gateway (`boat-platform/src/gateway/grpc_gateway/simulation_service_impl.cpp`).

Both items below are **pre-existing behaviour**, not regressions. Neither is
obviously wrong for single-simulation bench use; both are surprising enough to
mislead a client author, and the API shape actively invites the wrong assumption.

---

## 🔴 Simulation state is global, not per-simulation

`CreateSimulation` mints a fresh id and stores a `ScenarioDef` per id in the
`simulations_` map, so the service looks multi-simulation: every RPC takes a
`simulation_id`, and `ListSimulations` returns many.

But the actual state lives in one place. `FillSimulation` reads:

```cpp
out->set_state(ToProtoState(sim_.state_machine().Current()));
out->set_tick(sim_.clock().tick());
```

`sim_` is the single gateway-wide `SimulationContext`. The `simulation_id` selects
which *scenario* is reported, never which *state*. Likewise `StartSimulation`,
`PauseSimulation`, `StepSimulation` and friends all drive the one shared state
machine and scheduler after looking the id up purely to validate it exists.

**Impact.** With two or more simulations created, every one of them reports the
same state and the same tick, and starting one starts "all" of them. A client
showing a list of simulations with per-row state — which is the natural reading
of the API — displays something untrue. Observed directly: two simulations in the
companion app's list, both reporting the state of whichever was last acted on.

**Options.**
- Make the model explicit: document that the gateway hosts exactly one running
  simulation and that `simulation_id` is a scenario selector, not an instance
  handle. Cheapest, and honest.
- Reduce the API to match: drop `ListSimulations`, or have it return at most one.
- Make state genuinely per-simulation: a `SimulationContext` per id. Real work —
  the scheduler, clock and event bus are all currently gateway-scoped.

**Effort:** Small (document) to Large (per-instance state).

---

## 🟡 `ResetSimulation` does not rewind the tick

`ResetSimulation` stops the scheduler and transitions the state machine to `IDLE`,
but never touches the clock:

```cpp
sim_.scheduler().Stop();
if (!sim_.state_machine().Transition(boat::core::SimState::IDLE)) { ... }
FillSimulation(request->simulation_id(), scenario, response->mutable_simulation());
```

There is no `sim_.clock()` reset, so the tick counter survives.

**Impact.** Two surprises, both observed on hardware:
1. Reset reports `IDLE` while the tick still reads whatever it had reached
   (e.g. `tick 11911` immediately after a reset).
2. Because the clock is gateway-scoped, a **newly created** simulation inherits
   the leftover count and starts mid-run — a fresh `CreateSimulation` reported
   `tick 11911` before it had ever been started.

For a platform whose central invariant is determinism, a simulation that does not
begin at tick 0 is a bad default. It also makes tick values useless as a progress
indicator across runs without the client tracking its own baseline.

**Options.**
- Reset the clock in `ResetSimulation` — matches the plain meaning of "reset".
- If the clock is deliberately a free-running gateway clock, say so in
  `simulation.proto` next to `Simulation.tick`, and consider exposing a separate
  per-simulation `ticks_elapsed` so clients have something meaningful to show.

**Effort:** Small, but the choice is a semantics decision, not a code fix.

---

## Note for client authors

`WatchSimulation` is a **state-change** stream, not a progress stream. It
registers an `OnTransition` observer on the state machine and writes only when a
transition fires:

```cpp
const auto observer_token = sim_.state_machine().OnTransition(...);
while (!context->IsCancelled()) {
  if (changed->exchange(false, ...)) { ... writer->Write(response); }
  ...
}
```

A running simulation does not transition, so **no updates are sent while it runs**
and a client relying on this stream shows a frozen tick for exactly the period it
matters. The tick has to be polled via `GetSimulationState`.

This is defensible as designed — it is cheap and it does what its name says — but
the name reads like a general-purpose watch, and the first two clients written
against it (the companion app, and this note's author) both got it wrong. Worth a
comment in `simulation.proto` at minimum; a periodic keepalive write, or an
explicit `tick` field in the transition payload, would remove the trap.

// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <cstdint>
#include <random>

namespace boat::core {

/* Seeds a Mersenne Twister PRNG with (seed_ ⊕ tick) at the start of every
   simulation tick.  This guarantees bit-identical random sequences across
   runs and environments for the same (seed, tick) pair.

   Thread-safety: NOT thread-safe.  Must be called from the coordinator
   thread only (see TickScheduler::ExecuteTick).

   Used by: FaultInjector noise amplitude, plugin random decisions. */
class DeterminismEngine {
 public:
  explicit DeterminismEngine(std::uint64_t seed);

  void BeforeTick(std::uint64_t tick);
  [[nodiscard]] std::uint64_t NextRandom();
  [[nodiscard]] std::uint64_t Seed() const { return seed_; }

  /* Switch to a different seed and forget the last tick, as if the engine had
     been constructed with it. Applying a scenario's own seed at start time is
     what makes `ScenarioDef.seed` mean anything: without it every scenario ran
     on the gateway's fixed seed, so two scenarios asking for different random
     streams got the same one (issue #53). Also used by ResetSimulation, so a
     reset run replays the scenario's stream identically. */
  void SetSeed(std::uint64_t seed);

  /* Forget the last tick, so a rewound clock can start again from tick 1.
     Without this, resetting the clock while this engine still remembers a
     high-water tick makes the next BeforeTick() throw "tick must be
     monotonically increasing" -- the two have to be reset together, which is
     why TickScheduler::Reset() does both under its tick mutex. The RNG is
     reseeded per tick from (seed ^ tick), so nothing else needs clearing. */
  void Reset();

 private:
  std::uint64_t seed_;
  std::uint64_t last_tick_;
  std::mt19937_64 rng_;
};

}  // namespace boat::core

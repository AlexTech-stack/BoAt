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

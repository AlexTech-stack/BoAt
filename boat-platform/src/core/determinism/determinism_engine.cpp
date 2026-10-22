// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "determinism/determinism_engine.h"

#include <limits>
#include <stdexcept>

namespace boat::core {
namespace {

/* SplitMix64 finalizer. The old seed was literally `seed_ ^ tick`, so
   (seed=0, tick=1) and (seed=1, tick=0) selected the same stream, and every
   other XOR collision did too. A full 64-bit domain cannot encode the 128-bit
   (seed, tick) pair injectively, but mixing both halves independently removes
   the structural collisions the old expression made trivial. */
std::uint64_t Mix64(std::uint64_t x) {
  x += 0x9E3779B97F4A7C15ULL;
  x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ULL;
  x = (x ^ (x >> 27)) * 0x94D049BB133111EBULL;
  return x ^ (x >> 31);
}

std::uint64_t TickSeed(std::uint64_t seed, std::uint64_t tick) {
  return Mix64(seed ^ Mix64(tick + 0x9E3779B97F4A7C15ULL));
}

}  // namespace

DeterminismEngine::DeterminismEngine(std::uint64_t seed)
    : seed_(seed), last_tick_(std::numeric_limits<std::uint64_t>::max()), rng_(seed) {}

void DeterminismEngine::BeforeTick(std::uint64_t tick) {
  if (last_tick_ != std::numeric_limits<std::uint64_t>::max() && tick <= last_tick_) {
    throw std::logic_error("tick must be monotonically increasing");
  }
  rng_.seed(TickSeed(seed_, tick));
  last_tick_ = tick;
}

void DeterminismEngine::Reset() {
  last_tick_ = std::numeric_limits<std::uint64_t>::max();
}

void DeterminismEngine::SetSeed(std::uint64_t seed) {
  seed_ = seed;
  last_tick_ = std::numeric_limits<std::uint64_t>::max();
  rng_.seed(seed);
}

std::uint64_t DeterminismEngine::NextRandom() { return rng_(); }

}  // namespace boat::core

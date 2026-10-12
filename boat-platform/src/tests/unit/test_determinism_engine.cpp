// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include <catch2/catch_test_macros.hpp>

#include "determinism/determinism_engine.h"

TEST_CASE("DeterminismEngine produces deterministic sequences", "[unit][determinism_engine]") {
  boat::core::DeterminismEngine a(1234);
  boat::core::DeterminismEngine b(1234);

  for (std::uint64_t tick = 1; tick <= 10; ++tick) {
    a.BeforeTick(tick);
    b.BeforeTick(tick);
    REQUIRE(a.NextRandom() == b.NextRandom());
  }
}

TEST_CASE("Reset lets a rewound run replay the identical stream",
          "[unit][determinism_engine]") {
  boat::core::DeterminismEngine engine(99);

  std::vector<std::uint64_t> first;
  for (std::uint64_t tick = 1; tick <= 5; ++tick) {
    engine.BeforeTick(tick);
    first.push_back(engine.NextRandom());
  }

  // The clock has been rewound, but the engine still remembers tick 5. This is
  // the throw a gateway hit after a session with several simulations, and why
  // SimClock::Reset() and DeterminismEngine::Reset() must happen together.
  REQUIRE_THROWS_AS(engine.BeforeTick(1), std::logic_error);

  engine.Reset();
  std::vector<std::uint64_t> second;
  for (std::uint64_t tick = 1; tick <= 5; ++tick) {
    engine.BeforeTick(tick);
    second.push_back(engine.NextRandom());
  }
  REQUIRE(first == second);
}

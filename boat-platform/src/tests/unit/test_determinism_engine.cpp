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

TEST_CASE("Different seed/tick pairs that collided under XOR select different streams",
          "[unit][determinism_engine]") {
  // The old seed was seed ^ tick. These pairs all had the same old seed and
  // therefore produced the same random sequence.
  const auto stream = [](std::uint64_t seed, std::uint64_t tick) {
    boat::core::DeterminismEngine engine(seed);
    engine.BeforeTick(tick);
    return engine.NextRandom();
  };

  REQUIRE(stream(0, 1) != stream(1, 0));
  REQUIRE(stream(7, 3) != stream(6, 2));
  REQUIRE(stream(0x12345678, 0x9ABCDEF0) != stream(0x9ABCDEF0, 0x12345678));
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

TEST_CASE("SetSeed switches the stream and lets a rewound run start over",
          "[unit][determinism_engine]") {
  // `ScenarioDef.seed` used to be parsed and never applied, so every scenario
  // ran on the gateway's fixed seed (issue #53). Applying a scenario's seed at
  // start time is this call.
  boat::core::DeterminismEngine engine(777);

  engine.BeforeTick(1);
  const std::uint64_t stream_777 = engine.NextRandom();

  // Same seed, repeated: identical stream.
  engine.SetSeed(777);
  REQUIRE(engine.Seed() == 777);
  engine.BeforeTick(1);  // last_tick_ was cleared, so tick 1 is legal again
  REQUIRE(engine.NextRandom() == stream_777);

  // Different scenario, different seed: different stream.
  engine.SetSeed(42);
  REQUIRE(engine.Seed() == 42);
  engine.BeforeTick(1);
  REQUIRE(engine.NextRandom() != stream_777);
}

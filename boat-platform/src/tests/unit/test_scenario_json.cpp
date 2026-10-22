// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>

#include <cstddef>
#include <string>

#include "scenario/scenario_loader.h"

namespace {

std::string NestedArray(std::size_t depth) {
  return std::string(depth, '[') + "0" + std::string(depth, ']');
}

std::string ScenarioWithExtra(const std::string& value) {
  return "{\"id\":\"t\",\"name\":\"n\",\"version\":\"1\","
         "\"duration_ticks\":1,\"seed\":1,\"plugins\":[],"
         "\"signals\":[],\"faults\":[],\"extra\":" +
         value + "}";
}

}  // namespace

TEST_CASE("scenario JSON parser rejects nesting beyond the limit",
          "[unit][validation][scenario]") {
  REQUIRE_THROWS_WITH(
      boat::core::ScenarioLoader::LoadFromJson(ScenarioWithExtra(NestedArray(65))),
      "JSON nesting too deep");
}

TEST_CASE("scenario JSON parser accepts nesting within the limit",
          "[unit][validation][scenario]") {
  const auto scenario =
      boat::core::ScenarioLoader::LoadFromJson(ScenarioWithExtra(NestedArray(10)));
  CHECK(scenario.id == "t");
  CHECK(scenario.duration_ticks == 1);
}

TEST_CASE("scenario JSON keeps 64-bit integers exact above 2^53",
          "[unit][validation][scenario]") {
  constexpr std::uint64_t kLarge = 9007199254740993ULL;  // 2^53 + 1
  constexpr std::uint64_t kMax = 18446744073709551615ULL;
  const std::string json =
      R"({"id":"t","name":"n","version":"1","duration_ticks":)" +
      std::to_string(kLarge) +
      R"(,"seed":)" + std::to_string(kMax) +
      R"(,"plugins":[],"signals":[],"faults":[{"tick":)" +
      std::to_string(kLarge) +
      R"(,"signal_id":"s","fault_type":"noise","magnitude":1.5}]})";
  const auto scenario = boat::core::ScenarioLoader::LoadFromJson(json);
  CHECK(scenario.duration_ticks == kLarge);
  CHECK(scenario.seed == kMax);
  REQUIRE(scenario.faults.size() == 1);
  CHECK(scenario.faults[0].tick == kLarge);
  CHECK(scenario.faults[0].magnitude == 1.5);
}

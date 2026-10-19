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

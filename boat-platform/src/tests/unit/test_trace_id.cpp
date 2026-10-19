// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include <catch2/catch_test_macros.hpp>

#include <string>

#include "store/trace_store/trace_store.h"

TEST_CASE("trace ids are restricted to a safe path component", "[unit][validation]") {
  using boat::store::IsValidTraceId;

  CHECK(IsValidTraceId("trace_2026-10-19.01"));
  CHECK(IsValidTraceId(".hidden"));
  CHECK_FALSE(IsValidTraceId(""));
  CHECK_FALSE(IsValidTraceId("."));
  CHECK_FALSE(IsValidTraceId(".."));
  CHECK_FALSE(IsValidTraceId("../etc/passwd"));
  CHECK_FALSE(IsValidTraceId("a/b"));
  CHECK_FALSE(IsValidTraceId("a b"));
  CHECK_FALSE(IsValidTraceId(std::string(129, 'a')));
}

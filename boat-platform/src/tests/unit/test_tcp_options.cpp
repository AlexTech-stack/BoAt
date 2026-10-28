// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <cstring>
#include <vector>

#include "tcp_plugin.h"
#include "tcp_segment.h"

using namespace boat::tcp;

TEST_CASE("Tcp plugin initial sequence number comes from deterministic config",
          "[unit][tcp][determinism]") {
  BoatPlugin* raw = boat_plugin_create();
  REQUIRE(raw != nullptr);
  REQUIRE(raw->vtable->initialize(raw->ctx, R"({"isn_seed":42})") == 0);
  auto* plugin = static_cast<TcpPlugin*>(raw->ctx);
  REQUIRE(plugin->isn_seed == 42);
  REQUIRE(plugin->next_isn == 42);
  boat_plugin_destroy(raw);
}

TEST_CASE("TCP does not open a raw socket merely because iface is configured",
          "[unit][tcp][standalone]") {
  BoatPlugin* raw = boat_plugin_create();
  REQUIRE(raw != nullptr);
  REQUIRE(raw->vtable->initialize(raw->ctx, R"({"iface":"eth0"})") == 0);
  auto* plugin = static_cast<TcpPlugin*>(raw->ctx);
  REQUIRE(plugin->standalone == false);
  REQUIRE(plugin->raw_sock == -1);
  REQUIRE_FALSE(plugin->raw_rx_thread.joinable());
  boat_plugin_destroy(raw);
}

TEST_CASE("TCP parses JSON booleans for nagle",
          "[unit][tcp][config]") {
  BoatPlugin* raw = boat_plugin_create();
  REQUIRE(raw != nullptr);
  REQUIRE(raw->vtable->initialize(raw->ctx, R"({"nagle":false})") == 0);
  auto* plugin = static_cast<TcpPlugin*>(raw->ctx);
  REQUIRE(plugin->nagle_enabled == false);
  boat_plugin_destroy(raw);
}

TEST_CASE("TCP parses standalone as an explicit opt-in",
          "[unit][tcp][standalone]") {
  BoatPlugin* raw = boat_plugin_create();
  REQUIRE(raw != nullptr);
  REQUIRE(raw->vtable->initialize(raw->ctx, R"({"standalone":true})") == 0);
  auto* plugin = static_cast<TcpPlugin*>(raw->ctx);
  REQUIRE(plugin->standalone == true);
  // No iface means no socket even in standalone mode; this is an error path,
  // not an implicit all-interfaces bind.
  REQUIRE(plugin->raw_sock == -1);
  boat_plugin_destroy(raw);
}

TEST_CASE("TcpRttEstimator follows RFC 6298 and backs off on timeout",
          "[unit][tcp][rtt]") {
  TcpRttEstimator estimator;
  estimator.Reset(1000);
  REQUIRE(estimator.rto_ms == 1000);

  estimator.OnSample(100.0);
  REQUIRE(estimator.have_sample);
  REQUIRE(estimator.srtt_ms == 100.0);
  REQUIRE(estimator.rttvar_ms == 50.0);
  REQUIRE(estimator.rto_ms == 300);

  estimator.OnSample(200.0);
  REQUIRE(estimator.srtt_ms == 112.5);
  REQUIRE(estimator.rttvar_ms == 62.5);
  REQUIRE(estimator.rto_ms == 363);

  estimator.OnTimeout();
  REQUIRE(estimator.rto_ms == 726);
  for (int i = 0; i < 20; ++i) estimator.OnTimeout();
  REQUIRE(estimator.rto_ms == TcpRttEstimator::kMaxRtoMs);

  estimator.Reset(50);
  REQUIRE(estimator.rto_ms == TcpRttEstimator::kMinRtoMs);
  estimator.Reset(100000);
  REQUIRE(estimator.rto_ms == TcpRttEstimator::kMaxRtoMs);
}

TEST_CASE("TCP sequence comparisons are modular across 2^32",
          "[unit][tcp][sequence]") {
  REQUIRE(SeqGreater(0x00000010U, 0xFFFFFFF0U));
  REQUIRE_FALSE(SeqGreater(0xFFFFFFF0U, 0x00000010U));
  REQUIRE(SeqGreaterEqual(0x00000010U, 0xFFFFFFF0U));
  REQUIRE(SeqGreaterEqual(0x12345678U, 0x12345678U));
}

TEST_CASE("ParseMssOption - MSS option present", "[unit][tcp]") {
  // MSS option: kind=2, len=4, value=1400
  uint8_t options[] = {0x02, 0x04, 0x05, 0x78};  // 0x0578 = 1400
  uint16_t mss = ParseMssOption(options, sizeof(options));
  REQUIRE(mss == 1400);
}

TEST_CASE("ParseMssOption - no MSS option returns default 536", "[unit][tcp]") {
  // End-of-options immediately
  uint8_t options[] = {0x00};
  uint16_t mss = ParseMssOption(options, sizeof(options));
  REQUIRE(mss == 536);
}

TEST_CASE("ParseMssOption - empty options returns default 536", "[unit][tcp]") {
  uint8_t options[] = {};
  uint16_t mss = ParseMssOption(options, 0);
  REQUIRE(mss == 536);
}

TEST_CASE("ParseMssOption - NOP followed by MSS", "[unit][tcp]") {
  // NOP(1), MSS(2,len=4,val=800)
  uint8_t options[] = {0x01, 0x02, 0x04, 0x03, 0x20};  // 0x0320 = 800
  uint16_t mss = ParseMssOption(options, sizeof(options));
  REQUIRE(mss == 800);
}

TEST_CASE("ParseMssOption - multiple options, MSS extracted", "[unit][tcp]") {
  // NOP(1), MSS(2,len=4,val=1200), NOP(1), unknown(8,len=10,data)
  uint8_t options[] = {
    0x01,
    0x02, 0x04, 0x04, 0xB0,  // 0x04B0 = 1200
    0x01,
    0x08, 0x0A, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00  // timestamps dummy
  };
  uint16_t mss = ParseMssOption(options, sizeof(options));
  REQUIRE(mss == 1200);
}

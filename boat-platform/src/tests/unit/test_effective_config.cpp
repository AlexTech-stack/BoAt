// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include <catch2/catch_test_macros.hpp>

#include <string>

#include "effective_config.h"

using boat::gateway::EffectiveConfig;
using boat::gateway::JsonEscape;

TEST_CASE("JsonEscape produces valid JSON string content", "[unit][effective_config]") {
  REQUIRE(JsonEscape("plain") == "plain");
  REQUIRE(JsonEscape("say \"hi\"") == "say \\\"hi\\\"");
  REQUIRE(JsonEscape("back\\slash") == "back\\\\slash");
  REQUIRE(JsonEscape("line\nbreak") == "line\\nbreak");
  REQUIRE(JsonEscape("tab\there") == "tab\\there");

  // Control characters without a short escape need the \u00XX form, or the
  // document silently becomes invalid JSON.
  REQUIRE(JsonEscape(std::string("bell\x07")) == "bell\\u0007");

  // Bytes >= 0x20 pass through untouched so UTF-8 survives intact.
  REQUIRE(JsonEscape("iface-ü") == "iface-ü");
}

TEST_CASE("EffectiveConfig renders a plugin config verbatim", "[unit][effective_config]") {
  // A plugin config is arbitrary operator-supplied text that the gateway never
  // parses. Embedding it must not be able to break the surrounding document --
  // this is the case that would corrupt a committed artifact.
  EffectiveConfig cfg;
  EffectiveConfig::PluginEntry entry;
  entry.so_path = "/plugins/can_tp.so";
  entry.config_json = R"({"iface":"vcan0","note":"has \"quotes\""})";
  entry.loaded = true;
  cfg.node_plugins.push_back(entry);

  const std::string json = cfg.ToJson();
  // The raw config must appear escaped, never raw, inside the document.
  REQUIRE(json.find(R"("config": "{\"iface\":\"vcan0\")") != std::string::npos);
  REQUIRE(json.find("\"loaded\": true") != std::string::npos);
}

TEST_CASE("EffectiveConfig output is stable across identical configs",
          "[unit][effective_config]") {
  // The whole point of the artifact: two identically configured gateways must
  // emit byte-identical documents, or it cannot be diffed to prove that two
  // runs were configured the same way. Nothing time- or host-dependent may
  // creep into ToJson().
  auto build = [] {
    EffectiveConfig cfg;
    cfg.version = "0.1.0";
    cfg.grpc_port = 50051;
    cfg.can_interfaces = {"vcan0", "vcan1"};
    cfg.can_interfaces_opened = {true, false};
    cfg.tick_ns = 500000;
    cfg.tick_resolved_from = "BOAT_NODE_TICK_US";
    cfg.tick_phases = {"node_plugins", "sim_plugins", "replay"};
    cfg.warnings = {"CAN interface 'vcan1' failed to open"};
    return cfg;
  };
  REQUIRE(build().ToJson() == build().ToJson());
}

TEST_CASE("EffectiveConfig records which knob set the tick", "[unit][effective_config]") {
  // A run that silently used the default instead of the requested tick is the
  // failure this field exists to make visible.
  EffectiveConfig cfg;
  REQUIRE(cfg.tick_resolved_from == "default");
  REQUIRE(cfg.ToJson().find("\"resolved_from\": \"default\"") != std::string::npos);

  cfg.tick_resolved_from = "BOAT_NODE_TICK_MS";
  cfg.tick_ns = 2000000;
  const std::string json = cfg.ToJson();
  REQUIRE(json.find("\"interval_ns\": 2000000") != std::string::npos);
  REQUIRE(json.find("\"resolved_from\": \"BOAT_NODE_TICK_MS\"") != std::string::npos);
}

TEST_CASE("EffectiveConfig with no plugins still emits a valid array",
          "[unit][effective_config]") {
  // The empty-list branch is written separately from the populated one, so it
  // gets its own check that it does not emit a trailing comma.
  const std::string json = EffectiveConfig{}.ToJson();
  REQUIRE(json.find("\"node_plugins\": [],") != std::string::npos);
  REQUIRE(json.find(",\n}") == std::string::npos);
  REQUIRE(json.find(",]") == std::string::npos);
}

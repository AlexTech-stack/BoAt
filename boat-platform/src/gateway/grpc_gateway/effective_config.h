// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace boat::gateway {

/* Everything the gateway resolved out of the environment at startup, in one
 * place.
 *
 * The gateway's configuration is a scatter of getenv() calls, each with its
 * own fallback, and several of them fall back *silently* -- an unparsable
 * BOAT_NODE_TICK_US used to leave you running at the 1ms default with nothing
 * on stderr to say so. For a tool whose product claim is reproducible runs,
 * that is the wrong failure mode twice over: the run is not what was asked
 * for, and nothing records what it actually was.
 *
 * So resolution now happens once, into this struct, which records both the
 * value used and (where it is not obvious) which variable decided it. It is
 * written to disk as JSON via BOAT_CONFIG_DUMP and served over
 * DebugService.GetEffectiveConfig, so a CI run can commit its own
 * configuration next to the trace it produced.
 *
 * Deliberately NO timestamp or hostname: two runs configured identically must
 * produce byte-identical documents, or the artifact cannot be diffed to prove
 * "these two runs were configured the same way", which is most of the point.
 */
struct EffectiveConfig {
  struct PluginEntry {
    std::string so_path;
    /* Verbatim, exactly as handed to the plugin -- the gateway does not parse
       plugin config, and re-emitting a normalized form would misrepresent
       what the plugin received. */
    std::string config_json;
    bool loaded = false;
    std::string error;  // load failure detail; empty when loaded
  };

  std::string version;

  int  grpc_port = 50051;
  bool tls_enabled = false;
  bool mtls_required = false;
  std::string tls_cert_path;
  std::string tls_key_path;
  std::string tls_client_ca_path;

  /* Interfaces as *requested*. Whether each opened is reported per-entry in
     can_interfaces_opened / eth_interfaces_opened, since a bench that lost an
     interface is exactly the sort of thing you want visible in the artifact
     rather than only in scrollback. */
  std::vector<std::string> can_interfaces;
  std::vector<bool>        can_interfaces_opened;
  std::vector<std::string> eth_interfaces;
  std::vector<bool>        eth_interfaces_opened;

  std::vector<PluginEntry> node_plugins;

  std::uint64_t tick_ns = 1000000;
  /* Which knob actually decided the tick: "BOAT_NODE_TICK_US",
     "BOAT_NODE_TICK_MS", or "default". */
  std::string tick_resolved_from = "default";
  std::string time_source = "realtime";
  std::vector<std::string> tick_phases;

  bool hil_enabled = false;

  /* Anything that was ignored, malformed, or fell back to a default. Empty is
     the good case; a non-empty list belongs in the CI log. */
  std::vector<std::string> warnings;

  /* Stable, pretty-printed JSON. Key order is fixed by this function, not by
     a hash map, so the output diffs cleanly between runs. */
  [[nodiscard]] std::string ToJson() const;
};

/* Escapes a string for embedding in a JSON document (quotes, backslashes,
   control characters). Exposed for tests. */
[[nodiscard]] std::string JsonEscape(const std::string& in);

}  // namespace boat::gateway

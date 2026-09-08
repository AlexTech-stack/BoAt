// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "effective_config.h"

#include <cstdio>
#include <sstream>

namespace boat::gateway {
namespace {

/* Emits a JSON array of strings on one line: ["a", "b"]. */
void AppendStringArray(std::ostringstream& os, const std::vector<std::string>& items) {
  os << '[';
  for (std::size_t i = 0; i < items.size(); ++i) {
    if (i != 0) os << ", ";
    os << '"' << JsonEscape(items[i]) << '"';
  }
  os << ']';
}

const char* BoolStr(bool b) { return b ? "true" : "false"; }

}  // namespace

std::string JsonEscape(const std::string& in) {
  std::string out;
  out.reserve(in.size() + 8);
  for (const char c : in) {
    switch (c) {
      case '"':  out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\b': out += "\\b";  break;
      case '\f': out += "\\f";  break;
      case '\n': out += "\\n";  break;
      case '\r': out += "\\r";  break;
      case '\t': out += "\\t";  break;
      default:
        // Everything below 0x20 must be escaped; \u00XX is the only form that
        // works for the ones without a short escape. Bytes >= 0x20 pass
        // through untouched, which keeps valid UTF-8 payloads intact.
        if (static_cast<unsigned char>(c) < 0x20) {
          char buf[7];
          std::snprintf(buf, sizeof(buf), "\\u%04x", static_cast<unsigned char>(c));
          out += buf;
        } else {
          out += c;
        }
    }
  }
  return out;
}

std::string EffectiveConfig::ToJson() const {
  std::ostringstream os;
  os << "{\n";
  os << "  \"schema\": \"boat.gateway.effective_config/v1\",\n";
  os << "  \"version\": \"" << JsonEscape(version) << "\",\n";

  os << "  \"grpc\": {\n";
  os << "    \"port\": " << grpc_port << ",\n";
  os << "    \"tls_enabled\": " << BoolStr(tls_enabled) << ",\n";
  os << "    \"mtls_required\": " << BoolStr(mtls_required) << ",\n";
  os << "    \"tls_cert_path\": \"" << JsonEscape(tls_cert_path) << "\",\n";
  os << "    \"tls_key_path\": \"" << JsonEscape(tls_key_path) << "\",\n";
  os << "    \"tls_client_ca_path\": \"" << JsonEscape(tls_client_ca_path) << "\"\n";
  os << "  },\n";

  os << "  \"buses\": {\n";
  os << "    \"can\": ";
  AppendStringArray(os, can_interfaces);
  os << ",\n";
  os << "    \"can_opened\": [";
  for (std::size_t i = 0; i < can_interfaces_opened.size(); ++i) {
    if (i != 0) os << ", ";
    os << BoolStr(can_interfaces_opened[i]);
  }
  os << "],\n";
  os << "    \"ethernet\": ";
  AppendStringArray(os, eth_interfaces);
  os << ",\n";
  os << "    \"ethernet_opened\": [";
  for (std::size_t i = 0; i < eth_interfaces_opened.size(); ++i) {
    if (i != 0) os << ", ";
    os << BoolStr(eth_interfaces_opened[i]);
  }
  os << "]\n";
  os << "  },\n";

  os << "  \"node_plugins\": [";
  if (node_plugins.empty()) {
    os << "],\n";
  } else {
    os << "\n";
    for (std::size_t i = 0; i < node_plugins.size(); ++i) {
      const auto& p = node_plugins[i];
      os << "    {\n";
      os << "      \"so_path\": \"" << JsonEscape(p.so_path) << "\",\n";
      // The plugin's config stays a JSON *string*, not an inlined object: the
      // gateway never parses it, and quoting it is the only way to guarantee
      // this document stays well-formed no matter what the operator passed.
      os << "      \"config\": \"" << JsonEscape(p.config_json) << "\",\n";
      os << "      \"loaded\": " << BoolStr(p.loaded) << ",\n";
      os << "      \"error\": \"" << JsonEscape(p.error) << "\"\n";
      os << "    }" << (i + 1 < node_plugins.size() ? ",\n" : "\n");
    }
    os << "  ],\n";
  }

  os << "  \"tick\": {\n";
  os << "    \"interval_ns\": " << tick_ns << ",\n";
  os << "    \"resolved_from\": \"" << JsonEscape(tick_resolved_from) << "\",\n";
  os << "    \"time_source\": \"" << JsonEscape(time_source) << "\",\n";
  os << "    \"phases\": ";
  AppendStringArray(os, tick_phases);
  os << "\n  },\n";

  os << "  \"hil_enabled\": " << BoolStr(hil_enabled) << ",\n";
  os << "  \"warnings\": ";
  AppendStringArray(os, warnings);
  os << "\n}\n";
  return os.str();
}

}  // namespace boat::gateway

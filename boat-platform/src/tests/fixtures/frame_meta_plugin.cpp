// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* Test-only plugin: records the metadata of every CAN/Ethernet frame it sees
   to a file. Used to prove that simulation-scoped plugins receive iface and
   SELF_SENT through the unified frame subscription (issue #70), rather than
   the stripped-down frames the old SimulationService rebuilt by hand.

   Config: {"out": "/tmp/frames.log", "buses": "can"}.
   ``buses`` selects what ``declared_buses()`` returns -- either a
   comma-separated string ("can", "eth", "can,eth") or a JSON array
   (["can"]). Default is both, which keeps the issue #70 integration test
   working unchanged. A CAN-only instance is what TC_Plugins_003 needs to
   prove pre-filtered dispatch (the default declares both). */

#include <boat/plugin.h>

#include <cstdio>
#include <fstream>
#include <string>
#include <vector>

namespace {

struct MetaContext {
  std::string out;
  std::string declared = "[\"can\",\"eth\"]";
};

MetaContext* Context(void* ctx) { return static_cast<MetaContext*>(ctx); }

std::string ConfigValue(const char* json, const char* key) {
  if (json == nullptr) return {};
  const std::string text(json);
  const std::string needle = std::string("\"") + key + "\"";
  const auto pos = text.find(needle);
  if (pos == std::string::npos) return {};
  auto start = text.find('"', pos + needle.size());
  if (start == std::string::npos) return {};
  ++start;
  const auto end = text.find('"', start);
  return end == std::string::npos ? std::string{} : text.substr(start, end - start);
}

/* Accept either "buses": "can,eth" or "buses": ["can","eth"] and turn it into
   the JSON array string declared_buses() expects. */
std::string ConfigBuses(const char* json) {
  if (json == nullptr) return {};
  const std::string text(json);
  const std::string key = "\"buses\"";
  const auto pos = text.find(key);
  if (pos == std::string::npos) return {};
  const auto colon = text.find(':', pos + key.size());
  if (colon == std::string::npos) return {};
  const auto value_start = text.find_first_not_of(" \t\r\n", colon + 1);
  if (value_start == std::string::npos) return {};
  std::string region;
  if (text[value_start] == '[') {
    const auto close = text.find(']', value_start);
    if (close == std::string::npos) return {};
    region = text.substr(value_start, close - value_start + 1);
  } else if (text[value_start] == '"') {
    const auto close = text.find('"', value_start + 1);
    if (close == std::string::npos) return {};
    region = text.substr(value_start, close - value_start + 1);
  } else {
    const auto end = text.find_first_of(",}", value_start);
    region = text.substr(value_start, end == std::string::npos ? std::string::npos : end - value_start);
  }

  const auto trim = [](std::string s) {
    const auto first = s.find_first_not_of(" \t\r\n");
    const auto last = s.find_last_not_of(" \t\r\n");
    return first == std::string::npos ? std::string{} : s.substr(first, last - first + 1);
  };

  std::vector<std::string> raw_tokens;
  std::size_t cursor = 0;
  while ((cursor = region.find('"', cursor)) != std::string::npos) {
    const auto close = region.find('"', cursor + 1);
    if (close == std::string::npos) break;
    raw_tokens.push_back(region.substr(cursor + 1, close - cursor - 1));
    cursor = close + 1;
  }

  std::vector<std::string> buses;
  for (const auto& token : raw_tokens) {
    std::size_t start = 0;
    while (start <= token.size()) {
      const auto comma = token.find(',', start);
      const std::string part = trim(token.substr(start, comma == std::string::npos ? std::string::npos : comma - start));
      if (!part.empty()) buses.push_back(part);
      if (comma == std::string::npos) break;
      start = comma + 1;
    }
  }
  if (buses.empty()) return {};

  std::string out = "[";
  for (std::size_t i = 0; i < buses.size(); ++i) {
    if (i != 0) out += ",";
    out += "\"" + buses[i] + "\"";
  }
  out += "]";
  return out;
}

int MetaInitialize(void* ctx, const char* config_json) {
  auto* c = Context(ctx);
  c->out = ConfigValue(config_json, "out");
  if (c->out.empty()) return 1;
  const std::string buses = ConfigBuses(config_json);
  if (!buses.empty()) c->declared = buses;
  std::ofstream(c->out, std::ios::trunc).close();
  return 0;
}

void MetaTick(void*, uint64_t) {}

void MetaShutdown(void*) {}

void MetaOnFrame(void* ctx, const BoatFrame* f) {
  if (f == nullptr) return;
  auto* c = Context(ctx);
  std::ofstream out(c->out, std::ios::app);
  if (!out) return;
  const char* iface = f->iface == nullptr ? "" : f->iface;
  if (f->bus_type == BOAT_BUS_CAN || f->bus_type == BOAT_BUS_CANFD) {
    out << "can|" << iface << "|" << static_cast<unsigned>(f->meta.can.flags)
        << "|" << f->meta.can.can_id << "\n";
  } else if (f->bus_type == BOAT_BUS_ETHERNET) {
    out << "eth|" << iface << "|" << static_cast<unsigned>(f->meta.eth.flags)
        << "\n";
  }
}

const char* MetaDeclaredBuses(void* ctx) { return Context(ctx)->declared.c_str(); }

BoatPluginVTable gVTable = [] {
  BoatPluginVTable vt{};
  vt.initialize = MetaInitialize;
  vt.on_tick = MetaTick;
  vt.shutdown = MetaShutdown;
  vt.on_frame = MetaOnFrame;
  vt.declared_buses = MetaDeclaredBuses;
  return vt;
}();

}  // namespace

extern "C" BoatPlugin* boat_plugin_create() {
  auto* plugin = new BoatPlugin{};
  plugin->vtable = &gVTable;
  plugin->ctx = new MetaContext{};
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (plugin == nullptr) return;
  delete static_cast<MetaContext*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }

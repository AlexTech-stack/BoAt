// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* Test-only plugin: records the metadata of every CAN/Ethernet frame it sees
   to a file. Used to prove that simulation-scoped plugins receive iface and
   SELF_SENT through the unified frame subscription (issue #70), rather than
   the stripped-down frames the old SimulationService rebuilt by hand. */

#include <boat/plugin.h>

#include <cstdio>
#include <fstream>
#include <string>

namespace {

struct MetaContext {
  std::string out;
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

int MetaInitialize(void* ctx, const char* config_json) {
  auto* c = Context(ctx);
  c->out = ConfigValue(config_json, "out");
  if (c->out.empty()) return 1;
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

const char* MetaDeclaredBuses(void*) { return "[\"can\",\"eth\"]"; }

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

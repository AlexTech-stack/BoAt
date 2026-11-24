// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* Test-only plugin: publishes one CAN frame every N ticks.

   Purpose: give the manual system tests a deterministic traffic source that
   works as a scenario-scoped plugin (TC_Simulation_001/002/003,
   TC_Plugins_005) and as an always-on node plugin (TC_Plugins_004) without
   requiring a Python node process. Payload and cadence are fixed, so the
   frame count over a stepped tick range is exactly computable:
   ``floor(end_tick / period_ticks) - floor(start_tick / period_ticks)``.

   Config:
     {"iface":"vcan0","can_id":"0x321","period_ticks":100,
      "payload":"0102030405060708","bus_type":"can"}

   ``can_id`` accepts hex (0x..) or decimal; ``bus_type`` is ``can`` (default)
   or ``canfd``. A CAN-FD frame may carry up to 64 payload bytes. */

#include <boat/plugin.h>

#include <cstdint>
#include <string>
#include <vector>

namespace {

struct TrafficContext {
  std::string iface = "vcan0";
  std::vector<std::uint8_t> payload = {1, 2, 3, 4, 5, 6, 7, 8};
  std::uint32_t can_id = 0x321;
  std::uint64_t period_ticks = 100;
  bool is_fd = false;

  BoatFramePublishFn publish = nullptr;
  void* publish_ctx = nullptr;
};

TrafficContext* Context(void* ctx) { return static_cast<TrafficContext*>(ctx); }

std::string ConfigValue(const char* json, const char* key) {
  if (json == nullptr) return {};
  const std::string text(json);
  const std::string needle = std::string("\"") + key + "\"";
  const auto pos = text.find(needle);
  if (pos == std::string::npos) return {};
  auto start = text.find_first_not_of(" \t\r\n:", pos + needle.size());
  if (start == std::string::npos) return {};
  if (text[start] == '"') {
    ++start;
    const auto end = text.find('"', start);
    return end == std::string::npos ? std::string{} : text.substr(start, end - start);
  }
  const auto end = text.find_first_of(",}", start);
  std::string value = text.substr(start, end == std::string::npos ? std::string::npos : end - start);
  while (!value.empty() && (value.back() == ' ' || value.back() == '\t')) value.pop_back();
  return value;
}

std::uint64_t ParseU64(const std::string& text, std::uint64_t fallback) {
  if (text.empty()) return fallback;
  try {
    return static_cast<std::uint64_t>(std::stoull(text, nullptr, 0));
  } catch (...) {
    return fallback;
  }
}

std::vector<std::uint8_t> ParseHex(const std::string& text) {
  std::vector<std::uint8_t> out;
  std::uint8_t hi = 0;
  bool have_hi = false;
  for (char c : text) {
    int value;
    if (c >= '0' && c <= '9') value = c - '0';
    else if (c >= 'a' && c <= 'f') value = c - 'a' + 10;
    else if (c >= 'A' && c <= 'F') value = c - 'A' + 10;
    else continue;
    if (!have_hi) {
      hi = static_cast<std::uint8_t>(value);
      have_hi = true;
    } else {
      out.push_back(static_cast<std::uint8_t>((hi << 4) | value));
      have_hi = false;
    }
  }
  return out;
}

int TrafficInitialize(void* ctx, const char* config_json) {
  auto* c = Context(ctx);
  const std::string iface = ConfigValue(config_json, "iface");
  if (!iface.empty()) c->iface = iface;
  c->can_id = static_cast<std::uint32_t>(ParseU64(ConfigValue(config_json, "can_id"), 0x321));
  const std::uint64_t period = ParseU64(ConfigValue(config_json, "period_ticks"), 100);
  c->period_ticks = period == 0 ? 1 : period;
  const std::string payload = ConfigValue(config_json, "payload");
  if (!payload.empty()) c->payload = ParseHex(payload);
  c->is_fd = ConfigValue(config_json, "bus_type") == "canfd";
  if (c->payload.size() > (c->is_fd ? 64u : 8u)) return 1;
  return 0;
}

void TrafficTick(void* ctx, std::uint64_t tick) {
  auto* c = Context(ctx);
  if (c->publish == nullptr || c->period_ticks == 0) return;
  if (tick == 0 || (tick % c->period_ticks) != 0) return;

  const auto dlc = static_cast<std::uint8_t>(c->payload.size());
  auto frame = c->is_fd
                   ? BoatFrameOwner::CanFd(c->iface, c->can_id, dlc, 0, c->payload)
                   : BoatFrameOwner::Can(c->iface, c->can_id, dlc, 0, c->payload);
  c->publish(c->publish_ctx, frame.get());
}

void TrafficShutdown(void*) {}

void TrafficSetFramePublisher(void* ctx, BoatFramePublishFn fn, void* publisher_ctx) {
  auto* c = Context(ctx);
  c->publish = fn;
  c->publish_ctx = publisher_ctx;
}

const char* TrafficDeclaredBuses(void*) { return "[\"can\"]"; }

BoatPluginVTable gVTable = [] {
  BoatPluginVTable vt{};
  vt.initialize = TrafficInitialize;
  vt.on_tick = TrafficTick;
  vt.shutdown = TrafficShutdown;
  vt.set_frame_publisher = TrafficSetFramePublisher;
  vt.declared_buses = TrafficDeclaredBuses;
  return vt;
}();

}  // namespace

extern "C" BoatPlugin* boat_plugin_create() {
  auto* plugin = new BoatPlugin{};
  plugin->vtable = &gVTable;
  plugin->ctx = new TrafficContext{};
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (plugin == nullptr) return;
  delete static_cast<TrafficContext*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }

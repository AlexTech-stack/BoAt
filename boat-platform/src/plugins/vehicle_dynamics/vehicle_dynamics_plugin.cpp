#include "vehicle_dynamics_plugin.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace {

double parse_double_value(const std::string& json, const char* key, double fallback) {
  const std::string needle = std::string("\"") + key + "\"";
  const std::size_t key_pos = json.find(needle);
  if (key_pos == std::string::npos) return fallback;
  const std::size_t colon_pos = json.find(':', key_pos + needle.size());
  if (colon_pos == std::string::npos) return fallback;
  double parsed = fallback;
  if (std::sscanf(json.c_str() + static_cast<long long>(colon_pos + 1), " %lf", &parsed) == 1)
    return parsed;
  return fallback;
}

int vehicle_initialize(void* ctx, const char* config_json) {
  auto* plugin = static_cast<VehicleDynamicsPlugin*>(ctx);
  if (plugin == nullptr) return -1;
  const std::string config = config_json == nullptr ? std::string{} : std::string(config_json);
  plugin->speed_kmh = parse_double_value(config, "initial_speed_kmh", 0.0);
  plugin->rpm = parse_double_value(config, "initial_rpm", 800.0);
  return 0;
}

void vehicle_on_tick(void* ctx, uint64_t tick) {
  auto* plugin = static_cast<VehicleDynamicsPlugin*>(ctx);
  if (plugin == nullptr) return;

  plugin->speed_kmh = std::clamp(plugin->speed_kmh + plugin->speed_delta_dist(plugin->rng),
                                 0.0, 300.0);
  plugin->rpm = std::clamp(plugin->rpm + plugin->rpm_delta_dist(plugin->rng),
                           0.0, 8000.0);

  // Signal publishing
  if (plugin->publish_fn != nullptr) {
    plugin->publish_fn(plugin->publisher_ctx, "speed", tick, plugin->speed_kmh);
    plugin->publish_fn(plugin->publisher_ctx, "rpm",   tick, plugin->rpm);
  }

  // v8: Unified frame publishing (CAN + Ethernet)
  if (plugin->frame_publish_fn != nullptr) {
    const auto speed_raw = static_cast<std::uint32_t>(plugin->speed_kmh * 100.0);
    const auto rpm_raw   = static_cast<std::uint32_t>(plugin->rpm);

    uint8_t speed_buf[4] = {
      static_cast<uint8_t>((speed_raw >>  0) & 0xFF),
      static_cast<uint8_t>((speed_raw >>  8) & 0xFF),
      static_cast<uint8_t>((speed_raw >> 16) & 0xFF),
      static_cast<uint8_t>((speed_raw >> 24) & 0xFF),
    };
    uint8_t rpm_buf[4] = {
      static_cast<uint8_t>((rpm_raw >>  0) & 0xFF),
      static_cast<uint8_t>((rpm_raw >>  8) & 0xFF),
      static_cast<uint8_t>((rpm_raw >> 16) & 0xFF),
      static_cast<uint8_t>((rpm_raw >> 24) & 0xFF),
    };

    // CAN 0x100 — speed
    BoatFrame sf{};
    sf.bus_type    = BOAT_BUS_CAN;
    sf.payload     = speed_buf;
    sf.payload_len = 4;
    sf.meta.can.can_id = 0x100;
    sf.meta.can.dlc    = 4;
    plugin->frame_publish_fn(plugin->frame_publisher_ctx, &sf);

    // CAN 0x101 — rpm
    BoatFrame rf{};
    rf.bus_type    = BOAT_BUS_CAN;
    rf.payload     = rpm_buf;
    rf.payload_len = 4;
    rf.meta.can.can_id = 0x101;
    rf.meta.can.dlc    = 4;
    plugin->frame_publish_fn(plugin->frame_publisher_ctx, &rf);

    // Ethernet — 8-byte payload (speed + rpm)
    std::vector<uint8_t> payload(8);
    payload[0] = speed_buf[0]; payload[1] = speed_buf[1];
    payload[2] = speed_buf[2]; payload[3] = speed_buf[3];
    payload[4] = rpm_buf[0];   payload[5] = rpm_buf[1];
    payload[6] = rpm_buf[2];   payload[7] = rpm_buf[3];

    BoatFrame ef{};
    ef.bus_type    = BOAT_BUS_ETHERNET;
    ef.payload     = payload.data();
    ef.payload_len = payload.size();
    std::memset(ef.meta.eth.dst_mac, 0xFF, 6);
    std::memset(ef.meta.eth.src_mac, 0x02, 6);
    ef.meta.eth.ethertype = 0x0800;
    plugin->frame_publish_fn(plugin->frame_publisher_ctx, &ef);
  }

  // Bus-signal publishing
  if (plugin->bus_publish_fn != nullptr) {
    plugin->bus_publish_fn(plugin->bus_publisher_ctx, "vehicle.speed", plugin->speed_kmh);
    plugin->bus_publish_fn(plugin->bus_publisher_ctx, "vehicle.rpm",   plugin->rpm);
  }
}

/* ── Publisher setters ──────────────────────────────────────────────── */

void vehicle_set_publisher(void* ctx, BoatPublishFn fn, void* pub_ctx) {
  auto* plugin = static_cast<VehicleDynamicsPlugin*>(ctx);
  if (plugin == nullptr) return;
  plugin->publish_fn    = fn;
  plugin->publisher_ctx = pub_ctx;
}

void vehicle_set_frame_publisher(void* ctx, BoatFramePublishFn fn, void* pub_ctx) {
  auto* plugin = static_cast<VehicleDynamicsPlugin*>(ctx);
  if (plugin == nullptr) return;
  plugin->frame_publish_fn    = fn;
  plugin->frame_publisher_ctx = pub_ctx;
}

void vehicle_set_bus_publisher(void* ctx, BoatBusPublishFn fn, void* pub_ctx) {
  auto* plugin = static_cast<VehicleDynamicsPlugin*>(ctx);
  if (plugin == nullptr) return;
  plugin->bus_publish_fn    = fn;
  plugin->bus_publisher_ctx = pub_ctx;
}

void vehicle_shutdown(void* ctx) {
  auto* plugin = static_cast<VehicleDynamicsPlugin*>(ctx);
  if (plugin == nullptr) return;
  plugin->speed_kmh = 0.0;
  plugin->rpm = 0.0;
}

BoatPluginVTable kVehicleDynamicsVTable = {
    &vehicle_initialize,
    &vehicle_on_tick,
    &vehicle_shutdown,
    &vehicle_set_publisher,
    /* set_can_publisher */ nullptr,      // v7 → replaced
    /* on_can_frame      */ nullptr,
    /* set_eth_publisher */ nullptr,      // v7 → replaced
    /* on_eth_frame      */ nullptr,
    &vehicle_set_bus_publisher,
    /* set_pdu_publisher */ nullptr,
    /* on_frame           */ nullptr,     // v8 — no receive
    &vehicle_set_frame_publisher,         // v8
    /* declared_buses     */ nullptr,     // no receive, no filter needed
};

}  // namespace

extern "C" BoatPlugin* boat_plugin_create() {
  auto* plugin_state = new VehicleDynamicsPlugin{};
  auto* plugin = new BoatPlugin{};
  plugin->vtable = &kVehicleDynamicsVTable;
  plugin->ctx = plugin_state;
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (plugin == nullptr) return;
  if (plugin->vtable != nullptr && plugin->vtable->shutdown != nullptr) {
    plugin->vtable->shutdown(plugin->ctx);
  }
  delete static_cast<VehicleDynamicsPlugin*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }

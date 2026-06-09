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
  if (key_pos == std::string::npos) {
    return fallback;
  }
  const std::size_t colon_pos = json.find(':', key_pos + needle.size());
  if (colon_pos == std::string::npos) {
    return fallback;
  }

  double parsed = fallback;
  if (std::sscanf(json.c_str() + static_cast<long long>(colon_pos + 1), " %lf", &parsed) == 1) {
    return parsed;
  }
  return fallback;
}

int vehicle_initialize(void* ctx, const char* config_json) {
  auto* plugin = static_cast<VehicleDynamicsPlugin*>(ctx);
  if (plugin == nullptr) {
    return -1;
  }

  const std::string config = config_json == nullptr ? std::string{} : std::string(config_json);
  plugin->speed_kmh = parse_double_value(config, "initial_speed_kmh", 0.0);
  plugin->rpm = parse_double_value(config, "initial_rpm", 800.0);
  return 0;
}

void vehicle_on_tick(void* ctx, uint64_t tick) {
  auto* plugin = static_cast<VehicleDynamicsPlugin*>(ctx);
  if (plugin == nullptr) {
    return;
  }

  plugin->speed_kmh = std::clamp(plugin->speed_kmh + plugin->speed_delta_dist(plugin->rng),
                                 0.0, 300.0);
  plugin->rpm = std::clamp(plugin->rpm + plugin->rpm_delta_dist(plugin->rng),
                           0.0, 8000.0);

  if (plugin->publish_fn != nullptr) {
    plugin->publish_fn(plugin->publisher_ctx, "speed", tick, plugin->speed_kmh);
    plugin->publish_fn(plugin->publisher_ctx, "rpm",   tick, plugin->rpm);
  }

  // Publish CAN frames:
  //   0x100 — speed in km/h * 100, 4 bytes little-endian uint32
  //   0x101 — rpm as integer, 4 bytes little-endian uint32
  if (plugin->can_publish_fn != nullptr) {
    const auto speed_raw = static_cast<std::uint32_t>(plugin->speed_kmh * 100.0);
    const auto rpm_raw   = static_cast<std::uint32_t>(plugin->rpm);

    BoatCanFrame speed_frame{};
    speed_frame.can_id = 0x100;
    speed_frame.dlc    = 4;
    speed_frame.data[0] = static_cast<std::uint8_t>((speed_raw >>  0) & 0xFF);
    speed_frame.data[1] = static_cast<std::uint8_t>((speed_raw >>  8) & 0xFF);
    speed_frame.data[2] = static_cast<std::uint8_t>((speed_raw >> 16) & 0xFF);
    speed_frame.data[3] = static_cast<std::uint8_t>((speed_raw >> 24) & 0xFF);
    plugin->can_publish_fn(plugin->can_publisher_ctx, &speed_frame);

    BoatCanFrame rpm_frame{};
    rpm_frame.can_id = 0x101;
    rpm_frame.dlc    = 4;
    rpm_frame.data[0] = static_cast<std::uint8_t>((rpm_raw >>  0) & 0xFF);
    rpm_frame.data[1] = static_cast<std::uint8_t>((rpm_raw >>  8) & 0xFF);
    rpm_frame.data[2] = static_cast<std::uint8_t>((rpm_raw >> 16) & 0xFF);
    rpm_frame.data[3] = static_cast<std::uint8_t>((rpm_raw >> 24) & 0xFF);
    plugin->can_publish_fn(plugin->can_publisher_ctx, &rpm_frame);
  }

  // Publish Ethernet frames:
  //   ethertype 0x0800 (IPv4), payload = 8 bytes: speed_kmh*100 (4 LE) + rpm (4 LE)
  if (plugin->eth_publish_fn != nullptr) {
    const auto speed_raw = static_cast<std::uint32_t>(plugin->speed_kmh * 100.0);
    const auto rpm_raw   = static_cast<std::uint32_t>(plugin->rpm);
    std::vector<std::uint8_t> payload(8);
    payload[0] = static_cast<std::uint8_t>((speed_raw >>  0) & 0xFF);
    payload[1] = static_cast<std::uint8_t>((speed_raw >>  8) & 0xFF);
    payload[2] = static_cast<std::uint8_t>((speed_raw >> 16) & 0xFF);
    payload[3] = static_cast<std::uint8_t>((speed_raw >> 24) & 0xFF);
    payload[4] = static_cast<std::uint8_t>((rpm_raw   >>  0) & 0xFF);
    payload[5] = static_cast<std::uint8_t>((rpm_raw   >>  8) & 0xFF);
    payload[6] = static_cast<std::uint8_t>((rpm_raw   >> 16) & 0xFF);
    payload[7] = static_cast<std::uint8_t>((rpm_raw   >> 24) & 0xFF);

    // Broadcast MAC
    BoatEthFrame eth_frame{};
    std::memset(eth_frame.dst_mac, 0xFF, 6);
    std::memset(eth_frame.src_mac, 0x02, 6);
    eth_frame.ethertype   = 0x0800;
    eth_frame.payload     = payload.data();
    eth_frame.payload_len = payload.size();
    plugin->eth_publish_fn(plugin->eth_publisher_ctx, &eth_frame);
  }

  // Publish bus signals (always-on, simulation-independent).
  if (plugin->bus_publish_fn != nullptr) {
    plugin->bus_publish_fn(plugin->bus_publisher_ctx, "vehicle.speed", plugin->speed_kmh);
    plugin->bus_publish_fn(plugin->bus_publisher_ctx, "vehicle.rpm",   plugin->rpm);
  }
}

void vehicle_set_publisher(void* ctx, BoatPublishFn fn, void* publisher_ctx) {
  auto* plugin = static_cast<VehicleDynamicsPlugin*>(ctx);
  if (plugin == nullptr) return;
  plugin->publish_fn    = fn;
  plugin->publisher_ctx = publisher_ctx;
}

void vehicle_set_can_publisher(void* ctx, BoatCanPublishFn fn, void* publisher_ctx) {
  auto* plugin = static_cast<VehicleDynamicsPlugin*>(ctx);
  if (plugin == nullptr) return;
  plugin->can_publish_fn    = fn;
  plugin->can_publisher_ctx = publisher_ctx;
}

void vehicle_set_eth_publisher(void* ctx, BoatEthPublishFn fn, void* publisher_ctx) {
  auto* plugin = static_cast<VehicleDynamicsPlugin*>(ctx);
  if (plugin == nullptr) return;
  plugin->eth_publish_fn    = fn;
  plugin->eth_publisher_ctx = publisher_ctx;
}

void vehicle_set_bus_publisher(void* ctx, BoatBusPublishFn fn, void* publisher_ctx) {
  auto* plugin = static_cast<VehicleDynamicsPlugin*>(ctx);
  if (plugin == nullptr) return;
  plugin->bus_publish_fn    = fn;
  plugin->bus_publisher_ctx = publisher_ctx;
}

void vehicle_shutdown(void* ctx) {
  auto* plugin = static_cast<VehicleDynamicsPlugin*>(ctx);
  if (plugin == nullptr) {
    return;
  }
  plugin->speed_kmh = 0.0;
  plugin->rpm = 0.0;
}

BoatPluginVTable kVehicleDynamicsVTable = {
    &vehicle_initialize,
    &vehicle_on_tick,
    &vehicle_shutdown,
    &vehicle_set_publisher,
    &vehicle_set_can_publisher,
    /* on_can_frame */ nullptr,
    &vehicle_set_eth_publisher,
    /* on_eth_frame */ nullptr,
    &vehicle_set_bus_publisher,
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
  if (plugin == nullptr) {
    return;
  }
  if (plugin->vtable != nullptr && plugin->vtable->shutdown != nullptr) {
    plugin->vtable->shutdown(plugin->ctx);
  }
  delete static_cast<VehicleDynamicsPlugin*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }

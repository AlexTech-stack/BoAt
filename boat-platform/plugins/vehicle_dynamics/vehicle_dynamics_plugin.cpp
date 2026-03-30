#include "vehicle_dynamics_plugin.h"

#include <cstdio>
#include <cstring>
#include <string>

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

  plugin->position_m = 0.0;
  plugin->velocity_mps = 0.0;
  plugin->acceleration_mps2 = 0.0;
  plugin->mass_kg = 1000.0;

  const std::string config = config_json == nullptr ? std::string{} : std::string(config_json);
  plugin->velocity_mps = parse_double_value(config, "initial_velocity", plugin->velocity_mps);
  plugin->mass_kg = parse_double_value(config, "mass", plugin->mass_kg);
  plugin->acceleration_mps2 = parse_double_value(config, "acceleration", plugin->acceleration_mps2);
  return 0;
}

void vehicle_on_tick(void* ctx, uint64_t tick) {
  (void)tick;
  auto* plugin = static_cast<VehicleDynamicsPlugin*>(ctx);
  if (plugin == nullptr) {
    return;
  }
  constexpr double kTickDeltaSeconds = 0.01;
  plugin->velocity_mps += plugin->acceleration_mps2 * kTickDeltaSeconds;
  plugin->position_m += plugin->velocity_mps * kTickDeltaSeconds;
}

void vehicle_shutdown(void* ctx) {
  auto* plugin = static_cast<VehicleDynamicsPlugin*>(ctx);
  if (plugin == nullptr) {
    return;
  }
  plugin->position_m = 0.0;
  plugin->velocity_mps = 0.0;
  plugin->acceleration_mps2 = 0.0;
  plugin->mass_kg = 0.0;
}

BoatPluginVTable kVehicleDynamicsVTable = {
    &vehicle_initialize,
    &vehicle_on_tick,
    &vehicle_shutdown,
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

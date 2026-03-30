#include "sensor_model_plugin.h"

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

SensorType parse_sensor_type(const std::string& json) {
  if (json.find("\"sensor_type\":\"CAMERA\"") != std::string::npos ||
      json.find("\"sensor_type\": \"CAMERA\"") != std::string::npos) {
    return SensorType::CAMERA;
  }
  if (json.find("\"sensor_type\":\"RADAR\"") != std::string::npos ||
      json.find("\"sensor_type\": \"RADAR\"") != std::string::npos) {
    return SensorType::RADAR;
  }
  return SensorType::LIDAR;
}

const char* sensor_type_to_cstr(SensorType sensor_type) {
  switch (sensor_type) {
    case SensorType::CAMERA:
      return "CAMERA";
    case SensorType::RADAR:
      return "RADAR";
    case SensorType::LIDAR:
    default:
      return "LIDAR";
  }
}

int sensor_initialize(void* ctx, const char* config_json) {
  auto* plugin = static_cast<SensorModelPlugin*>(ctx);
  if (plugin == nullptr) {
    return -1;
  }
  plugin->sensor_type = SensorType::LIDAR;
  plugin->range_m = 100.0;
  plugin->noise_stddev = 0.01;

  const std::string config = config_json == nullptr ? std::string{} : std::string(config_json);
  plugin->sensor_type = parse_sensor_type(config);
  plugin->range_m = parse_double_value(config, "range_m", plugin->range_m);
  plugin->noise_stddev = parse_double_value(config, "noise_stddev", plugin->noise_stddev);
  return 0;
}

void sensor_on_tick(void* ctx, uint64_t tick) {
  auto* plugin = static_cast<SensorModelPlugin*>(ctx);
  if (plugin == nullptr) {
    return;
  }
  std::fprintf(stderr, "[sensor_model] tick=%llu sensor_type=%s\n",
               static_cast<unsigned long long>(tick), sensor_type_to_cstr(plugin->sensor_type));
}

void sensor_shutdown(void* ctx) {
  auto* plugin = static_cast<SensorModelPlugin*>(ctx);
  if (plugin == nullptr) {
    return;
  }
  plugin->sensor_type = SensorType::LIDAR;
  plugin->range_m = 0.0;
  plugin->noise_stddev = 0.0;
}

BoatPluginVTable kSensorModelVTable = {
    &sensor_initialize,
    &sensor_on_tick,
    &sensor_shutdown,
};

}  // namespace

extern "C" BoatPlugin* boat_plugin_create() {
  auto* plugin_state = new SensorModelPlugin{};
  auto* plugin = new BoatPlugin{};
  plugin->vtable = &kSensorModelVTable;
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
  delete static_cast<SensorModelPlugin*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }

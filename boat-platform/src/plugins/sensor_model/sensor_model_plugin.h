#pragma once

#include <boat/plugin.h>

enum class SensorType {
  LIDAR = 0,
  CAMERA = 1,
  RADAR = 2,
};

struct SensorModelPlugin {
  SensorType sensor_type;
  double range_m;
  double noise_stddev;
};

extern "C" BoatPlugin* boat_plugin_create();
extern "C" void boat_plugin_destroy(BoatPlugin* plugin);
extern "C" uint32_t boat_plugin_abi_version();

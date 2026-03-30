#pragma once

#include <boat/plugin.h>

struct VehicleDynamicsPlugin {
  double position_m;
  double velocity_mps;
  double acceleration_mps2;
  double mass_kg;
};

extern "C" BoatPlugin* boat_plugin_create();
extern "C" void boat_plugin_destroy(BoatPlugin* plugin);
extern "C" uint32_t boat_plugin_abi_version();

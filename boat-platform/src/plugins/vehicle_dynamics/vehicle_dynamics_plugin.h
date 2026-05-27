#pragma once

#include <random>

#include <boat/plugin.h>

struct VehicleDynamicsPlugin {
  double speed_kmh{0.0};
  double rpm{800.0};
  std::mt19937 rng{42};
  std::uniform_real_distribution<double> speed_delta_dist{-3.0, 3.0};
  std::uniform_real_distribution<double> rpm_delta_dist{-120.0, 120.0};
  /* Signal publishing — wired in by PluginManager after initialize(). */
  BoatPublishFn publish_fn{nullptr};
  void* publisher_ctx{nullptr};
  /* CAN publishing — wired in by PluginManager after initialize(). */
  BoatCanPublishFn can_publish_fn{nullptr};
  void* can_publisher_ctx{nullptr};
};

extern "C" BoatPlugin* boat_plugin_create();
extern "C" void boat_plugin_destroy(BoatPlugin* plugin);
extern "C" uint32_t boat_plugin_abi_version();

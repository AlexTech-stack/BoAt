#pragma once

#include <boat/plugin.h>

enum class NetworkProtocol {
  CAN = 0,
  LIN = 1,
  ETHERNET = 2,
};

struct NetworkSimPlugin {
  NetworkProtocol protocol;
  double bus_load_percent;
  uint64_t frame_count;
};

extern "C" BoatPlugin* boat_plugin_create();
extern "C" void boat_plugin_destroy(BoatPlugin* plugin);
extern "C" uint32_t boat_plugin_abi_version();

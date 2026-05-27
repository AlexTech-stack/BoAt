#pragma once

#include <boat/plugin.h>

/* Listens for CAN ID 0x123 on vcan1.
   On each matching frame, publishes 0x234 with payload 0x1122334455667788
   on all registered CAN buses. */
struct CanResponderPlugin {
  BoatCanPublishFn can_publish_fn{nullptr};
  void* can_publisher_ctx{nullptr};
};

extern "C" BoatPlugin* boat_plugin_create();
extern "C" void boat_plugin_destroy(BoatPlugin* plugin);
extern "C" uint32_t boat_plugin_abi_version();

#pragma once

#include <boat/plugin.h>

// Receives every frame dispatched by the plugin manager and forwards it
// to the hardware buses via the frame_publish_fn (wired by the gateway to
// SetFramePublisher → can_registry / eth_registry).
//
// Loopback prevention:
//   CAN:   skips frames where meta.can.flags & BOAT_CAN_FLAG_SELF_SENT
//   ETH:   skips frames where meta.eth.flags  & BOAT_ETH_FLAG_SELF_SENT
struct FrameForwarderPlugin {
  BoatFramePublishFn frame_publish_fn{nullptr};
  void*              frame_publisher_ctx{nullptr};
};

extern "C" BoatPlugin* boat_plugin_create();
extern "C" void       boat_plugin_destroy(BoatPlugin* plugin);
extern "C" uint32_t   boat_plugin_abi_version();

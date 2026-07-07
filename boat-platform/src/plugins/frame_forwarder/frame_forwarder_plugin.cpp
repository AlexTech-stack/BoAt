#include "frame_forwarder_plugin.h"

namespace {

int forwarder_initialize(void* /*ctx*/, const char* /*config_json*/) { return 0; }
void forwarder_on_tick(void* /*ctx*/, uint64_t /*tick*/) {}
void forwarder_shutdown(void* /*ctx*/) {}

void forwarder_set_frame_publisher(void* ctx, BoatFramePublishFn fn, void* pub_ctx) {
  auto* plugin = static_cast<FrameForwarderPlugin*>(ctx);
  if (plugin == nullptr) return;
  plugin->frame_publish_fn    = fn;
  plugin->frame_publisher_ctx = pub_ctx;
}

void forwarder_on_frame(void* ctx, const BoatFrame* frame) {
  auto* plugin = static_cast<FrameForwarderPlugin*>(ctx);
  if (plugin == nullptr || frame == nullptr) return;
  if (plugin->frame_publish_fn == nullptr) return;

  switch (frame->bus_type) {
    case BOAT_BUS_CAN:
    case BOAT_BUS_CANFD:
      // Skip loopback frames sent by ourselves (registry dispatches self-
      // sent frames locally with BOAT_CAN_FLAG_SELF_SENT).
      if (frame->meta.can.flags & BOAT_CAN_FLAG_SELF_SENT) return;
      plugin->frame_publish_fn(plugin->frame_publisher_ctx, frame);
      break;

    case BOAT_BUS_ETHERNET:
      // Same loopback guard — Phase 3 adds this flag.
      if (frame->meta.eth.flags & BOAT_ETH_FLAG_SELF_SENT) return;
      plugin->frame_publish_fn(plugin->frame_publisher_ctx, frame);
      break;

    case BOAT_BUS_PDU:
      plugin->frame_publish_fn(plugin->frame_publisher_ctx, frame);
      break;

    default:
      break;
  }
}

const char* forwarder_declared_buses(void* /*ctx*/) {
  return "[\"can\",\"canfd\",\"eth\",\"pdu\",\"tcp\"]";
}

}  // namespace

extern "C" BoatPlugin* boat_plugin_create() {
  static BoatPluginVTable kVTable = [] {
    BoatPluginVTable vt{};
    vt.initialize           = &forwarder_initialize;
    vt.on_tick              = &forwarder_on_tick;
    vt.shutdown             = &forwarder_shutdown;
    vt.set_publisher        = nullptr;
    vt.set_frame_publisher  = &forwarder_set_frame_publisher;
    vt.on_frame             = &forwarder_on_frame;
    vt.declared_buses       = &forwarder_declared_buses;
    return vt;
  }();

  auto* state  = new FrameForwarderPlugin{};
  auto* plugin = new BoatPlugin{};
  plugin->vtable = &kVTable;
  plugin->ctx    = state;
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (plugin == nullptr) return;
  if (plugin->vtable != nullptr && plugin->vtable->shutdown != nullptr) {
    plugin->vtable->shutdown(plugin->ctx);
  }
  delete static_cast<FrameForwarderPlugin*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }

#include "can_responder_plugin.h"

#include <cstring>

namespace {

constexpr uint32_t kListenCanId  = 0x123;
constexpr uint32_t kRespondCanId = 0x234;
constexpr uint8_t kPayload[8] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88};

int responder_initialize(void* /*ctx*/, const char* /*config_json*/) { return 0; }

void responder_on_tick(void* /*ctx*/, uint64_t /*tick*/) {}

void responder_shutdown(void* /*ctx*/) {}

/* ── v8 callbacks ───────────────────────────────────────────────────── */

void responder_set_frame_publisher(void* ctx, BoatFramePublishFn fn, void* pub_ctx) {
  auto* plugin = static_cast<CanResponderPlugin*>(ctx);
  if (plugin == nullptr) return;
  plugin->frame_publish_fn    = fn;
  plugin->frame_publisher_ctx = pub_ctx;
}

void responder_on_frame(void* ctx, const BoatFrame* frame) {
  auto* plugin = static_cast<CanResponderPlugin*>(ctx);
  if (plugin == nullptr || frame == nullptr) return;
  if (frame->bus_type != BOAT_BUS_CAN && frame->bus_type != BOAT_BUS_CANFD) return;

  if (frame->meta.can.can_id != kListenCanId) return;
  if (frame->iface == nullptr || std::strcmp(frame->iface, "vcan1") != 0) return;

  if (plugin->frame_publish_fn == nullptr) return;

  BoatFrame response{};
  response.bus_type    = BOAT_BUS_CAN;
  response.payload     = const_cast<uint8_t*>(kPayload);
  response.payload_len = 8;
  response.meta.can.can_id = kRespondCanId;
  response.meta.can.dlc    = 8;
  response.meta.can.flags  = 0;
  plugin->frame_publish_fn(plugin->frame_publisher_ctx, &response);
}

const char* responder_declared_buses(void* /*ctx*/) {
  return "[\"can\"]";
}

}  // namespace

extern "C" BoatPlugin* boat_plugin_create() {
  static BoatPluginVTable kVTable = [] {
    BoatPluginVTable vt{};
    vt.initialize           = &responder_initialize;
    vt.on_tick              = &responder_on_tick;
    vt.shutdown             = &responder_shutdown;
    vt.set_publisher        = nullptr;
    vt.set_can_publisher    = nullptr;  // replaced by v8
    vt.on_can_frame         = nullptr;  // replaced by v8
    vt.set_frame_publisher  = &responder_set_frame_publisher;
    vt.on_frame             = &responder_on_frame;
    vt.declared_buses       = &responder_declared_buses;
    return vt;
  }();

  auto* state  = new CanResponderPlugin{};
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
  delete static_cast<CanResponderPlugin*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }

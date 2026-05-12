#include "can_responder_plugin.h"

#include <cstring>

namespace {

constexpr uint32_t kListenCanId  = 0x123;
constexpr uint32_t kRespondCanId = 0x234;
// Payload bytes: 0x11 0x22 0x33 0x44 0x55 0x66 0x77 0x88
constexpr uint8_t kPayload[8] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88};

int responder_initialize(void* /*ctx*/, const char* /*config_json*/) { return 0; }

void responder_on_tick(void* /*ctx*/, uint64_t /*tick*/) {}

void responder_shutdown(void* /*ctx*/) {}

void responder_set_can_publisher(void* ctx, BoatCanPublishFn fn, void* publisher_ctx) {
  auto* plugin = static_cast<CanResponderPlugin*>(ctx);
  if (plugin == nullptr) return;
  plugin->can_publish_fn    = fn;
  plugin->can_publisher_ctx = publisher_ctx;
}

void responder_on_can_frame(void* ctx, const BoatCanFrame* frame, const char* iface) {
  auto* plugin = static_cast<CanResponderPlugin*>(ctx);
  if (plugin == nullptr || frame == nullptr) return;

  // React only to 0x123 arriving on vcan1.
  if (frame->can_id != kListenCanId) return;
  if (iface == nullptr || std::strcmp(iface, "vcan1") != 0) return;

  if (plugin->can_publish_fn == nullptr) return;

  BoatCanFrame response{};
  response.can_id = kRespondCanId;
  response.dlc    = 8;
  std::memcpy(response.data, kPayload, 8);
  plugin->can_publish_fn(plugin->can_publisher_ctx, &response);
}

}  // namespace

extern "C" BoatPlugin* boat_plugin_create() {
  static BoatPluginVTable kVTable = [] {
    BoatPluginVTable vt{};
    vt.initialize        = &responder_initialize;
    vt.on_tick           = &responder_on_tick;
    vt.shutdown          = &responder_shutdown;
    vt.set_publisher     = nullptr;
    vt.set_can_publisher = &responder_set_can_publisher;
    vt.on_can_frame      = &responder_on_can_frame;
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

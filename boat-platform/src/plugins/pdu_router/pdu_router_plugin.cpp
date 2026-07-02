#include "pdu/pdu_router.h"

#include <boat/plugin.h>
#include <core/pdu_router_interface.h>
#include <core/plugin/plugin_manager.h>

#include <cstring>

namespace {

struct PduRouterPlugin {
  boat::hil::PduRouter router;
};

int pdu_router_initialize(void* ctx, const char* /*config_json*/) {
  auto* p = static_cast<PduRouterPlugin*>(ctx);
  if (!p) return -1;
  return 0;
}

void pdu_router_on_tick(void* ctx, uint64_t tick) {
  auto* p = static_cast<PduRouterPlugin*>(ctx);
  if (!p) return;
  p->router.OnTick(tick);
}

void pdu_router_shutdown(void* ctx) {
  auto* p = static_cast<PduRouterPlugin*>(ctx);
  if (!p) return;
  p->router.Stop();
}

void pdu_router_set_frame_publisher(void* ctx, BoatFramePublishFn fn,
                                     void* pub_ctx) {
  auto* p = static_cast<PduRouterPlugin*>(ctx);
  if (!p) return;
  if (fn && pub_ctx) {
    p->router.SetFramePublisher([fn, pub_ctx](const BoatFrame& bf) {
      fn(pub_ctx, &bf);
    });
  }
}

void pdu_router_on_frame(void* ctx, const BoatFrame* frame) {
  auto* p = static_cast<PduRouterPlugin*>(ctx);
  if (!p || !frame) return;
  if (frame->bus_type == BOAT_BUS_CAN || frame->bus_type == BOAT_BUS_CANFD) {
    boat::hil::CanFrame cf{};
    cf.can_id = frame->meta.can.can_id;
    cf.dlc    = frame->meta.can.dlc;
    cf.flags  = frame->meta.can.flags;
    const auto copy_len = frame->payload_len > 64 ? 64U : frame->payload_len;
    if (frame->payload && copy_len > 0)
      std::memcpy(cf.data, frame->payload, copy_len);
    p->router.OnCanFrame(cf, frame->iface ? frame->iface : "");
  } else if (frame->bus_type == BOAT_BUS_ETHERNET) {
    boat::hil::EthernetFrame ef{};
    ef.ethertype = frame->meta.eth.ethertype;
    ef.vlan_id   = frame->meta.eth.vlan_id;
    if (frame->payload && frame->payload_len > 0)
      ef.payload.assign(frame->payload, frame->payload + frame->payload_len);
    p->router.OnEthernetFrame(ef, frame->iface ? frame->iface : "");
  }
}

const char* pdu_router_declared_buses(void* /*ctx*/) {
  return "[\"can\",\"eth\"]";
}

BoatPluginVTable gVTable = {
    &pdu_router_initialize,
    &pdu_router_on_tick,
    &pdu_router_shutdown,
    nullptr,                      // set_publisher
    nullptr,                      // set_can_publisher
    nullptr,                      // on_can_frame
    nullptr,                      // set_eth_publisher
    nullptr,                      // on_eth_frame
    nullptr,                      // set_bus_publisher
    nullptr,                      // set_pdu_publisher
    &pdu_router_on_frame,         // v8 on_frame
    &pdu_router_set_frame_publisher, // v8 set_frame_publisher
    &pdu_router_declared_buses,   // v8 declared_buses
};

}  // namespace

extern "C" BoatPlugin* boat_plugin_create() {
  auto* state = new PduRouterPlugin{};
  auto* plugin = new BoatPlugin{};
  plugin->vtable = &gVTable;
  plugin->ctx    = state;
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (!plugin) return;
  if (plugin->vtable && plugin->vtable->shutdown)
    plugin->vtable->shutdown(plugin->ctx);
  delete static_cast<PduRouterPlugin*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }

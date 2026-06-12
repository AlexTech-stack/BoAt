#pragma once

#include <stddef.h>
#include <stdint.h>

#define BOAT_PLUGIN_ABI_VERSION 6

#ifdef __cplusplus
extern "C" {
#endif

/* Callback a plugin calls to publish a numeric signal value. */
typedef void (*BoatPublishFn)(void* publisher_ctx, const char* signal_id,
                              uint64_t tick, double value);

/* CAN frame type used by the plugin CAN-publish callback.
 * flags bits: CANFD_BRS=0x01, CANFD_ESI=0x02, CANFD_FDF=0x04; 0 = classic CAN. */
typedef struct BoatCanFrame {
  uint32_t can_id;
  uint8_t  dlc;    /* actual byte count: 0-8 classic, 0-64 FD */
  uint8_t  flags;
  uint8_t  data[64];
} BoatCanFrame;

/* Callback a plugin calls to publish a raw CAN frame onto the HIL bus. */
typedef void (*BoatCanPublishFn)(void* publisher_ctx, const BoatCanFrame* frame);

/* Callback the host calls on a plugin to deliver an incoming CAN frame.
   iface is the name of the interface the frame was received on (e.g. "vcan1"). */
typedef void (*BoatCanReceiveFn)(void* ctx, const BoatCanFrame* frame, const char* iface);

/* Ethernet frame type used by the plugin Ethernet-publish callback. */
typedef struct BoatEthFrame {
  uint8_t  dst_mac[6];
  uint8_t  src_mac[6];
  uint16_t ethertype;
  uint8_t* payload;
  size_t   payload_len;
} BoatEthFrame;

/* Callback a plugin calls to publish an Ethernet frame onto the HIL bus. */
typedef void (*BoatEthPublishFn)(void* publisher_ctx, const BoatEthFrame* frame);

/* Callback the host calls on a plugin to deliver an incoming Ethernet frame.
   iface is the name of the interface the frame was received on (e.g. "veth0"). */
typedef void (*BoatEthReceiveFn)(void* ctx, const BoatEthFrame* frame, const char* iface);

/* Callback a plugin calls to publish a named value on the always-on signal bus.
   The bus is independent of any simulation lifecycle. */
typedef void (*BoatBusPublishFn)(void* publisher_ctx, const char* name, double value);

/* PDU frame type used by the PDU-publish callback.
   This is the mechanism for CanTp to deliver reassembled I-PDUs. */
typedef struct BoatPduFrame {
  uint32_t    pdu_id;
  uint8_t*    payload;
  size_t      payload_len;
  const char* iface;  /* interface the frame arrived on */
} BoatPduFrame;

/* Callback a plugin calls to publish a fully-formed PDU into the PduRouter. */
typedef void (*BoatPduPublishFn)(void* publisher_ctx, const BoatPduFrame* frame);

typedef struct BoatPluginVTable {
  int (*initialize)(void* ctx, const char* config_json);
  void (*on_tick)(void* ctx, uint64_t tick);
  void (*shutdown)(void* ctx);
  /* Optional — set to NULL if the plugin does not publish signals. Called once
     before the first tick so the plugin can store the callback. */
  void (*set_publisher)(void* ctx, BoatPublishFn fn, void* publisher_ctx);
  /* Optional — set to NULL if the plugin does not publish CAN frames. */
  void (*set_can_publisher)(void* ctx, BoatCanPublishFn fn, void* publisher_ctx);
  /* Optional — set to NULL if the plugin does not react to incoming CAN frames.
     The host calls this function directly to deliver each received frame. */
  BoatCanReceiveFn on_can_frame;
  /* Optional — set to NULL if the plugin does not publish Ethernet frames. */
  void (*set_eth_publisher)(void* ctx, BoatEthPublishFn fn, void* publisher_ctx);
  /* Optional — set to NULL if the plugin does not react to incoming Ethernet frames. */
  BoatEthReceiveFn on_eth_frame;
  /* Optional — set to NULL if the plugin does not publish bus signals. */
  void (*set_bus_publisher)(void* ctx, BoatBusPublishFn fn, void* publisher_ctx);
  /* Optional (v6) — set to NULL if the plugin does not publish PDU frames.
     The host wires this to PduRouter::SendPdu(). */
  void (*set_pdu_publisher)(void* ctx, BoatPduPublishFn fn, void* publisher_ctx);
} BoatPluginVTable;

typedef struct BoatPlugin {
  BoatPluginVTable* vtable;
  void* ctx;
} BoatPlugin;

typedef BoatPlugin* (*boat_plugin_create_fn)();
typedef void (*boat_plugin_destroy_fn)(BoatPlugin* plugin);
typedef uint32_t (*boat_plugin_abi_version_fn)();

BoatPlugin* boat_plugin_create();
// Ownership contract: `boat_plugin_destroy()` is responsible for full teardown,
// including invoking `vtable->shutdown(ctx)` exactly once before freeing memory.
void boat_plugin_destroy(BoatPlugin* plugin);
uint32_t boat_plugin_abi_version();

#ifdef __cplusplus
}
#endif

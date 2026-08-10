#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* CanTp N-SDU connection configuration (ISO 15765-2).
   A connection is identified by nsdu_id and represents one session between
   source_addr (this node) and target_addr (peer node), both required and
   non-zero -- a single-ID session (one CAN ID for both directions) is
   expressed by passing the same value for both, not by omitting them. The
   same session handles both TX and RX:
     - We send data (FF/CF) and receive FC on source_addr.
     - We receive data (SF/FF/CF) and send FC on target_addr.

   Of ISO 15765-2's six timing parameters (§9.8 Table 21), only N_Bs and
   N_Cr are enforced here -- they're the two whose expiry actually leaves a
   session stuck forever (a peer that dies mid-transfer). N_As/N_Ar are
   local transmit-confirmation timeouts with no analogue in this software
   transport (frame_publish_fn is synchronous -- there is nothing to time
   out waiting for), and N_Br/N_Cs are soft performance targets, not
   correctness bugs. See backlog/can_tp_plugin_backlog.md item #1. */
typedef struct CanTpConfig {
  uint32_t nsdu_id;            /* Logical session identifier (map key) */
  uint32_t source_addr;        /* CAN ID of this node (required, non-zero) */
  uint32_t target_addr;        /* CAN ID of the peer node (required, non-zero) */
  uint32_t rx_buffer_size;     /* max reassembly buffer (default 4095) */
  uint8_t  block_size;         /* BS to advertise in sent FC (0 = unlimited) */
  uint8_t  st_min;             /* STmin to advertise in sent FC (0..127 ms) */
  uint8_t  can_dlc;            /* max CAN DLC for this connection (8 or 64) */
  bool     extended_addressing;/* use first data byte as target address */
  uint32_t n_bs_ms;            /* ISO 15765-2 N_Bs: max time TX waits for FC
                                   after FF/last CF of a block, before
                                   aborting the transfer (0 = ISO default,
                                   1000ms) */
  uint32_t n_cr_ms;            /* ISO 15765-2 N_Cr: max time RX waits for the
                                   next CF before aborting reassembly
                                   (0 = ISO default, 1000ms) */
  bool     brs;                /* CAN FD Bit Rate Switch -- use a faster data-
                                   phase bit rate for this connection's
                                   frames. Only meaningful when can_dlc > 8;
                                   ignored for classic CAN. Not forced on
                                   automatically, since not every CAN FD bus
                                   is configured with a distinct data-phase
                                   bit rate to switch to. */
  uint8_t  pad_byte;           /* Fill byte for unused trailing data bytes on
                                   every emitted SF/FF/CF/FC (0 = ISO/AUTOSAR
                                   default, 0xCC). Note: because 0 is the
                                   "use default" sentinel here (same
                                   convention as n_bs_ms/n_cr_ms), literal
                                   0x00 padding isn't independently
                                   selectable through this field -- pick a
                                   value your peer won't confuse with real
                                   data if 0xCC doesn't work for your case. */
} CanTpConfig;

/* Send a PDU through CanTp segmentation to an already-configured nsdu_id.
   Returns 1 for a single-frame send, 0 for multi-frame (initiated
   asynchronously via the internal TX thread), or -1 on error (not
   configured, or a multi-frame transfer is already in progress). */
int32_t can_tp_send(void* tp_ctx, uint32_t nsdu_id,
                    const uint8_t* data, uint32_t len);

/* Configure an N-SDU connection. Also doubles as "edit": calling this again
   for an already-configured nsdu_id edits its parameters in place.
   Returns:
     0  success
    -1  invalid config (source_addr/target_addr zero, etc.)
    -2  nsdu_id already exists and has an active TX or RX transfer in
        progress -- edit-in-place is refused rather than silently
        discarding it (retry once it settles, or remove it first)
    -3  target_addr is already used by a *different* nsdu_id on this
        instance -- every connection's target_addr must be unique so
        incoming frames route unambiguously (see find_by_target()) */
int32_t can_tp_configure(void* tp_ctx, const CanTpConfig* config);

/* Remove a configured N-SDU connection. Returns 0 on success, -1 if nsdu_id
   isn't configured, -2 if a multi-frame transfer is in progress (retry once
   it settles -- not forced, to avoid disrupting an in-flight transfer). */
int32_t can_tp_remove(void* tp_ctx, uint32_t nsdu_id);

#ifdef __cplusplus
}
#endif

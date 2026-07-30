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
     - We receive data (SF/FF/CF) and send FC on target_addr. */
typedef struct CanTpConfig {
  uint32_t nsdu_id;            /* Logical session identifier (map key) */
  uint32_t source_addr;        /* CAN ID of this node (required, non-zero) */
  uint32_t target_addr;        /* CAN ID of the peer node (required, non-zero) */
  uint32_t rx_buffer_size;     /* max reassembly buffer (default 4095) */
  uint8_t  block_size;         /* BS to advertise in sent FC (0 = unlimited) */
  uint8_t  st_min;             /* STmin to advertise in sent FC (0..127 ms) */
  uint8_t  can_dlc;            /* max CAN DLC for this connection (8 or 64) */
  bool     extended_addressing;/* use first data byte as target address */
} CanTpConfig;

/* Send a PDU through CanTp segmentation to an already-configured nsdu_id.
   Returns 1 for a single-frame send, 0 for multi-frame (initiated
   asynchronously via the internal TX thread), or -1 on error (not
   configured, or a multi-frame transfer is already in progress). */
int32_t can_tp_send(void* tp_ctx, uint32_t nsdu_id,
                    const uint8_t* data, uint32_t len);

/* Configure an N-SDU connection. Returns 0 on success, -1 on invalid config.
   Also doubles as "edit": calling this again for an already-configured
   nsdu_id overwrites its parameters in place. */
int32_t can_tp_configure(void* tp_ctx, const CanTpConfig* config);

/* Remove a configured N-SDU connection. Returns 0 on success, -1 if nsdu_id
   isn't configured, -2 if a multi-frame transfer is in progress (retry once
   it settles -- not forced, to avoid disrupting an in-flight transfer). */
int32_t can_tp_remove(void* tp_ctx, uint32_t nsdu_id);

#ifdef __cplusplus
}
#endif

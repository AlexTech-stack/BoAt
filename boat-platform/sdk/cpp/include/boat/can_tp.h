#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* CanTp N-SDU connection configuration (ISO 15765-2). */
typedef struct CanTpConfig {
  uint32_t nsdu_id;            /* N-SDU identifier (typically CAN ID) */
  uint32_t rx_buffer_size;     /* max reassembly buffer (default 4095) */
  uint8_t  block_size;         /* Flow Control BS (0..255) */
  uint8_t  st_min;             /* Separation Time in ms (0..127) */
  uint8_t  can_dlc;            /* max CAN DLC for this connection (8 or 64) */
  bool     extended_addressing;/* use first data byte as target address */
  bool     is_rx;              /* true = receive-only, false = both */
} CanTpConfig;

/* Send a large PDU through CanTp segmentation.
   Returns the number of CAN frames sent, or -1 on error. */
int32_t can_tp_send(void* tp_ctx, uint32_t nsdu_id,
                    const uint8_t* data, uint32_t len);

/* Configure an N-SDU connection. Returns 0 on success. */
int32_t can_tp_configure(void* tp_ctx, const CanTpConfig* config);

#ifdef __cplusplus
}
#endif

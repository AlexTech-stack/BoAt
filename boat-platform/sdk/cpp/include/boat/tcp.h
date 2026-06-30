#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// ── TCP event types ─────────────────────────────────────────────────────────
#define TCP_EVENT_CONNECTED 0
#define TCP_EVENT_CLOSED    1
#define TCP_EVENT_RST       2
#define TCP_EVENT_ACCEPTED  3
#define TCP_EVENT_ERROR     4

// ── Callback types ──────────────────────────────────────────────────────────
typedef void (*TcpOnDataFn)(void* user_ctx, int conn_id,
                             const uint8_t* data, uint32_t len);
typedef void (*TcpOnEventFn)(void* user_ctx, int conn_id, int event);

// ── Client API ──────────────────────────────────────────────────────────────

/* Open an outgoing TCP connection.
   Returns conn_id (> 0) on success, -1 on error.
   The connection is established asynchronously — wait for TCP_EVENT_CONNECTED
   callback before calling tcp_send(). */
int tcp_connect(void* ctx, const char* src_ip, uint16_t src_port,
                const char* dst_ip, uint16_t dst_port);

/* Send data on an established connection.
   Returns number of bytes accepted on success, -1 on error. */
int tcp_send(void* ctx, int conn_id, const uint8_t* data, uint32_t len);

// ── Server API ──────────────────────────────────────────────────────────────

/* Start listening on an address.
   Returns listener_id (> 0) on success, -1 on error.
   Incoming connections fire TCP_EVENT_CONNECTED on the listener's event
   callback, with the newly created conn_id. */
int tcp_listen(void* ctx, const char* bind_ip, uint16_t bind_port);

// ── Both modes ──────────────────────────────────────────────────────────────

/* Register callbacks for a connection or listener.
   For listeners: the event callback fires TCP_EVENT_CONNECTED for each
   new accepted connection (with the new conn_id).
   For connections: on_data is called when data arrives, on_event for
   lifecycle events. */
void tcp_set_callbacks(void* ctx, int conn_or_listener_id,
                       TcpOnDataFn on_data, TcpOnEventFn on_event,
                       void* user_ctx);

/* Gracefully close a connection (FIN handshake). */
int tcp_close(void* ctx, int conn_id);

/* Abort a connection (send RST). */
int tcp_abort(void* ctx, int conn_id);

#ifdef __cplusplus
}
#endif

#pragma once

#include <boat/can_tp.h>

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace boat::core {

/* Snapshot of one configured N-SDU connection, for introspection
   (ListSessions). rx_state/tx_state are human-readable, not the internal
   enum values, since this crosses the plugin/gRPC boundary. */
struct CanTpSessionInfo {
  uint32_t nsdu_id;
  uint32_t source_addr;
  uint32_t target_addr;
  uint32_t rx_buffer_size;
  uint8_t  block_size;
  uint8_t  st_min;
  uint8_t  can_dlc;
  bool     extended_addressing;
  uint32_t n_bs_ms;       // effective N_Bs (already resolved from the 0-sentinel)
  uint32_t n_cr_ms;       // effective N_Cr (already resolved from the 0-sentinel)
  bool     brs;
  uint8_t  pad_byte;      // effective pad byte (already resolved from the 0-sentinel)
  uint32_t addressing_mode;     // CanTpAddressingMode (0=NORMAL, 1=EXTENDED, 2=MIXED)
  uint8_t  address_byte;        // effective N_TA/N_AE (already resolved from the 0-sentinel); meaningless for NORMAL
  std::string rx_state;  // "IDLE" | "WAIT_CF"
  std::string tx_state;  // "IDLE" | "WAIT_FC" | "SEND_CF" | "COMPLETE"
};

/* One asynchronous error/abort event on a connection -- ISO 15765-2's
   N_Result values, the subset this plugin can actually detect and
   attribute to a specific nsdu_id. Not every failure mode gets one (e.g.
   Send() returning -1 for "busy" is already a synchronous, directly-
   visible return value; an unrecognized incoming CAN ID has no connection
   to attribute the drop to). See CanTpResult in boat/can_tp.h. */
struct CanTpErrorEvent {
  uint32_t    nsdu_id;
  uint32_t    result;   // CanTpResult
  std::string message;  // human-readable detail, e.g. "N_Bs expired after 1000ms"
};

/* Interface that the CanTp plugin exposes to gRPC CanTpService.
   The plugin registers itself via PluginManager::RegisterService("can_tp", this)
   during Load() (see boat_plugin_service_name()/boat_plugin_service_ptr()).
   CanTpServiceImpl looks it up and delegates all calls. */
class ICanTp {
 public:
  virtual ~ICanTp() = default;

  // Configure an N-SDU connection. Returns 0 on success, -1 on invalid config.
  virtual int32_t Configure(const CanTpConfig& config) = 0;

  // Send a PDU through CanTp segmentation.
  // Returns 1 for a single-frame send, 0 for multi-frame (initiated
  // asynchronously via the plugin's internal TX thread), or -1 on error
  // (no connection configured for nsdu_id, or connection busy).
  virtual int32_t Send(uint32_t nsdu_id, const uint8_t* data, uint32_t len) = 0;

  // True if a connection has already been Configure()'d for this nsdu_id,
  // so callers can distinguish "never configured" from "busy" when Send()
  // returns -1.
  virtual bool HasConnection(uint32_t nsdu_id) const = 0;

  // The CAN interface this plugin instance is bound to (from its load-time
  // JSON config), for client-side validation of an expected iface.
  virtual std::string GetIface() const = 0;

  // Snapshot of every currently-configured N-SDU connection on this
  // instance, for introspection (`boat can-tp list-sessions`).
  virtual std::vector<CanTpSessionInfo> ListSessions() const = 0;

  // Delete a configured N-SDU connection. Returns 0 on success; -1 if no
  // connection is configured for nsdu_id; -2 if it's busy with an in-progress
  // multi-frame transmission (caller must wait/retry, not force it, to avoid
  // erasing a connection the TX pacing thread is actively working with).
  virtual int32_t Remove(uint32_t nsdu_id) = 0;

  // Decoded-payload subscription, for `CanTpService.Subscribe` /
  // `boat can-tp subscribe`. Invoked once per completed RX (Single Frame, or
  // a fully-reassembled multi-frame payload) on a matching nsdu_id.
  using RxCallback = std::function<void(uint32_t nsdu_id, const std::vector<uint8_t>& payload)>;
  using SubId = std::size_t;

  // Subscribe to decoded RX payloads. Empty nsdu_ids means "every session on
  // this instance". Mirrors PduRouter::Subscribe (src/hil/pdu/pdu_router.h).
  virtual SubId Subscribe(std::vector<uint32_t> nsdu_ids, RxCallback cb) = 0;
  virtual void  Unsubscribe(SubId id) = 0;

  // Error/abort event subscription, for `CanTpService.SubscribeErrors` /
  // `boat can-tp subscribe-errors`. Invoked on N_Bs/N_Cr timeout, a wrong
  // CF sequence number, or an RX/peer-signaled buffer overflow -- the
  // detectable subset of ISO 15765-2's N_Result values (see
  // CanTpErrorEvent). Same empty-nsdu_ids-means-"every session" convention
  // as Subscribe().
  using ErrorCallback = std::function<void(const CanTpErrorEvent&)>;
  virtual SubId SubscribeErrors(std::vector<uint32_t> nsdu_ids, ErrorCallback cb) = 0;
  virtual void  UnsubscribeErrors(SubId id) = 0;
};

}  // namespace boat::core

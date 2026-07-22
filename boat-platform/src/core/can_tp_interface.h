#pragma once

#include <boat/can_tp.h>

#include <cstdint>
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
  std::string rx_state;  // "IDLE" | "WAIT_CF"
  std::string tx_state;  // "IDLE" | "WAIT_FC" | "SEND_CF" | "COMPLETE"
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

  // True if a connection has already been Configure()'d for this nsdu_id
  // (matched either by source_addr key or by the .nsdu_id field), so callers
  // can distinguish "never configured" from "busy" when Send() returns -1.
  virtual bool HasConnection(uint32_t nsdu_id) const = 0;

  // The CAN interface this plugin instance is bound to (from its load-time
  // JSON config), for client-side validation of an expected iface.
  virtual std::string GetIface() const = 0;

  // Snapshot of every currently-configured N-SDU connection on this
  // instance, for introspection (`boat can-tp list-sessions`).
  virtual std::vector<CanTpSessionInfo> ListSessions() const = 0;
};

}  // namespace boat::core

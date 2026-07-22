#pragma once

#include <boat/plugin.h>
#include <boat/can_tp.h>

#include <core/can_tp_interface.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

// Standalone CanTp API — declared here (ahead of the CanTpPlugin struct
// definition below) so CanTpPlugin's ICanTp forwarders can call them.
extern "C" int32_t can_tp_send(void* tp_ctx, uint32_t nsdu_id,
                               const uint8_t* data, uint32_t len);
extern "C" int32_t can_tp_configure(void* tp_ctx, const CanTpConfig* config);

/* ISO 15765-2 N-SDU connection state.
   A connection is keyed by source_addr and handles both TX and RX
   directions within one session (source_addr ↔ target_addr). */
struct NsduConnection {
  uint32_t nsdu_id;
  uint32_t source_addr;
  uint32_t target_addr;
  CanTpConfig config;

  // RX reassembly state
  enum RxState { RX_IDLE, RX_WAIT_CF } rx_state{RX_IDLE};
  std::vector<uint8_t> rx_buffer;
  uint32_t rx_expected_len{0};
  uint8_t  rx_next_seq{0};

  // TX state machine
  enum TxState {
    TX_IDLE,      // no TX in progress
    TX_WAIT_FC,   // FF sent, awaiting Flow Control from peer
    TX_SEND_CF,   // FC received, sending Consecutive Frames
    TX_COMPLETE   // all CFs sent (transitions to TX_IDLE on next check)
  } tx_state{TX_IDLE};

  std::vector<uint8_t> tx_buffer;
  uint32_t tx_offset{0};
  uint8_t  tx_seq{0};
  uint8_t  tx_bs_remaining{0};    // BS remaining before needing next FC
  uint8_t  tx_bs_original{0};     // BS from the received FC (0 = unlimited)
  uint32_t tx_stmin_us{0};        // STmin from peer in microseconds
  std::chrono::steady_clock::time_point tx_next_send_time;

  // RX CF tracking for re-FC (BS > 0)
  uint32_t rx_cf_count{0};
};

/* CanTp plugin state.
   Also implements ICanTp so PluginManager::FindService("can_tp") can hand
   gateway-side gRPC code a live pointer straight at this instance (see
   boat_plugin_service_name()/boat_plugin_service_ptr() in the .cpp) — the
   ICanTp methods below are thin forwarders to the existing can_tp_configure/
   can_tp_send free functions, which remain the single source of truth. */
struct CanTpPlugin : public boat::core::ICanTp {
  BoatFramePublishFn  frame_publish_fn{nullptr};
  void*               frame_publisher_ctx{nullptr};
  BoatPduPublishFn    pdu_publish_fn{nullptr};
  void*               pdu_publisher_ctx{nullptr};
  std::unordered_map<uint32_t, NsduConnection> connections;  // keyed by source_addr
  std::string         iface;
  // "can_tp:" + iface, computed once in tp_initialize() after iface is
  // resolved. Returned by boat_plugin_service_name() so multiple loaded
  // instances (one per CAN interface) register distinct service names
  // instead of colliding on a single fixed "can_tp".
  std::string         service_name;

  // TX pacing thread + synchronization
  std::thread         tx_thread;
  mutable std::mutex  tx_mutex;
  std::condition_variable tx_cv;
  std::atomic<bool>   tx_stop{false};

  // ── ICanTp ──────────────────────────────────────────────────────────────
  int32_t Configure(const CanTpConfig& config) override {
    return can_tp_configure(this, &config);
  }
  int32_t Send(uint32_t nsdu_id, const uint8_t* data, uint32_t len) override {
    return can_tp_send(this, nsdu_id, data, len);
  }
  bool HasConnection(uint32_t nsdu_id) const override {
    std::lock_guard<std::mutex> lock(tx_mutex);
    if (connections.find(nsdu_id) != connections.end()) return true;
    for (const auto& [addr, c] : connections) {
      (void)addr;
      if (c.nsdu_id == nsdu_id) return true;
    }
    return false;
  }
  std::string GetIface() const override { return iface; }

  std::vector<boat::core::CanTpSessionInfo> ListSessions() const override {
    std::vector<boat::core::CanTpSessionInfo> result;
    std::lock_guard<std::mutex> lock(tx_mutex);
    result.reserve(connections.size());
    for (const auto& [addr, c] : connections) {
      (void)addr;
      boat::core::CanTpSessionInfo info{};
      info.nsdu_id             = c.nsdu_id;
      info.source_addr         = c.source_addr;
      info.target_addr         = c.target_addr;
      info.rx_buffer_size      = c.config.rx_buffer_size;
      info.block_size          = c.config.block_size;
      info.st_min              = c.config.st_min;
      info.can_dlc             = c.config.can_dlc;
      info.extended_addressing = c.config.extended_addressing;
      info.rx_state = (c.rx_state == NsduConnection::RX_WAIT_CF) ? "WAIT_CF" : "IDLE";
      switch (c.tx_state) {
        case NsduConnection::TX_IDLE:     info.tx_state = "IDLE";     break;
        case NsduConnection::TX_WAIT_FC:  info.tx_state = "WAIT_FC";  break;
        case NsduConnection::TX_SEND_CF:  info.tx_state = "SEND_CF";  break;
        case NsduConnection::TX_COMPLETE: info.tx_state = "COMPLETE"; break;
      }
      result.push_back(std::move(info));
    }
    return result;
  }
};

extern "C" BoatPlugin* boat_plugin_create();
extern "C" void boat_plugin_destroy(BoatPlugin* plugin);
extern "C" uint32_t boat_plugin_abi_version();

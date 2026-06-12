#pragma once

#include <boat/plugin.h>
#include <boat/can_tp.h>

#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

/* ISO 15765-2 N-SDU connection state. */
struct NsduConnection {
  uint32_t nsdu_id;
  CanTpConfig config;

  // RX reassembly state
  enum RxState { RX_IDLE, RX_WAIT_CF, RX_WAIT_CONTINUATION } rx_state{RX_IDLE};
  std::vector<uint8_t> rx_buffer;
  uint32_t rx_expected_len{0};
  uint8_t  rx_next_seq{0};

  // TX segmentation state
  std::vector<uint8_t> tx_buffer;
  uint32_t tx_offset{0};
  uint8_t  tx_seq{0};
  uint8_t  tx_bs_remaining{0};
  bool     tx_wait_fc{false};
};

/* CanTp plugin state. */
struct CanTpPlugin {
  BoatCanPublishFn  can_publish_fn{nullptr};
  void*             can_publisher_ctx{nullptr};
  BoatPduPublishFn  pdu_publish_fn{nullptr};
  void*             pdu_publisher_ctx{nullptr};
  std::unordered_map<uint32_t, NsduConnection> connections;
  std::string       iface;  // CAN interface to operate on
};

extern "C" BoatPlugin* boat_plugin_create();
extern "C" void boat_plugin_destroy(BoatPlugin* plugin);
extern "C" uint32_t boat_plugin_abi_version();

// Standalone CanTp API
extern "C" int32_t can_tp_send(void* tp_ctx, uint32_t nsdu_id,
                               const uint8_t* data, uint32_t len);
extern "C" int32_t can_tp_configure(void* tp_ctx, const CanTpConfig* config);

// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "can_tp_plugin.h"

#include <cstring>
#include <sstream>
#include <string>

std::uint64_t CanTpPlugin::NowNs() const {
  if (now_ns_fn != nullptr) return now_ns_fn(now_ns_ctx);
  // No host clock (plugin loaded standalone): fall back to the system
  // monotonic clock so behaviour is unchanged for embedders that don't supply
  // one. Determinism needs the host clock.
  return static_cast<std::uint64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::steady_clock::now().time_since_epoch())
          .count());
}

namespace {

// PCI byte definitions per ISO 15765-2
constexpr uint8_t kPciSf    = 0x00;  // Single Frame
constexpr uint8_t kPciFf    = 0x10;  // First Frame
constexpr uint8_t kPciCf    = 0x20;  // Consecutive Frame
constexpr uint8_t kPciFc    = 0x30;  // Flow Control
constexpr uint8_t kPciMask  = 0xF0;

constexpr uint8_t kFcContinue   = 0x00;  // FC flags: Continue To Send
constexpr uint8_t kFcWait       = 0x01;  // FC flags: Wait
constexpr uint8_t kFcOverflow   = 0x02;  // FC flags: Overflow / abort

// ISO 15765-2/AUTOSAR CanTp320-325 default fill byte for unused trailing
// data bytes. Configurable per connection (CanTpConfig::pad_byte).
constexpr uint8_t kDefaultPadByte = 0xCC;

// ISO 15765-2 default for both N_Bs (TX waiting for FC) and N_Cr (RX waiting
// for the next CF) -- see CanTpConfig::n_bs_ms/n_cr_ms in boat/can_tp.h.
constexpr uint32_t kDefaultTimeoutMs = 1000;

// Resolve a 0-sentinel (== "use the ISO default") to an actual timeout.
uint32_t resolve_timeout_ms(uint32_t configured) {
  return configured != 0 ? configured : kDefaultTimeoutMs;
}

// ISO 15765-2 N_WFTmax. The standard leaves the exact value to the network;
// four consecutive FC(WT) frames is enough to distinguish a peer that is
// still alive from one that is stalling the transfer indefinitely.
constexpr uint8_t kMaxConsecutiveWaitFrames = 4;

// Resolve a 0-sentinel (== "use the ISO/AUTOSAR default") to an actual pad
// byte. See CanTpConfig::pad_byte's doc comment for why literal 0x00
// padding isn't independently selectable through this field.
uint8_t resolve_pad_byte(uint8_t configured) {
  return configured != 0 ? configured : kDefaultPadByte;
}

// CAN flags byte for an emitted frame -- see boat/frame.h: CANFD_BRS=0x01,
// CANFD_FDF=0x04. FDF is implied by is_fd; BRS is a separate per-connection
// choice (CanTpConfig::brs) since not every CAN FD bus has a distinct
// data-phase bit rate configured to switch to. Meaningless (and dropped)
// for classic CAN.
uint8_t can_flags(bool is_fd, bool brs) {
  if (!is_fd) return 0;
  return static_cast<uint8_t>(0x04 | (brs ? 0x01 : 0));
}

// Every CanTp-emitted frame (SF/FF/CF/FC) is sent at the connection's fixed
// can_dlc, not the length actually in use -- unused trailing bytes are
// filled with pad_byte (already resolved from the 0-sentinel by the caller).
// `used` is the number of meaningful bytes already written to buf (PCI/
// addressing + payload); pads buf[used..dlc) and returns dlc, the DLC/
// payload_len to publish the frame with.
uint8_t pad_frame(uint8_t* buf, uint8_t used, uint8_t dlc, uint8_t pad_byte) {
  for (uint8_t i = used; i < dlc; ++i) buf[i] = pad_byte;
  return dlc;
}

// ── STmin helpers ──────────────────────────────────────────────────────────

// Convert ISO 15765-2 STmin byte to microseconds.
//   0x00–0x7F : directly in ms (0–127 ms)
//   0xF1–0xF9 : 100–900 μs (steps of 100 μs)
//   0x80–0xF0 and 0xFA–0xFF : reserved; ISO 15765-2 requires the maximum
//   separation time (127 ms), not zero/full-speed.
uint32_t stmin_to_us(uint8_t stmin) {
  if (stmin <= 0x7F) return static_cast<uint32_t>(stmin) * 1000;
  if (stmin >= 0xF1 && stmin <= 0xF9)
    return static_cast<uint32_t>(stmin - 0xF0) * 100;
  return 127'000;
}

// ── Connection lookup helpers ─────────────────────────────────────────────

// Finds the connection this incoming frame belongs to. Usually a single
// target_addr match is unambiguous -- but can_tp_configure() allows more
// than one connection to share a target_addr when every sharer has an
// address byte (N_TA/N_AE, EXTENDED/MIXED addressing) with a distinct
// value, exactly so multiple logical peers can be multiplexed onto one
// physical CAN ID. When that happens, disambiguate by reading the address
// byte (always payload[0] when present, regardless of 11- vs 29-bit ID --
// the addressing mode only affects the CAN ID field, never the payload
// layout).
NsduConnection* find_by_target(CanTpPlugin* plugin, uint32_t can_id,
                               const uint8_t* payload, size_t payload_len) {
  NsduConnection* match = nullptr;
  bool ambiguous = false;
  for (auto& [id, conn] : plugin->connections) {
    if (conn.target_addr != can_id) continue;
    if (match == nullptr) {
      match = &conn;
    } else {
      ambiguous = true;
      break;
    }
  }
  if (!ambiguous) return match;  // 0 or 1 candidates either way

  if (payload_len < 1) return nullptr;
  const uint8_t addr_byte = payload[0];
  for (auto& [id, conn] : plugin->connections) {
    if (conn.target_addr == can_id && conn.config.address_byte == addr_byte) {
      return &conn;
    }
  }
  return nullptr;  // no candidate claims this address byte
}

// ── TX thread ──────────────────────────────────────────────────────────────

/* One TX/watchdog pass: send any CF whose STmin has elapsed, and fire any
   N_Bs/N_Cr deadline that has passed. Returns true if it sent at least one CF,
   which tp_on_tick uses to decide whether more may already be due.

   This was a dedicated thread looping on steady_clock with a condition-variable
   sleep. It is now called from on_tick, so CanTp's pacing and watchdogs run on
   the host's clock alongside every other tick consumer instead of on a private
   time domain -- which is what lets a virtual clock reach ISO-TP timeouts. */
bool can_tp_service_pending(CanTpPlugin* plugin) {
  if (plugin == nullptr || plugin->frame_publish_fn == nullptr) return false;

  // Collect everything the send-CF phase needs while holding the lock,
  // including a copy of the payload chunk. The old version kept a raw
  // NsduConnection* across the unlock and re-locked to memcpy from
  // tx_buffer; a concurrent FC(Overflow)/Remove could clear or erase that
  // state in between. The connection is re-looked-up by nsdu_id before its
  // state is advanced.
  struct TxWork {
    uint32_t nsdu_id;
    uint32_t source_addr;
    uint8_t seq;
    uint32_t offset;
    uint8_t dlc;
    bool is_fd;
    bool brs;
    bool extended_addressing;
    uint8_t address_byte;
    uint8_t pad_byte;
    std::vector<uint8_t> data;
  };
  std::vector<TxWork> to_send_cf;

  {
    std::lock_guard<std::mutex> lock(plugin->tx_mutex);
    const std::uint64_t now = plugin->NowNs();
    for (auto& [addr, conn] : plugin->connections) {
      (void)addr;
      if (conn.tx_state == NsduConnection::TX_SEND_CF &&
          now >= conn.tx_next_send_time) {
        const uint32_t max_payload = conn.config.can_dlc;
        const uint32_t cf_overhead = conn.config.extended_addressing ? 2 : 1;
        if (max_payload > cf_overhead && conn.tx_offset <= conn.tx_buffer.size()) {
          const std::size_t chunk = std::min(
              conn.tx_buffer.size() - conn.tx_offset,
              static_cast<std::size_t>(max_payload - cf_overhead));
          if (chunk > 0) {
            TxWork work;
            work.nsdu_id = conn.nsdu_id;
            work.source_addr = conn.source_addr;
            work.seq = conn.tx_seq;
            work.offset = conn.tx_offset;
            work.dlc = conn.config.can_dlc;
            work.is_fd = conn.config.can_dlc > 8;
            work.brs = conn.config.brs;
            work.extended_addressing = conn.config.extended_addressing;
            work.address_byte = conn.config.address_byte;
            work.pad_byte = conn.config.pad_byte;
            work.data.assign(conn.tx_buffer.begin() + conn.tx_offset,
                             conn.tx_buffer.begin() + conn.tx_offset + chunk);
            to_send_cf.push_back(std::move(work));
          }
        }
      }

      // ── N_Bs watchdog: TX gave up waiting for FC ──────────────────────
      // Fires whether we're waiting for the *first* FC after FF, or the
      // next FC at a block boundary (both go through TX_WAIT_FC) -- ISO
      // 15765-2 §9.8 uses N_Bs for both cases.
      if (conn.tx_state == NsduConnection::TX_WAIT_FC &&
          now >= conn.tx_fc_deadline) {
        conn.tx_state = NsduConnection::TX_IDLE;
        conn.tx_buffer.clear();
        conn.tx_offset = 0;
        conn.tx_seq = 0;
        conn.tx_wait_frames = 0;
        plugin->NotifyError(conn.nsdu_id, CANTP_N_TIMEOUT_BS,
            "N_Bs expired after " + std::to_string(conn.config.n_bs_ms) +
            "ms waiting for Flow Control");
      }

      // ── N_Cr watchdog: RX gave up waiting for the next CF ─────────────
      if (conn.rx_state == NsduConnection::RX_WAIT_CF &&
          now >= conn.rx_cf_deadline) {
        conn.rx_state = NsduConnection::RX_IDLE;
        conn.rx_buffer.clear();
        plugin->NotifyError(conn.nsdu_id, CANTP_N_TIMEOUT_CR,
            "N_Cr expired after " + std::to_string(conn.config.n_cr_ms) +
            "ms waiting for the next Consecutive Frame");
      }
    }
  }

  if (to_send_cf.empty()) return false;

  // Send CFs without holding the lock. The connection is re-found by id
  // before advancing state; if it was removed, reconfigured, or aborted by
  // an FC in the meantime, this snapshot is simply discarded.
  for (auto& work : to_send_cf) {
    uint8_t cf_buf[64];
    uint8_t idx = 0;
    if (work.extended_addressing) {
      cf_buf[idx++] = work.address_byte;
    }
    cf_buf[idx++] = kPciCf | (work.seq & 0x0F);
    std::memcpy(cf_buf + idx, work.data.data(), work.data.size());
    const uint8_t cf_dlc = pad_frame(
        cf_buf, static_cast<uint8_t>(idx + work.data.size()), work.dlc,
        work.pad_byte);

    auto cf = BoatFrameOwner::Can(
        plugin->iface, work.source_addr, cf_dlc,
        can_flags(work.is_fd, work.brs),
        std::vector<uint8_t>(cf_buf, cf_buf + cf_dlc), work.is_fd);
    plugin->frame_publish_fn(plugin->frame_publisher_ctx, cf.get());

    std::lock_guard<std::mutex> lock(plugin->tx_mutex);
    auto it = plugin->connections.find(work.nsdu_id);
    if (it == plugin->connections.end()) continue;
    auto& conn = it->second;
    if (conn.tx_state != NsduConnection::TX_SEND_CF ||
        conn.tx_offset != work.offset) {
      continue;
    }
    conn.tx_offset += static_cast<uint32_t>(work.data.size());
    conn.tx_seq = (conn.tx_seq + 1) & 0x0F;
    if (conn.tx_bs_remaining > 0) conn.tx_bs_remaining--;
    conn.tx_next_send_time =
        plugin->NowNs() +
        static_cast<std::uint64_t>(conn.tx_stmin_us) * 1000ULL;

    if (conn.tx_offset < conn.tx_buffer.size()) {
      if (conn.tx_bs_original > 0 && conn.tx_bs_remaining == 0) {
        // Block size reached — wait for next FC
        conn.tx_state = NsduConnection::TX_WAIT_FC;
        conn.tx_fc_deadline =
            plugin->NowNs() +
            static_cast<std::uint64_t>(conn.config.n_bs_ms) * 1'000'000ULL;
      }
    } else {
      conn.tx_state = NsduConnection::TX_IDLE;
      conn.tx_buffer.clear();
      conn.tx_offset = 0;
      conn.tx_seq = 0;
    }
  }
  return true;
}

// ── Plugin vtable callbacks ──────────────────────────────────────────────────

int tp_initialize(void* ctx, const char* config_json) {
  auto* plugin = static_cast<CanTpPlugin*>(ctx);
  if (plugin == nullptr) return -1;

  // Parse minimal config: {"iface":"vcan0"}
  if (config_json != nullptr) {
    const char* key = "\"iface\"";
    const char* pos = std::strstr(config_json, key);
    if (pos != nullptr) {
      pos += std::strlen(key);
      while (*pos && *pos != '"') ++pos;
      if (*pos == '"') {
        ++pos;
        const char* end = pos;
        while (*end && *end != '"') ++end;
        plugin->iface.assign(pos, end - pos);
      }
    }
  }
  if (plugin->iface.empty()) plugin->iface = "vcan0";
  plugin->service_name = "can_tp:" + plugin->iface;

  // No TX thread: pacing and the N_Bs/N_Cr watchdogs run on the host's tick
  // (see tp_on_tick), so CanTp shares the gateway's clock instead of running
  // its own.

  return 0;
}

/* Cap on CFs drained in a single tick.
   With STmin=0 the peer has asked for no separation at all, and the old TX
   thread streamed back-to-back until the buffer emptied. Draining inside the
   tick preserves that, but the tick authority runs every other phase after
   this one, so the burst has to be bounded or a large transfer would stall
   replay and the PDU router behind it. 256 CFs is ~1.8 KB of ISO-TP payload at
   DLC 8 -- above any realistic diagnostic exchange, and still a fixed bound. */
constexpr int kMaxCfBurstPerTick = 256;

void tp_on_tick(void* ctx, uint64_t /*tick*/) {
  auto* plugin = static_cast<CanTpPlugin*>(ctx);
  if (plugin == nullptr) return;
  // Keep servicing while CFs remain immediately due: STmin=0 means the next CF
  // is due the instant the previous one goes out, and one pass per tick would
  // otherwise pace an unlimited-rate transfer at the tick interval.
  for (int i = 0; i < kMaxCfBurstPerTick; ++i) {
    if (!can_tp_service_pending(plugin)) break;
  }
}

void tp_set_time_source(void* ctx, BoatNowNsFn fn, void* host_ctx) {
  auto* plugin = static_cast<CanTpPlugin*>(ctx);
  if (plugin == nullptr) return;
  std::lock_guard<std::mutex> lock(plugin->tx_mutex);
  plugin->now_ns_fn  = fn;
  plugin->now_ns_ctx = host_ctx;
}

void tp_shutdown(void* ctx) {
  auto* plugin = static_cast<CanTpPlugin*>(ctx);
  if (plugin == nullptr) return;

  // No thread to stop -- the host guarantees no concurrent callbacks after
  // shutdown() returns, and on_tick is one of them.
  std::lock_guard<std::mutex> lock(plugin->tx_mutex);
  plugin->connections.clear();
}

void tp_set_frame_publisher(void* ctx, BoatFramePublishFn fn, void* publisher_ctx) {
  auto* plugin = static_cast<CanTpPlugin*>(ctx);
  if (plugin == nullptr) return;
  plugin->frame_publish_fn    = fn;
  plugin->frame_publisher_ctx = publisher_ctx;
}

void tp_set_pdu_publisher(void* ctx, BoatPduPublishFn fn, void* publisher_ctx) {
  auto* plugin = static_cast<CanTpPlugin*>(ctx);
  if (plugin == nullptr) return;
  plugin->pdu_publish_fn    = fn;
  plugin->pdu_publisher_ctx = publisher_ctx;
}

// ── ISO 15765-2 receive path ─────────────────────────────────────────────────

void tp_on_frame(void* ctx, const BoatFrame* frame) {
  auto* plugin = static_cast<CanTpPlugin*>(ctx);
  if (plugin == nullptr || frame == nullptr || frame->payload_len < 1) return;

  if (frame->bus_type == BOAT_BUS_PDU) {
    // Generic PDU-bus dispatch: segment-and-send if this frame's pdu_id
    // matches a configured connection's nsdu_id -- symmetric with the RX
    // side, which already publishes reassembled I-PDUs the same way
    // (pf.pdu_id = conn->nsdu_id, below). This is a second way to trigger a
    // send alongside CanTpService.Send (both end up calling can_tp_send()).
    //
    // Require iface to be set and match this instance -- NOT the "empty
    // iface = accept from anyone" rule the CAN-bus path below uses. Reason:
    // this plugin's own RX-reassembly-complete path (pdu_publish_fn, below)
    // republishes onto the very same PDU bus via DispatchFrame, with no
    // iface set (main.cpp's PduPublisher wiring never sets one) and
    // pdu_id == this connection's own nsdu_id -- if empty iface were
    // accepted here, that internal echo would loop straight back into
    // can_tp_send() and re-transmit the payload we just finished receiving.
    // Requiring a real, matching iface closes that off entirely (an
    // external caller must explicitly set --iface to target an instance,
    // e.g. `boat frame send --bus-type pdu --iface vcan0 ...`), at the cost
    // of also requiring nsdu_id to be unique across every CanTp instance
    // sharing a PDU-bus namespace for callers that rely on iface alone.
    if (frame->iface == nullptr || frame->iface[0] == '\0' || frame->iface != plugin->iface) return;
    can_tp_send(plugin, frame->meta.pdu.pdu_id, frame->payload,
               static_cast<uint32_t>(frame->payload_len));
    return;
  }

  // Only handle CAN and CAN-FD frames
  if (frame->bus_type != BOAT_BUS_CAN && frame->bus_type != BOAT_BUS_CANFD) return;

  if (frame->iface != nullptr && frame->iface != plugin->iface) return;

  // ── Self-sent filter ─────────────────────────────────────────────────────
  // Internally-dispatched frames carry BOAT_CAN_FLAG_SELF_SENT, set by the
  // CanBusRegistry when a plugin sends a frame.  Drop them immediately —
  // they are our own sends looped back via DispatchRx, not peer frames.
  if (frame->meta.can.flags & BOAT_CAN_FLAG_SELF_SENT) return;

  const uint8_t* data = frame->payload;
  const size_t  dlc  = frame->payload_len;

  // Single critical section covering the connection lookup and every
  // SF/FF/CF/FC state mutation below. Required so a concurrent Remove()
  // (which erases the NsduConnection from the map) can never run while this
  // function is still holding/using the pointer find_by_target() returns --
  // std::unordered_map only invalidates references/pointers on erase, so
  // serializing against Remove() via this same mutex is sufficient (insert/
  // rehash from a concurrent Configure() on a *different* nsdu_id does not
  // invalidate this connection's pointer). Held across the frame_publish_fn/
  // pdu_publish_fn calls too -- both can synchronously re-enter this same
  // on_frame() via self-echo (CAN self-sent tagging / the PDU-bus loopback
  // guard above), and both of those re-entry paths return before ever
  // touching tx_mutex, so this can't self-deadlock.
  std::lock_guard<std::mutex> lock(plugin->tx_mutex);

  // ── Find connection by target_addr ───────────────────────────────────────
  // Only frames from the peer (on target_addr) are processed.
  NsduConnection* conn = find_by_target(plugin, frame->meta.can.can_id, data, dlc);
  if (conn == nullptr) return;  // unknown — silently drop

  const bool is_fd = (conn->config.can_dlc > 8);

  // The PCI byte's position depends on this connection's addressing mode --
  // under extended addressing, data[0] is the target-address-extension
  // byte, not the PCI byte (which then falls at data[1]). This has to wait
  // until conn is known: addressing is per-connection config, not
  // derivable from the frame alone. Bounds-check first -- a malformed/
  // truncated frame under extended addressing could be too short to even
  // contain a PCI byte at data[1].
  const size_t pci_idx = conn->config.extended_addressing ? 1 : 0;
  if (dlc <= pci_idx) return;
  const uint8_t pci_byte = data[pci_idx];
  const uint8_t pci_type = pci_byte & kPciMask;

  if (pci_type == kPciFc) {
    // ── Flow Control from peer ─────────────────────────────────────────────
    // At least PCI + BS + STmin must be present. The old code read data[1..3]
    // without checking dlc, so a one- or two-byte frame could read arbitrary
    // bytes (or past the end of the payload vector).
    if (dlc < pci_idx + 3) return;
    if (conn->tx_state != NsduConnection::TX_WAIT_FC) return;

    const uint8_t fc_flags = pci_byte & 0x0F;
    if (fc_flags != kFcContinue && fc_flags != kFcWait &&
        fc_flags != kFcOverflow) {
      conn->tx_state = NsduConnection::TX_IDLE;
      conn->tx_buffer.clear();
      conn->tx_offset = 0;
      plugin->NotifyError(conn->nsdu_id, CANTP_N_INVALID_FS,
          "peer sent invalid Flow Status " + std::to_string(fc_flags));
      return;
    }
    if (fc_flags == kFcOverflow) {
      conn->tx_state = NsduConnection::TX_IDLE;
      conn->tx_buffer.clear();
      conn->tx_offset = 0;
      conn->tx_wait_frames = 0;
      plugin->NotifyError(conn->nsdu_id, CANTP_N_BUFFER_OVFLW,
          "peer sent FC(Overflow) -- aborting transfer");
      return;
    }
    if (fc_flags == kFcWait) {
      // FC(WT) extends N_Bs, but only up to N_WFTmax consecutive times.
      // Without the cap an alive-but-stalling peer can hold the session
      // open forever.
      if (conn->tx_wait_frames >= kMaxConsecutiveWaitFrames) {
        conn->tx_state = NsduConnection::TX_IDLE;
        conn->tx_buffer.clear();
        conn->tx_offset = 0;
        conn->tx_wait_frames = 0;
        plugin->NotifyError(conn->nsdu_id, CANTP_N_WFT_OVRN,
            "N_WFTmax exceeded (" +
            std::to_string(kMaxConsecutiveWaitFrames) +
            " consecutive FC(Wait) frames)");
        return;
      }
      ++conn->tx_wait_frames;
      conn->tx_fc_deadline =
          plugin->NowNs() +
          static_cast<std::uint64_t>(conn->config.n_bs_ms) * 1'000'000ULL;
      return;
    }
    // Continue
    const uint8_t bs    = data[pci_idx + 1];
    const uint8_t stmin = data[pci_idx + 2];
    conn->tx_bs_remaining = bs;
    conn->tx_bs_original  = bs;
    conn->tx_stmin_us     = stmin_to_us(stmin);
    conn->tx_state        = NsduConnection::TX_SEND_CF;
    conn->tx_wait_frames  = 0;
    conn->tx_next_send_time = plugin->NowNs();
    return;
  }

  // ── RX path: SF / FF / CF on target_addr ────────────────────────────────

  if (pci_type == kPciSf) {
    // Single Frame. CAN FD (is_fd) peers may use the 2-PCI-byte escape
    // format (low nibble 0, SF_DL in the next byte) for SF_DL > 7 -- see
    // the TX-side comment in can_tp_send() for the full format. A nibble of
    // 0 only means "escape" when is_fd; on classic CAN it's SF_DL==0 (an
    // empty SF), which existing behavior already handles via actual_len==0
    // below, so this doesn't change classic-CAN decoding at all.
    const uint8_t sf_len_nibble = pci_byte & 0x0F;
    const bool escaped = is_fd && sf_len_nibble == 0 && dlc > pci_idx + 1u;
    const size_t offset = pci_idx + (escaped ? 2 : 1);
    const size_t sf_len = escaped ? data[pci_idx + 1] : sf_len_nibble;
    const size_t payload_len = dlc > offset ? dlc - offset : 0;
    const size_t actual_len = std::min(sf_len, payload_len);
    const uint32_t nsdu_id = conn->nsdu_id;

    plugin->NotifySubscribers(nsdu_id, std::vector<uint8_t>(data + offset, data + offset + actual_len));

    if (plugin->pdu_publish_fn == nullptr) return;
    BoatPduFrame pf{};
    pf.pdu_id      = nsdu_id;
    pf.payload     = const_cast<uint8_t*>(data + offset);
    pf.payload_len = actual_len;
    pf.iface       = plugin->iface.c_str();
    plugin->pdu_publish_fn(plugin->pdu_publisher_ctx, &pf);
    return;
  }

  if (pci_type == kPciFf) {
    // First Frame. FF_DL's low byte is the byte right after the PCI byte
    // (data[pci_idx + 1]), not a hardcoded data[1] -- under extended
    // addressing pci_idx is 1, so this is data[2].
    if (dlc <= pci_idx + 1) return;  // too short to hold FF_DL's low byte
    const uint32_t ff_len = ((static_cast<uint32_t>(pci_byte & 0x0F)) << 8) |
                             static_cast<uint32_t>(data[pci_idx + 1]);

    // ISO 15765-2:2016 §9.6.3.2 Table 14: FF_DL < 8 is invalid -- a
    // compliant sender uses SF for payloads that small. Drop rather than
    // reject with an error frame, matching this function's existing
    // precedent for other malformed input (unknown connection, sequence
    // error).
    if (ff_len < 8) return;

    const size_t offset = conn->config.extended_addressing ? 3 : 2;
    const size_t first_chunk = dlc > offset ? dlc - offset : 0;

    if (ff_len > conn->config.rx_buffer_size) {
      // ── Overflow: send FC with Overflow status ──────────────────────────
      conn->rx_state = NsduConnection::RX_IDLE;
      plugin->NotifyError(conn->nsdu_id, CANTP_N_BUFFER_OVFLW,
          "FF_DL (" + std::to_string(ff_len) + ") exceeds rx_buffer_size (" +
          std::to_string(conn->config.rx_buffer_size) + ")");
      if (plugin->frame_publish_fn == nullptr) return;

      uint8_t fc_buf[64];
      uint8_t idx = 0;
      if (conn->config.extended_addressing) {
        // Extended/mixed addressing: the byte is the address of the
        // frame's intended recipient, i.e. the peer's N_TA/N_AE -- matches
        // the pattern used for outgoing SF/FF/CF in can_tp_send().
        fc_buf[idx++] = conn->config.address_byte;
      }
      fc_buf[idx++] = kPciFc | kFcOverflow;
      fc_buf[idx++] = 0;  // BS (don't care for overflow)
      fc_buf[idx++] = 0;  // STmin (don't care for overflow)
      const uint8_t fc_dlc = pad_frame(fc_buf, idx, conn->config.can_dlc, conn->config.pad_byte);

      {
        auto fc = BoatFrameOwner::Can(
            plugin->iface, conn->source_addr,
            fc_dlc,
            can_flags(is_fd, conn->config.brs),
            std::vector<uint8_t>(fc_buf, fc_buf + fc_dlc), is_fd);
        plugin->frame_publish_fn(plugin->frame_publisher_ctx, fc.get());
      }
      return;
    }

    // Normal FF processing
    conn->rx_buffer.clear();
    conn->rx_buffer.reserve(ff_len);
    conn->rx_buffer.assign(data + offset, data + offset + first_chunk);
    conn->rx_expected_len = ff_len;
    conn->rx_next_seq = 1;
    conn->rx_cf_count = 0;
    conn->rx_state = NsduConnection::RX_WAIT_CF;
    conn->rx_cf_deadline =
        plugin->NowNs() +
        static_cast<std::uint64_t>(conn->config.n_cr_ms) * 1'000'000ULL;
    // Required, not just for consistency: this is a *new* deadline where
    // none existed before, possibly earlier than whatever the TX thread is
    // currently sleeping until (or it may be sleeping indefinitely, with
    // nothing else pending) -- without this it wouldn't wake to notice
    // N_Cr until some unrelated event happened to notify it.

    // Send Flow Control (Continue) with configured BS and STmin
    if (plugin->frame_publish_fn == nullptr) return;

    uint8_t fc_buf[64];
    uint8_t idx = 0;
    if (conn->config.extended_addressing) {
      fc_buf[idx++] = conn->config.address_byte;
    }
    fc_buf[idx++] = kPciFc | kFcContinue;
    fc_buf[idx++] = conn->config.block_size;  // BS (0 = unlimited)
    fc_buf[idx++] = conn->config.st_min;      // STmin
    const uint8_t fc_dlc = pad_frame(fc_buf, idx, conn->config.can_dlc, conn->config.pad_byte);

    {
      auto fc = BoatFrameOwner::Can(
          plugin->iface, conn->source_addr,
          fc_dlc,
          can_flags(is_fd, conn->config.brs),
          std::vector<uint8_t>(fc_buf, fc_buf + fc_dlc), is_fd);
      plugin->frame_publish_fn(plugin->frame_publisher_ctx, fc.get());
    }
    return;
  }

  if (pci_type == kPciCf) {
    // Consecutive Frame
    if (conn->rx_state != NsduConnection::RX_WAIT_CF) return;
    const uint8_t seq = pci_byte & 0x0F;
    if (seq != conn->rx_next_seq) {
      conn->rx_state = NsduConnection::RX_IDLE;
      plugin->NotifyError(conn->nsdu_id, CANTP_N_WRONG_SN,
          "expected CF seq " + std::to_string(conn->rx_next_seq) + ", got " +
          std::to_string(seq));
      return;  // sequence error
    }
    const size_t offset = conn->config.extended_addressing ? 2 : 1;
    const size_t chunk = dlc > offset ? dlc - offset : 0;
    conn->rx_buffer.insert(conn->rx_buffer.end(), data + offset, data + offset + chunk);

    if (conn->rx_buffer.size() >= conn->rx_expected_len) {
      conn->rx_buffer.resize(conn->rx_expected_len);
      plugin->NotifySubscribers(conn->nsdu_id, conn->rx_buffer);
      if (plugin->pdu_publish_fn == nullptr) return;
      BoatPduFrame pf{};
      pf.pdu_id      = conn->nsdu_id;
      pf.payload     = conn->rx_buffer.data();
      pf.payload_len = conn->rx_buffer.size();
      pf.iface       = plugin->iface.c_str();
      plugin->pdu_publish_fn(plugin->pdu_publisher_ctx, &pf);
      conn->rx_state = NsduConnection::RX_IDLE;
    } else {
      conn->rx_next_seq = (seq + 1) & 0x0F;
      // Each accepted CF restarts N_Cr -- it's the deadline for the *next*
      // CF, not a one-shot timer for the whole reassembly. Only pushes the
      // deadline later (never earlier), so notifying isn't strictly
      // required, but see the FC(WT) comment above for why it's done
      // anyway.
      conn->rx_cf_deadline =
          plugin->NowNs() +
          static_cast<std::uint64_t>(conn->config.n_cr_ms) * 1'000'000ULL;
      // Re-FC: if BS > 0 and we've received a full block, send another FC
      ++conn->rx_cf_count;
      if (conn->config.block_size > 0 &&
          (conn->rx_cf_count % conn->config.block_size) == 0) {
        if (plugin->frame_publish_fn == nullptr) return;

        uint8_t fc_buf[64];
        uint8_t fcidx = 0;
        if (conn->config.extended_addressing) {
          fc_buf[fcidx++] = conn->config.address_byte;
        }
        fc_buf[fcidx++] = kPciFc | kFcContinue;
        fc_buf[fcidx++] = conn->config.block_size;
        fc_buf[fcidx++] = conn->config.st_min;
        const uint8_t fc_dlc = pad_frame(fc_buf, fcidx, conn->config.can_dlc, conn->config.pad_byte);

        auto fc = BoatFrameOwner::Can(
            plugin->iface, conn->source_addr,
            fc_dlc,
            can_flags(is_fd, conn->config.brs),
            std::vector<uint8_t>(fc_buf, fc_buf + fc_dlc), is_fd);
        plugin->frame_publish_fn(plugin->frame_publisher_ctx, fc.get());
      }
    }
    return;
  }
}

const char* can_tp_declared_buses(void* /*ctx*/) {
  return "[\"can\",\"pdu\"]";
}

}  // anonymous namespace

// ── Standalone CanTp API ─────────────────────────────────────────────────────

int32_t can_tp_configure(void* tp_ctx, const CanTpConfig* config) {
  auto* plugin = static_cast<CanTpPlugin*>(tp_ctx);
  if (plugin == nullptr || config == nullptr) return -1;

  // source_addr/target_addr must both be explicit and non-zero -- no
  // implicit "0 = use nsdu_id" fallback. A single-ID session (one CAN ID
  // used for both directions) is still supported, just by passing that same
  // address for both explicitly, so the addressing is always visible in the
  // config rather than inferred.
  if (config->source_addr == 0 || config->target_addr == 0) return -1;

  NsduConnection conn;
  conn.nsdu_id     = config->nsdu_id;
  conn.config      = *config;
  // Resolve the 0-sentinel to the ISO default once here, so every later
  // read of conn.config.n_bs_ms/n_cr_ms (deadline-setting in can_tp_send()
  // and tp_on_frame(), the watchdog check in can_tp_service_pending()) can
  // use the value directly without re-checking for 0.
  conn.config.n_bs_ms = resolve_timeout_ms(config->n_bs_ms);
  conn.config.n_cr_ms = resolve_timeout_ms(config->n_cr_ms);
  conn.config.pad_byte = resolve_pad_byte(config->pad_byte);
  // addressing_mode is the authoritative field; the legacy extended_addressing
  // bool only takes effect when addressing_mode was left at NORMAL (0), for
  // configs written before addressing_mode existed. Either way, normalize
  // extended_addressing itself down to "does this connection have an
  // address byte" (true for EXTENDED *and* MIXED, which are wire-identical)
  // so every existing `conn->config.extended_addressing ? ... : ...` call
  // site in this file keeps doing the right thing without having to touch
  // each one individually.
  auto resolved_mode = static_cast<CanTpAddressingMode>(config->addressing_mode);
  if (resolved_mode == CANTP_ADDR_NORMAL && config->extended_addressing) {
    resolved_mode = CANTP_ADDR_EXTENDED;
  }
  conn.config.addressing_mode = resolved_mode;
  conn.config.extended_addressing = (resolved_mode != CANTP_ADDR_NORMAL);
  conn.config.address_byte = config->address_byte != 0
      ? config->address_byte
      : static_cast<uint8_t>(config->target_addr & 0xFF);
  conn.rx_state    = NsduConnection::RX_IDLE;
  conn.tx_state    = NsduConnection::TX_IDLE;
  conn.source_addr = config->source_addr;
  conn.target_addr = config->target_addr;

  std::lock_guard<std::mutex> lock(plugin->tx_mutex);
  // Keyed by nsdu_id -- the caller-facing session identifier -- not
  // source_addr, so send/remove/subscribe by nsdu_id are unambiguous.
  // Re-configuring an already-configured nsdu_id edits it in place -- but
  // only when it's safe to discard whatever state it currently holds.

  auto it = plugin->connections.find(conn.nsdu_id);
  if (it != plugin->connections.end()) {
    // AUTOSAR CanTp123: a TX channel in CANTP_TX_PROCESSING shall reject
    // new requests with E_NOT_OK rather than silently discard in-flight
    // data. Extended here to RX for the same reason -- overwriting mid-
    // reassembly loses just as much data as overwriting mid-send. Matches
    // can_tp_remove()'s existing tx_state check.
    const bool tx_busy = it->second.tx_state != NsduConnection::TX_IDLE &&
                         it->second.tx_state != NsduConnection::TX_COMPLETE;
    const bool rx_busy = it->second.rx_state == NsduConnection::RX_WAIT_CF;
    if (tx_busy || rx_busy) return -2;  // busy
  }

  // find_by_target() can disambiguate two connections sharing a target_addr
  // *only* when both have an address byte (EXTENDED/MIXED) and those bytes
  // are distinct -- that's the whole point of those addressing modes
  // (multiplexing several logical peers onto one physical CAN ID). Any
  // other kind of sharing (either side NORMAL, or matching address bytes)
  // is a genuine, unresolvable routing ambiguity: reject it rather than
  // silently leaving one connection unreachable (AUTOSAR CanTp096). A
  // connection re-configuring itself with the target_addr it already owns
  // is not a conflict with itself.
  for (const auto& [id, existing] : plugin->connections) {
    if (id == conn.nsdu_id || existing.target_addr != conn.target_addr) continue;
    const bool both_addressed = existing.config.extended_addressing &&
                                conn.config.extended_addressing;
    if (!both_addressed || existing.config.address_byte == conn.config.address_byte) {
      return -3;  // ambiguous target_addr -- see can_tp_configure()'s doc comment
    }
  }

  plugin->connections[conn.nsdu_id] = conn;
  return 0;
}

int32_t can_tp_remove(void* tp_ctx, uint32_t nsdu_id) {
  auto* plugin = static_cast<CanTpPlugin*>(tp_ctx);
  if (plugin == nullptr) return -1;

  std::lock_guard<std::mutex> lock(plugin->tx_mutex);
  auto it = plugin->connections.find(nsdu_id);
  if (it == plugin->connections.end()) return -1;  // not configured

  // Refuse to erase a connection the TX pacing thread may still be actively
  // working with (holds a raw NsduConnection* obtained under this same lock,
  // used across several re-locks while streaming CFs -- see
  // can_tp_service_pending). Only IDLE/COMPLETE are safe to erase.
  if (it->second.tx_state != NsduConnection::TX_IDLE &&
      it->second.tx_state != NsduConnection::TX_COMPLETE) {
    return -2;  // busy
  }

  plugin->connections.erase(it);
  return 0;
}

int32_t can_tp_send(void* tp_ctx, uint32_t nsdu_id,
                    const uint8_t* data, uint32_t len) {
  auto* plugin = static_cast<CanTpPlugin*>(tp_ctx);
  if (plugin == nullptr || data == nullptr) return -1;
  if (plugin->frame_publish_fn == nullptr) return -1;

  if (len == 0) return -1;

  // FF_DL is 12 bits (ISO 15765-2 §9.6.3). The 32-bit escape format (FF_DL=0
  // followed by a 4-byte length) is not implemented, so a longer payload would
  // announce a wrapped length and then transmit every byte anyway -- a silent
  // corruption the receiver cannot detect. Fail instead.
  if (len > 4095) return -1;

  uint8_t frame_buf[64];
  uint8_t frame_dlc = 0;
  uint32_t source_addr = 0;
  bool is_fd = false;
  bool brs = false;
  bool single_frame = false;
  bool started = false;

  // Build the first (and for a SF, only) frame and reserve the multi-frame
  // state in one locked section. The old code published the FF before
  // entering TX_WAIT_FC: a fast peer's FC could arrive first and be dropped,
  // and two concurrent Send calls could both pass the idle check and emit
  // two FFs. Reserving before publishing makes those races impossible.
  {
    std::lock_guard<std::mutex> lock(plugin->tx_mutex);
    auto it = plugin->connections.find(nsdu_id);
    if (it == plugin->connections.end()) return -1;
    auto& conn = it->second;
    if (conn.tx_state != NsduConnection::TX_IDLE) return -1;  // busy

    const uint8_t dlc = conn.config.can_dlc;
    const bool extended_addressing = conn.config.extended_addressing;
    const uint8_t pad_byte = conn.config.pad_byte;
    const uint8_t address_byte = conn.config.address_byte;
    const uint32_t max_payload = dlc;
    is_fd = dlc > 8;
    brs = conn.config.brs;
    source_addr = conn.source_addr;

    const uint32_t sf_pci_overhead = is_fd ? 2 : 1;
    const uint32_t addr_overhead = extended_addressing ? 1 : 0;
    if (max_payload <= sf_pci_overhead + addr_overhead) return -1;
    const uint32_t sf_max_len = max_payload - sf_pci_overhead - addr_overhead;

    if (len <= sf_max_len) {
      // Single Frame — no state machine needed.
      uint8_t idx = 0;
      if (extended_addressing) frame_buf[idx++] = address_byte;
      if (is_fd) {
        frame_buf[idx++] = kPciSf;                    // escape: low nibble 0
        frame_buf[idx++] = static_cast<uint8_t>(len);  // SF_DL as a full byte
      } else {
        frame_buf[idx++] = kPciSf | static_cast<uint8_t>(len);
      }
      std::memcpy(frame_buf + idx, data, len);
      frame_dlc = pad_frame(frame_buf, static_cast<uint8_t>(idx + len), dlc,
                            pad_byte);
      single_frame = true;
    } else {
      // Multi-frame: build the FF and reserve TX state under the same lock.
      uint8_t idx = 0;
      if (extended_addressing) frame_buf[idx++] = address_byte;
      frame_buf[idx++] = kPciFf | static_cast<uint8_t>((len >> 8) & 0x0F);
      frame_buf[idx++] = static_cast<uint8_t>(len & 0xFF);
      const uint32_t ff_overhead = extended_addressing ? 3 : 2;
      if (max_payload <= ff_overhead) return -1;
      const uint32_t ff_payload = std::min(len, max_payload - ff_overhead);
      std::memcpy(frame_buf + idx, data, ff_payload);
      frame_dlc = pad_frame(frame_buf, static_cast<uint8_t>(idx + ff_payload),
                            dlc, pad_byte);

      conn.tx_buffer.assign(data, data + len);
      conn.tx_offset = ff_payload;
      conn.tx_seq = 1;
      conn.tx_bs_remaining = 0;   // will be set when FC arrives
      conn.tx_bs_original = 0;
      conn.tx_stmin_us = 0;
      conn.tx_wait_frames = 0;
      conn.tx_state = NsduConnection::TX_WAIT_FC;
      conn.tx_next_send_time = plugin->NowNs();
      conn.tx_fc_deadline =
          plugin->NowNs() +
          static_cast<std::uint64_t>(conn.config.n_bs_ms) * 1'000'000ULL;
      started = true;
    }
  }

  auto frame = BoatFrameOwner::Can(
      plugin->iface, source_addr, frame_dlc, can_flags(is_fd, brs),
      std::vector<uint8_t>(frame_buf, frame_buf + frame_dlc), is_fd);
  plugin->frame_publish_fn(plugin->frame_publisher_ctx, frame.get());
  if (!single_frame && !started) return -1;
  return single_frame ? 1 : 0;
}

// ── Standard BoatPlugin entry points ─────────────────────────────────────────

extern "C" BoatPlugin* boat_plugin_create() {
  static BoatPluginVTable kVTable = [] {
    BoatPluginVTable vt{};
    vt.initialize          = &tp_initialize;
    vt.on_tick             = &tp_on_tick;
    vt.shutdown            = &tp_shutdown;
    vt.set_publisher       = nullptr;
    vt.set_bus_publisher   = nullptr;
    vt.set_pdu_publisher   = &tp_set_pdu_publisher;
    vt.on_frame            = &tp_on_frame;
    vt.set_frame_publisher = &tp_set_frame_publisher;
    vt.declared_buses      = &can_tp_declared_buses;
    vt.set_time_source     = &tp_set_time_source;
    return vt;
  }();

  auto* state  = new CanTpPlugin{};
  auto* plugin = new BoatPlugin{};
  plugin->vtable = &kVTable;
  plugin->ctx    = state;
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (plugin == nullptr) return;
  if (plugin->vtable != nullptr && plugin->vtable->shutdown != nullptr) {
    plugin->vtable->shutdown(plugin->ctx);
  }
  delete static_cast<CanTpPlugin*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }

// Exposes this plugin instance as a "can_tp:<iface>" service so
// PluginManager::Load() registers it and CanTpServiceImpl can find it via
// FindService("can_tp:" + iface) -- see boat/plugin.h's service-export docs
// and pdu_router_plugin.cpp for the base pattern this mirrors. Iface-scoped
// (rather than a single fixed "can_tp") so multiple loaded instances, one
// per CAN interface, register distinct, independently-addressable names.
extern "C" const char* boat_plugin_service_name(void* ctx) {
  auto* p = static_cast<CanTpPlugin*>(ctx);
  return (p != nullptr) ? p->service_name.c_str() : nullptr;
}

extern "C" void* boat_plugin_service_ptr(void* ctx) {
  auto* p = static_cast<CanTpPlugin*>(ctx);
  if (p == nullptr) return nullptr;
  return static_cast<boat::core::ICanTp*>(p);
}

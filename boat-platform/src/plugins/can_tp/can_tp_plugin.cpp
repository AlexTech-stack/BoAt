#include "can_tp_plugin.h"

#include <cstring>
#include <sstream>

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

constexpr uint8_t kPadByte = 0x55;  // fill byte for unused trailing data bytes

// ISO 15765-2 default for both N_Bs (TX waiting for FC) and N_Cr (RX waiting
// for the next CF) -- see CanTpConfig::n_bs_ms/n_cr_ms in boat/can_tp.h.
constexpr uint32_t kDefaultTimeoutMs = 1000;

// Resolve a 0-sentinel (== "use the ISO default") to an actual timeout.
uint32_t resolve_timeout_ms(uint32_t configured) {
  return configured != 0 ? configured : kDefaultTimeoutMs;
}

// Every CanTp-emitted frame (SF/FF/CF/FC) is sent at the connection's fixed
// can_dlc, not the length actually in use -- unused trailing bytes are
// filled with kPadByte. `used` is the number of meaningful bytes already
// written to buf (PCI/addressing + payload); pads buf[used..dlc) and
// returns dlc, the DLC/payload_len to publish the frame with.
uint8_t pad_frame(uint8_t* buf, uint8_t used, uint8_t dlc) {
  for (uint8_t i = used; i < dlc; ++i) buf[i] = kPadByte;
  return dlc;
}

// ── STmin helpers ──────────────────────────────────────────────────────────

// Convert ISO 15765-2 STmin byte to microseconds.
//   0x00–0x7F : directly in ms (0–127 ms)
//   0xF1–0xF9 : 100–900 μs (steps of 100 μs)
//   0x81–0xF0 : reserved, treated as 0
uint32_t stmin_to_us(uint8_t stmin) {
  if (stmin <= 0x7F) return static_cast<uint32_t>(stmin) * 1000;
  if (stmin >= 0xF1 && stmin <= 0xF9)
    return static_cast<uint32_t>(stmin - 0xF0) * 100;
  return 0;
}

// ── Connection lookup helpers ─────────────────────────────────────────────

NsduConnection* find_by_target(CanTpPlugin* plugin, uint32_t can_id) {
  for (auto& [id, conn] : plugin->connections) {
    if (conn.target_addr == can_id) return &conn;
  }
  return nullptr;
}

// ── TX thread ──────────────────────────────────────────────────────────────

void can_tp_tx_thread_func(CanTpPlugin* plugin) {
  using namespace std::chrono;

  while (!plugin->tx_stop.load()) {
    // Collect connections that need TX processing. Each entry captures
    // everything the send-CF phase below needs in one locked pass, instead
    // of that phase re-locking three separate times per CF to read tx_seq/
    // tx_buffer, then again to update state -- keeping the scan and the
    // "do we owe this connection a CF" decision in the single section below
    // means the data can't change out from under the unlocked send.
    struct TxWork {
      NsduConnection* conn;
      uint32_t source_addr;
      uint8_t seq;
      uint32_t chunk;
    };
    std::vector<TxWork> to_send_cf;
    // Earliest of: next CF's STmin pacing time, any TX_WAIT_FC's N_Bs
    // deadline, any RX_WAIT_CF's N_Cr deadline. Drives how long to sleep
    // below -- steady_clock::time_point::max() means "nothing pending,
    // sleep until notified".
    steady_clock::time_point next_wake = steady_clock::time_point::max();

    {
      std::lock_guard<std::mutex> lock(plugin->tx_mutex);
      auto now = steady_clock::now();
      for (auto& [addr, conn] : plugin->connections) {
        if (conn.tx_state == NsduConnection::TX_SEND_CF) {
          if (now >= conn.tx_next_send_time) {
            const uint32_t max_payload = conn.config.can_dlc;
            const uint32_t cf_overhead = conn.config.extended_addressing ? 2 : 1;
            to_send_cf.push_back({
                &conn, addr, conn.tx_seq,
                static_cast<uint32_t>(std::min(
                    conn.tx_buffer.size() - conn.tx_offset,
                    static_cast<size_t>(max_payload - cf_overhead)))});
          } else {
            next_wake = std::min(next_wake, conn.tx_next_send_time);
          }
        }

        // ── N_Bs watchdog: TX gave up waiting for FC ──────────────────────
        // Fires whether we're waiting for the *first* FC after FF, or the
        // next FC at a block boundary (both go through TX_WAIT_FC) -- ISO
        // 15765-2 §9.8 uses N_Bs for both cases. Reset directly here rather
        // than deferring like to_send_cf: it's just clearing local state,
        // nothing to publish, so there's no reason to release the lock
        // first.
        if (conn.tx_state == NsduConnection::TX_WAIT_FC) {
          if (now >= conn.tx_fc_deadline) {
            conn.tx_state = NsduConnection::TX_IDLE;
            conn.tx_buffer.clear();
            conn.tx_offset = 0;
            conn.tx_seq = 0;
          } else {
            next_wake = std::min(next_wake, conn.tx_fc_deadline);
          }
        }

        // ── N_Cr watchdog: RX gave up waiting for the next CF ─────────────
        if (conn.rx_state == NsduConnection::RX_WAIT_CF) {
          if (now >= conn.rx_cf_deadline) {
            conn.rx_state = NsduConnection::RX_IDLE;
            conn.rx_buffer.clear();
          } else {
            next_wake = std::min(next_wake, conn.rx_cf_deadline);
          }
        }
      }
    }

    if (to_send_cf.empty()) {
      // Nothing was immediately due -- next_wake (computed above) reflects
      // the true state and it's safe to sleep on it. No predicate on either
      // wait: can_tp_send() and tp_on_frame() call tx_cv.notify_one()
      // whenever they create or move up a deadline (new TX_WAIT_FC, an FC
      // unblocking one, a new/refreshed RX_WAIT_CF) -- any wake, spurious
      // or real, just loops back around to rescan, which is always safe
      // and cheap. This replaces the old fixed 500µs poll, which woke and
      // rescanned every connection ~2000×/sec even when nothing was
      // pending.
      std::unique_lock<std::mutex> wait_lock(plugin->tx_mutex);
      if (plugin->tx_stop.load()) break;
      if (next_wake == steady_clock::time_point::max()) {
        plugin->tx_cv.wait(wait_lock);
      } else {
        plugin->tx_cv.wait_until(wait_lock, next_wake);
      }
      continue;
    }

    // Send CFs without holding the lock
    for (auto& work : to_send_cf) {
      auto* conn = work.conn;
      auto addr = work.source_addr;
      if (conn->tx_state != NsduConnection::TX_SEND_CF) continue;

      const uint8_t dlc = conn->config.can_dlc;
      const bool is_fd = (conn->config.can_dlc > 8);

      // Build and send one CF, using the seq/chunk captured under the lock
      // above -- both are read-only snapshots of state that's about to be
      // advanced (below) once this CF actually goes out.
      uint8_t cf_buf[64];
      uint8_t idx = 0;
      if (conn->config.extended_addressing) {
        cf_buf[idx++] = static_cast<uint8_t>(conn->target_addr & 0xFF);
      }
      cf_buf[idx++] = kPciCf | (work.seq & 0x0F);
      {
        std::lock_guard<std::mutex> lock(plugin->tx_mutex);
        std::memcpy(cf_buf + idx, conn->tx_buffer.data() + conn->tx_offset,
                    work.chunk);
      }
      const uint8_t cf_dlc = pad_frame(cf_buf, static_cast<uint8_t>(idx + work.chunk), dlc);

      auto cf = BoatFrameOwner::Can(
          plugin->iface, conn->source_addr,
          cf_dlc, static_cast<uint8_t>(is_fd ? 0x04 : 0),
          std::vector<uint8_t>(cf_buf, cf_buf + cf_dlc), is_fd);
      plugin->frame_publish_fn(plugin->frame_publisher_ctx, cf.get());

      {
        std::lock_guard<std::mutex> lock(plugin->tx_mutex);
        conn->tx_offset += work.chunk;
        conn->tx_seq = (conn->tx_seq + 1) & 0x0F;
        if (conn->tx_bs_remaining > 0) conn->tx_bs_remaining--;
        conn->tx_next_send_time = steady_clock::now() +
                                  microseconds(conn->tx_stmin_us);

        if (conn->tx_offset < conn->tx_buffer.size()) {
          if (conn->tx_bs_original > 0 && conn->tx_bs_remaining == 0) {
            // Block size reached — wait for next FC
            conn->tx_state = NsduConnection::TX_WAIT_FC;
            conn->tx_fc_deadline = steady_clock::now() +
                                   milliseconds(conn->config.n_bs_ms);
          }
          // else: BS=0 (unlimited) — keep sending CFs without waiting
        } else {
          // All data sent
          conn->tx_state = NsduConnection::TX_IDLE;
          conn->tx_buffer.clear();
          conn->tx_offset = 0;
          conn->tx_seq = 0;
        }
      }
    }
    // Loop back around immediately (no sleep) rather than trusting
    // next_wake here -- it was computed *before* the sends above updated
    // tx_next_send_time (STmin pacing) or tx_fc_deadline (block-boundary
    // N_Bs) for the connections just serviced, so it can't be trusted for
    // them. The next iteration's scan reads the fresh values instead. For
    // STmin=0 (unlimited) streaming this means back-to-back scan+send with
    // no sleep, which is correct -- that's what STmin=0 means.
  }
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

  // Start the TX pacing thread
  plugin->tx_stop.store(false);
  plugin->tx_thread = std::thread(can_tp_tx_thread_func, plugin);

  return 0;
}

void tp_on_tick(void* /*ctx*/, uint64_t /*tick*/) {}

void tp_shutdown(void* ctx) {
  auto* plugin = static_cast<CanTpPlugin*>(ctx);
  if (plugin == nullptr) return;

  // Stop the TX thread
  plugin->tx_stop.store(true);
  plugin->tx_cv.notify_all();
  if (plugin->tx_thread.joinable()) {
    plugin->tx_thread.join();
  }

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

  const uint8_t pci_byte = frame->payload[0];
  const uint8_t pci_type = pci_byte & kPciMask;
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
  NsduConnection* conn = find_by_target(plugin, frame->meta.can.can_id);
  if (conn == nullptr) return;  // unknown — silently drop

  const bool is_fd = (conn->config.can_dlc > 8);

  if (pci_type == kPciFc) {
    // ── Flow Control from peer ─────────────────────────────────────────────
    // Data[0] = PCI (0x30 | flags)
    // Data[1] = BS (Block Size)
    // Data[2] = STmin (Separation Time)
    if (conn->tx_state != NsduConnection::TX_WAIT_FC) return;

    const uint8_t fc_flags = pci_byte & 0x0F;
    if (fc_flags == kFcOverflow) {
      conn->tx_state = NsduConnection::TX_IDLE;
      conn->tx_buffer.clear();
      conn->tx_offset = 0;
      return;
    }
    if (fc_flags == kFcWait) {
      // Wait — stay in TX_WAIT_FC, will be retried. Per ISO 15765-2 §9.6.5.3,
      // an FC(WT) restarts the N_Bs timer -- an unresponsive-but-alive peer
      // that keeps sending WT before N_Bs expires can hold the session open
      // indefinitely, which is correct behavior (it's still telling us it's
      // there); a peer that goes silent still gets caught by the deadline.
      conn->tx_fc_deadline = std::chrono::steady_clock::now() +
                             std::chrono::milliseconds(conn->config.n_bs_ms);
      // Not strictly required (this only pushes the deadline later, never
      // earlier), but notifying here too keeps "every deadline mutation
      // notifies" simple to reason about rather than case-by-case.
      plugin->tx_cv.notify_one();
      return;
    }
    // Continue
    const uint8_t bs    = (conn->config.extended_addressing) ? data[2] : data[1];
    const uint8_t stmin = (conn->config.extended_addressing) ? data[3] : data[2];
    conn->tx_bs_remaining = bs;
    conn->tx_bs_original  = bs;
    conn->tx_stmin_us     = stmin_to_us(stmin);
    conn->tx_state        = NsduConnection::TX_SEND_CF;
    conn->tx_next_send_time = std::chrono::steady_clock::now();
    plugin->tx_cv.notify_one();
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
    const uint8_t addr_off = conn->config.extended_addressing ? 1 : 0;
    const uint8_t sf_len_nibble = pci_byte & 0x0F;
    const bool escaped = is_fd && sf_len_nibble == 0 && dlc > addr_off + 1u;
    const size_t offset = addr_off + (escaped ? 2 : 1);
    const size_t sf_len = escaped ? data[addr_off + 1] : sf_len_nibble;
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
    // First Frame
    const uint32_t ff_len = ((static_cast<uint32_t>(pci_byte & 0x0F)) << 8) |
                             static_cast<uint32_t>(data[1]);

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
      if (plugin->frame_publish_fn == nullptr) return;

      uint8_t fc_buf[64];
      uint8_t idx = 0;
      if (conn->config.extended_addressing) {
        fc_buf[idx++] = 0x00;
      }
      fc_buf[idx++] = kPciFc | kFcOverflow;
      fc_buf[idx++] = 0;  // BS (don't care for overflow)
      fc_buf[idx++] = 0;  // STmin (don't care for overflow)
      const uint8_t fc_dlc = pad_frame(fc_buf, idx, conn->config.can_dlc);

      {
        auto fc = BoatFrameOwner::Can(
            plugin->iface, conn->source_addr,
            fc_dlc,
            static_cast<uint8_t>(is_fd ? 0x04 : 0),
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
    conn->rx_cf_deadline = std::chrono::steady_clock::now() +
                           std::chrono::milliseconds(conn->config.n_cr_ms);
    // Required, not just for consistency: this is a *new* deadline where
    // none existed before, possibly earlier than whatever the TX thread is
    // currently sleeping until (or it may be sleeping indefinitely, with
    // nothing else pending) -- without this it wouldn't wake to notice
    // N_Cr until some unrelated event happened to notify it.
    plugin->tx_cv.notify_one();

    // Send Flow Control (Continue) with configured BS and STmin
    if (plugin->frame_publish_fn == nullptr) return;

    uint8_t fc_buf[64];
    uint8_t idx = 0;
    if (conn->config.extended_addressing) {
      fc_buf[idx++] = 0x00;
    }
    fc_buf[idx++] = kPciFc | kFcContinue;
    fc_buf[idx++] = conn->config.block_size;  // BS (0 = unlimited)
    fc_buf[idx++] = conn->config.st_min;      // STmin
    const uint8_t fc_dlc = pad_frame(fc_buf, idx, conn->config.can_dlc);

    {
      auto fc = BoatFrameOwner::Can(
          plugin->iface, conn->source_addr,
          fc_dlc,
          static_cast<uint8_t>(is_fd ? 0x04 : 0),
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
      conn->rx_cf_deadline = std::chrono::steady_clock::now() +
                             std::chrono::milliseconds(conn->config.n_cr_ms);
      plugin->tx_cv.notify_one();
      // Re-FC: if BS > 0 and we've received a full block, send another FC
      ++conn->rx_cf_count;
      if (conn->config.block_size > 0 &&
          (conn->rx_cf_count % conn->config.block_size) == 0) {
        if (plugin->frame_publish_fn == nullptr) return;

        uint8_t fc_buf[64];
        uint8_t fcidx = 0;
        if (conn->config.extended_addressing) {
          fc_buf[fcidx++] = 0x00;
        }
        fc_buf[fcidx++] = kPciFc | kFcContinue;
        fc_buf[fcidx++] = conn->config.block_size;
        fc_buf[fcidx++] = conn->config.st_min;
        const uint8_t fc_dlc = pad_frame(fc_buf, fcidx, conn->config.can_dlc);

        auto fc = BoatFrameOwner::Can(
            plugin->iface, conn->source_addr,
            fc_dlc,
            static_cast<uint8_t>(is_fd ? 0x04 : 0),
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
  // and tp_on_frame(), the watchdog check in can_tp_tx_thread_func()) can
  // use the value directly without re-checking for 0.
  conn.config.n_bs_ms = resolve_timeout_ms(config->n_bs_ms);
  conn.config.n_cr_ms = resolve_timeout_ms(config->n_cr_ms);
  conn.rx_state    = NsduConnection::RX_IDLE;
  conn.tx_state    = NsduConnection::TX_IDLE;
  conn.source_addr = config->source_addr;
  conn.target_addr = config->target_addr;

  {
    std::lock_guard<std::mutex> lock(plugin->tx_mutex);
    // Keyed by nsdu_id -- the caller-facing session identifier -- not
    // source_addr, so send/remove/subscribe by nsdu_id are unambiguous.
    // Re-configuring an already-configured nsdu_id overwrites it in place
    // (doubles as "edit"); this also resets rx/tx state, which is fine even
    // mid-transfer since tp_on_frame's RX path and the TX pacing thread both
    // hold this same lock before touching a connection.
    plugin->connections[conn.nsdu_id] = conn;
  }
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
  // can_tp_tx_thread_func). Only IDLE/COMPLETE are safe to erase.
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

  // connections is keyed by nsdu_id, so lookup is direct. Copy the fields
  // this function needs into locals while still holding tx_mutex -- a
  // concurrent can_tp_configure() targeting the same connection overwrites
  // NsduConnection::config in place (same map slot, same pointer), so
  // reading conn->config.* unlocked after releasing the lock could observe
  // a torn mix of old/new values.
  NsduConnection* conn = nullptr;
  uint8_t dlc;
  bool extended_addressing;
  uint32_t target_addr;
  {
    std::lock_guard<std::mutex> lock(plugin->tx_mutex);
    auto it = plugin->connections.find(nsdu_id);
    if (it == plugin->connections.end()) return -1;
    conn = &it->second;

    if (conn->tx_state != NsduConnection::TX_IDLE) return -1;  // busy

    dlc = conn->config.can_dlc;
    extended_addressing = conn->config.extended_addressing;
    target_addr = conn->target_addr;
  }

  const uint32_t max_payload = dlc;
  const bool is_fd = (dlc > 8);

  // SF PCI format per ISO 15765-2:2016 §9.6.2 Table 11:
  //   - Classic CAN (dlc == 8): 1 PCI byte, SF_DL encoded in the low nibble
  //     (0x0X), so SF_DL is capped at 7 (6 with extended addressing's extra
  //     address byte).
  //   - CAN FD (dlc > 8): 2 PCI bytes -- byte0 = 0x00 (escape, low nibble
  //     zero), byte1 = SF_DL as a full byte, allowing SF_DL up to dlc-2
  //     (dlc-3 with extended addressing).
  const uint32_t sf_pci_overhead = is_fd ? 2 : 1;
  const uint32_t addr_overhead = extended_addressing ? 1 : 0;
  const uint32_t sf_max_len = max_payload - sf_pci_overhead - addr_overhead;

  if (len <= sf_max_len) {
    // Single Frame — send directly, no state machine needed
    uint8_t sf_buf[64];
    uint8_t idx = 0;
    if (extended_addressing) {
      sf_buf[idx++] = static_cast<uint8_t>(target_addr & 0xFF);
    }
    if (is_fd) {
      sf_buf[idx++] = kPciSf;                    // escape: low nibble 0
      sf_buf[idx++] = static_cast<uint8_t>(len);  // SF_DL as a full byte
    } else {
      sf_buf[idx++] = kPciSf | static_cast<uint8_t>(len);
    }
    std::memcpy(sf_buf + idx, data, len);
    const uint8_t sf_dlc = pad_frame(sf_buf, static_cast<uint8_t>(idx + len), dlc);

    {
      auto sf = BoatFrameOwner::Can(
          plugin->iface, conn->source_addr,
          sf_dlc, static_cast<uint8_t>(is_fd ? 0x04 : 0),
          std::vector<uint8_t>(sf_buf, sf_buf + sf_dlc), is_fd);
      plugin->frame_publish_fn(plugin->frame_publisher_ctx, sf.get());
    }
    return 1;
  }

  // Multi-frame: send FF, then CFs via TX thread

  // First Frame
  uint8_t ff_buf[64];
  uint8_t idx = 0;
  if (extended_addressing) {
    ff_buf[idx++] = static_cast<uint8_t>(target_addr & 0xFF);
  }
  ff_buf[idx++] = kPciFf | static_cast<uint8_t>((len >> 8) & 0x0F);
  ff_buf[idx++] = static_cast<uint8_t>(len & 0xFF);
  // FF overhead is 2 PCI bytes, plus 1 more for the target-address byte
  // under extended addressing -- matches `idx` above exactly (1 addr byte
  // if extended, then always 2 PCI bytes).
  const uint32_t ff_overhead = extended_addressing ? 3 : 2;
  const uint32_t ff_payload = std::min(len, max_payload - ff_overhead);
  std::memcpy(ff_buf + idx, data, ff_payload);
  const uint8_t ff_dlc = pad_frame(ff_buf, static_cast<uint8_t>(idx + ff_payload), dlc);

  {
    auto ff = BoatFrameOwner::Can(
        plugin->iface, conn->source_addr,
        ff_dlc, static_cast<uint8_t>(is_fd ? 0x04 : 0),
        std::vector<uint8_t>(ff_buf, ff_buf + ff_dlc), is_fd);
    plugin->frame_publish_fn(plugin->frame_publisher_ctx, ff.get());
  }

  // Initialize TX state machine
  {
    std::lock_guard<std::mutex> lock(plugin->tx_mutex);
    conn->tx_buffer.assign(data, data + len);
    conn->tx_offset = ff_payload;
    conn->tx_seq = 1;
    conn->tx_bs_remaining = 0;   // will be set when FC arrives
    conn->tx_stmin_us = 0;
    conn->tx_state = NsduConnection::TX_WAIT_FC;
    conn->tx_next_send_time = std::chrono::steady_clock::now();
    conn->tx_fc_deadline = std::chrono::steady_clock::now() +
                           std::chrono::milliseconds(conn->config.n_bs_ms);
  }
  plugin->tx_cv.notify_one();

  return 0;  // 0 = initiated
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

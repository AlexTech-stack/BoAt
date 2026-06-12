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

bool is_source_addr(CanTpPlugin* plugin, uint32_t can_id) {
  return plugin->connections.find(can_id) != plugin->connections.end();
}

// ── TX thread ──────────────────────────────────────────────────────────────

void can_tp_tx_thread_func(CanTpPlugin* plugin) {
  using namespace std::chrono;

  while (!plugin->tx_stop.load()) {
    // Collect connections that need TX processing
    struct TxWork {
      NsduConnection* conn;
      uint32_t source_addr;
    };
    std::vector<TxWork> to_send_cf;

    {
      std::unique_lock<std::mutex> lock(plugin->tx_mutex);
      plugin->tx_cv.wait_for(lock, std::chrono::microseconds(500), [&] {
        return plugin->tx_stop.load();
      });
      if (plugin->tx_stop.load()) break;

      auto now = steady_clock::now();
      for (auto& [addr, conn] : plugin->connections) {
        if (conn.tx_state == NsduConnection::TX_SEND_CF) {
          if (now >= conn.tx_next_send_time) {
            to_send_cf.push_back({&conn, addr});
          }
        }
      }
    }

    // Send CFs without holding the lock
    for (auto& work : to_send_cf) {
      auto* conn = work.conn;
      auto addr = work.source_addr;
      if (conn->tx_state != NsduConnection::TX_SEND_CF) continue;

      const uint8_t dlc = conn->config.can_dlc;
      const uint32_t max_payload = dlc - (conn->config.extended_addressing ? 2 : 1);

      // Build and send one CF
      BoatCanFrame cf{};
      cf.can_id = conn->source_addr;
      uint8_t idx = 0;
      if (conn->config.extended_addressing) {
        cf.data[idx++] = static_cast<uint8_t>(conn->target_addr & 0xFF);
      }
      uint8_t seq;
      uint32_t chunk;
      {
        std::lock_guard<std::mutex> lock(plugin->tx_mutex);
        seq = conn->tx_seq;
        chunk = static_cast<uint32_t>(
            std::min(conn->tx_buffer.size() - conn->tx_offset,
                     static_cast<size_t>(max_payload - 1)));
        std::memcpy(cf.data + idx, conn->tx_buffer.data() + conn->tx_offset,
                    chunk);
      }
      cf.data[idx++] = kPciCf | (seq & 0x0F);
      cf.dlc = static_cast<uint8_t>(idx + chunk);

      plugin->can_publish_fn(plugin->can_publisher_ctx, &cf);

      {
        std::lock_guard<std::mutex> lock(plugin->tx_mutex);
        conn->tx_offset += chunk;
        conn->tx_seq = (conn->tx_seq + 1) & 0x0F;
        if (conn->tx_bs_remaining > 0) conn->tx_bs_remaining--;
        conn->tx_next_send_time = steady_clock::now() +
                                  microseconds(conn->tx_stmin_us);

        if (conn->tx_bs_remaining == 0 &&
            conn->tx_offset < conn->tx_buffer.size()) {
          // Block size exhausted — wait for next FC
          conn->tx_state = NsduConnection::TX_WAIT_FC;
        } else if (conn->tx_offset >= conn->tx_buffer.size()) {
          // All data sent
          conn->tx_state = NsduConnection::TX_IDLE;
          conn->tx_buffer.clear();
          conn->tx_offset = 0;
          conn->tx_seq = 0;
        }
      }
    }
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

void tp_set_can_publisher(void* ctx, BoatCanPublishFn fn, void* publisher_ctx) {
  auto* plugin = static_cast<CanTpPlugin*>(ctx);
  if (plugin == nullptr) return;
  plugin->can_publish_fn    = fn;
  plugin->can_publisher_ctx = publisher_ctx;
}

void tp_set_pdu_publisher(void* ctx, BoatPduPublishFn fn, void* publisher_ctx) {
  auto* plugin = static_cast<CanTpPlugin*>(ctx);
  if (plugin == nullptr) return;
  plugin->pdu_publish_fn    = fn;
  plugin->pdu_publisher_ctx = publisher_ctx;
}

// ── ISO 15765-2 receive path ─────────────────────────────────────────────────

void tp_on_can_frame(void* ctx, const BoatCanFrame* frame, const char* iface) {
  auto* plugin = static_cast<CanTpPlugin*>(ctx);
  if (plugin == nullptr || frame == nullptr || frame->dlc < 1) return;

  if (iface != nullptr && iface != plugin->iface) return;

  // ── Loopback filter ──────────────────────────────────────────────────────
  // Frames we sent ourselves have CAN ID matching source_addr of some
  // connection.  Skip them to avoid self-interference.
  if (is_source_addr(plugin, frame->can_id)) return;

  const uint8_t pci_byte = frame->data[0];
  const uint8_t pci_type = pci_byte & kPciMask;
  const uint8_t* data = frame->data;
  const uint8_t  dlc  = frame->dlc;

  // ── Find connection by target_addr ───────────────────────────────────────
  // Only frames from the peer (on target_addr) are processed.
  NsduConnection* conn = find_by_target(plugin, frame->can_id);
  if (conn == nullptr) return;  // unknown — silently drop

  if (pci_type == kPciFc) {
    // ── Flow Control from peer ─────────────────────────────────────────────
    // Data[0] = PCI (0x30 | flags)
    // Data[1] = BS (Block Size)
    // Data[2] = STmin (Separation Time)
    {
      std::lock_guard<std::mutex> lock(plugin->tx_mutex);
      if (conn->tx_state != NsduConnection::TX_WAIT_FC) return;

      const uint8_t fc_flags = pci_byte & 0x0F;
      if (fc_flags == kFcOverflow) {
        conn->tx_state = NsduConnection::TX_IDLE;
        conn->tx_buffer.clear();
        conn->tx_offset = 0;
        return;
      }
      if (fc_flags == kFcWait) {
        // Wait — stay in TX_WAIT_FC, will be retried
        return;
      }
      // Continue
      const uint8_t bs    = (conn->config.extended_addressing) ? data[2] : data[1];
      const uint8_t stmin = (conn->config.extended_addressing) ? data[3] : data[2];
      conn->tx_bs_remaining = bs;
      conn->tx_stmin_us     = stmin_to_us(stmin);
      conn->tx_state        = NsduConnection::TX_SEND_CF;
      conn->tx_next_send_time = std::chrono::steady_clock::now();
    }
    plugin->tx_cv.notify_one();
    return;
  }

  // ── RX path: SF / FF / CF on target_addr ────────────────────────────────

  if (pci_type == kPciSf) {
    // Single Frame
    const uint8_t sf_len = pci_byte & 0x0F;
    const uint32_t offset = conn->config.extended_addressing ? 2 : 1;
    const uint32_t payload_len = dlc > offset ? dlc - offset : 0;
    const uint32_t actual_len = std::min(static_cast<uint32_t>(sf_len), payload_len);

    if (plugin->pdu_publish_fn == nullptr) return;
    BoatPduFrame pf{};
    pf.pdu_id      = conn->nsdu_id;
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

    const uint32_t offset = conn->config.extended_addressing ? 3 : 2;
    const uint32_t first_chunk = dlc > offset ? dlc - offset : 0;

    if (ff_len > conn->config.rx_buffer_size) {
      // ── Overflow: send FC with Overflow status ──────────────────────────
      conn->rx_state = NsduConnection::RX_IDLE;
      if (plugin->can_publish_fn == nullptr) return;
      BoatCanFrame fc{};
      fc.can_id = conn->target_addr;
      uint8_t idx = 0;
      if (conn->config.extended_addressing) {
        fc.data[idx++] = 0x00;
      }
      fc.data[idx++] = kPciFc | kFcOverflow;
      fc.data[idx++] = 0;  // BS (don't care for overflow)
      fc.data[idx++] = 0;  // STmin (don't care for overflow)
      fc.dlc = static_cast<uint8_t>(idx);
      plugin->can_publish_fn(plugin->can_publisher_ctx, &fc);
      return;
    }

    // Normal FF processing
    conn->rx_buffer.clear();
    conn->rx_buffer.reserve(ff_len);
    conn->rx_buffer.assign(data + offset, data + offset + first_chunk);
    conn->rx_expected_len = ff_len;
    conn->rx_next_seq = 1;
    conn->rx_state = NsduConnection::RX_WAIT_CF;

    // Send Flow Control (Continue) with configured BS and STmin
    if (plugin->can_publish_fn == nullptr) return;
    BoatCanFrame fc{};
    fc.can_id = conn->target_addr;
    uint8_t idx = 0;
    if (conn->config.extended_addressing) {
      fc.data[idx++] = 0x00;
    }
    fc.data[idx++] = kPciFc | kFcContinue;
    fc.data[idx++] = conn->config.block_size;  // BS (0 = unlimited)
    fc.data[idx++] = conn->config.st_min;      // STmin
    fc.dlc = static_cast<uint8_t>(idx);
    plugin->can_publish_fn(plugin->can_publisher_ctx, &fc);
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
    const uint32_t offset = conn->config.extended_addressing ? 2 : 1;
    const uint32_t chunk = dlc > offset ? dlc - offset : 0;
    conn->rx_buffer.insert(conn->rx_buffer.end(), data + offset, data + offset + chunk);

    if (conn->rx_buffer.size() >= conn->rx_expected_len) {
      conn->rx_buffer.resize(conn->rx_expected_len);
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
    }
    return;
  }
}

}  // anonymous namespace

// ── Standalone CanTp API ─────────────────────────────────────────────────────

int32_t can_tp_configure(void* tp_ctx, const CanTpConfig* config) {
  auto* plugin = static_cast<CanTpPlugin*>(tp_ctx);
  if (plugin == nullptr || config == nullptr) return -1;

  NsduConnection conn;
  conn.nsdu_id    = config->nsdu_id;
  conn.config     = *config;
  conn.rx_state   = NsduConnection::RX_IDLE;
  conn.tx_state   = NsduConnection::TX_IDLE;

  // Backward compat: if source_addr and target_addr are both 0,
  // use nsdu_id as a single CAN ID for both.
  if (config->source_addr == 0 && config->target_addr == 0) {
    conn.source_addr = config->nsdu_id;
    conn.target_addr = config->nsdu_id;
  } else {
    conn.source_addr = config->source_addr;
    conn.target_addr = config->target_addr;
  }

  if (conn.source_addr == 0 || conn.target_addr == 0) return -1;

  {
    std::lock_guard<std::mutex> lock(plugin->tx_mutex);
    plugin->connections[conn.source_addr] = conn;
  }
  return 0;
}

int32_t can_tp_send(void* tp_ctx, uint32_t nsdu_id,
                    const uint8_t* data, uint32_t len) {
  auto* plugin = static_cast<CanTpPlugin*>(tp_ctx);
  if (plugin == nullptr || data == nullptr) return -1;
  if (plugin->can_publish_fn == nullptr) return -1;

  if (len == 0) return -1;

  // Find connection by nsdu_id (fallback) or source_addr
  NsduConnection* conn = nullptr;
  {
    std::lock_guard<std::mutex> lock(plugin->tx_mutex);
    auto it = plugin->connections.find(nsdu_id);
    if (it == plugin->connections.end()) {
      // Try nsdu_id as source_addr
      for (auto& [addr, c] : plugin->connections) {
        if (c.nsdu_id == nsdu_id) {
          conn = &c;
          break;
        }
      }
      if (conn == nullptr) return -1;
    } else {
      conn = &it->second;
    }

    if (conn->tx_state != NsduConnection::TX_IDLE) return -1;  // busy
  }

  const uint8_t dlc = conn->config.can_dlc;
  const uint32_t max_payload = dlc - (conn->config.extended_addressing ? 2 : 1);

  if (len <= 7) {
    // Single Frame — send directly, no state machine needed
    BoatCanFrame sf{};
    sf.can_id = conn->source_addr;
    uint8_t idx = 0;
    if (conn->config.extended_addressing) {
      sf.data[idx++] = static_cast<uint8_t>(conn->target_addr & 0xFF);
    }
    sf.data[idx++] = kPciSf | static_cast<uint8_t>(len);
    std::memcpy(sf.data + idx, data, len);
    sf.dlc = static_cast<uint8_t>(idx + len);
    plugin->can_publish_fn(plugin->can_publisher_ctx, &sf);
    return 1;
  }

  // Multi-frame: send FF, then CFs via TX thread

  // First Frame
  BoatCanFrame ff{};
  ff.can_id = conn->source_addr;
  uint8_t idx = 0;
  if (conn->config.extended_addressing) {
    ff.data[idx++] = static_cast<uint8_t>(conn->target_addr & 0xFF);
  }
  ff.data[idx++] = kPciFf | static_cast<uint8_t>((len >> 8) & 0x0F);
  ff.data[idx++] = static_cast<uint8_t>(len & 0xFF);
  const uint32_t ff_payload = std::min(len, max_payload - 2);
  std::memcpy(ff.data + idx, data, ff_payload);
  ff.dlc = static_cast<uint8_t>(idx + ff_payload);
  plugin->can_publish_fn(plugin->can_publisher_ctx, &ff);

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
    vt.set_can_publisher   = &tp_set_can_publisher;
    vt.on_can_frame        = &tp_on_can_frame;
    vt.set_eth_publisher   = nullptr;
    vt.on_eth_frame        = nullptr;
    vt.set_bus_publisher   = nullptr;
    vt.set_pdu_publisher   = &tp_set_pdu_publisher;
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

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

// ── Plugin vtable callbacks ──────────────────────────────────────────────────

int tp_initialize(void* ctx, const char* config_json) {
  auto* plugin = static_cast<CanTpPlugin*>(ctx);
  if (plugin == nullptr) return -1;

  // Parse minimal config: {"iface":"vcan0"}
  if (config_json != nullptr) {
    // Simple JSON extraction for iface — no full JSON parser dependency
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
  return 0;
}

void tp_on_tick(void* /*ctx*/, uint64_t /*tick*/) {}

void tp_shutdown(void* ctx) {
  auto* plugin = static_cast<CanTpPlugin*>(ctx);
  if (plugin == nullptr) return;
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

  const uint8_t pci_byte = frame->data[0];
  const uint8_t pci_type = pci_byte & kPciMask;

  // Find the connection by CAN ID (used as N-SDU ID).
  auto it = plugin->connections.find(frame->can_id);
  if (it == plugin->connections.end()) {
    // Unknown CAN ID — attempt to create an implicit RX connection
    if (pci_type == kPciSf || pci_type == kPciFf) {
      CanTpConfig cfg{};
      cfg.nsdu_id = frame->can_id;
      cfg.rx_buffer_size = 4095;
      cfg.can_dlc = 8;
      cfg.is_rx = true;
      can_tp_configure(plugin, &cfg);
      it = plugin->connections.find(frame->can_id);
      if (it == plugin->connections.end()) return;
    } else {
      return;  // CF or FC without prior FF → ignore
    }
  }

  auto& conn = it->second;
  const uint8_t* data = frame->data;
  const uint8_t  dlc  = frame->dlc;

  if (pci_type == kPciSf) {
    // Implicit connection was just created, rx_state is RX_IDLE
    // Single Frame: PCI byte = [0x00 | length]
    const uint8_t sf_len = pci_byte & 0x0F;
    // In extended addressing, the first data byte is the target address
    const uint32_t offset = conn.config.extended_addressing ? 2 : 1;
    const uint32_t payload_len = dlc > offset ? dlc - offset : 0;
    const uint32_t actual_len = std::min(static_cast<uint32_t>(sf_len), payload_len);

    if (plugin->pdu_publish_fn == nullptr) return;
    BoatPduFrame pf{};
    pf.pdu_id      = conn.nsdu_id;
    pf.payload     = const_cast<uint8_t*>(data + offset);
    pf.payload_len = actual_len;
    pf.iface       = plugin->iface.c_str();
    plugin->pdu_publish_fn(plugin->pdu_publisher_ctx, &pf);
    return;
  }

  if (pci_type == kPciFf) {
    // First Frame: PCI = [0x10 | len>>8] [len & 0xFF]
    const uint32_t ff_len = ((static_cast<uint32_t>(pci_byte & 0x0F)) << 8) |
                             static_cast<uint32_t>(data[1]);
    if (ff_len > conn.config.rx_buffer_size) {
      conn.rx_state = NsduConnection::RX_IDLE;
      return;  // overflow
    }
    conn.rx_buffer.clear();
    conn.rx_buffer.reserve(ff_len);
    const uint32_t offset = conn.config.extended_addressing ? 3 : 2;
    const uint32_t first_chunk = dlc > offset ? dlc - offset : 0;
    conn.rx_buffer.assign(data + offset, data + offset + first_chunk);
    conn.rx_expected_len = ff_len;
    conn.rx_next_seq = 1;  // next CF expects sequence number 1
    conn.rx_state = NsduConnection::RX_WAIT_CF;

    // Send Flow Control (Continue, BS=0, STmin=0) — BS=0 means unlimited
    if (plugin->can_publish_fn == nullptr) return;
    BoatCanFrame fc{};
    fc.can_id = conn.nsdu_id;
    fc.dlc    = conn.config.extended_addressing ? 4 : 3;
    uint8_t idx = 0;
    if (conn.config.extended_addressing) {
      fc.data[idx++] = 0x00;  // target address placeholder
    }
    fc.data[idx++] = kPciFc | kFcContinue;  // FC flags
    fc.data[idx++] = conn.config.block_size;
    fc.data[idx++] = conn.config.st_min;
    plugin->can_publish_fn(plugin->can_publisher_ctx, &fc);
    return;
  }

  if (pci_type == kPciCf) {
    // Consecutive Frame: PCI = [0x20 | seq_index]
    if (conn.rx_state != NsduConnection::RX_WAIT_CF) return;
    const uint8_t seq = pci_byte & 0x0F;
    if (seq != conn.rx_next_seq) {
      conn.rx_state = NsduConnection::RX_IDLE;
      return;  // sequence error
    }
    const uint32_t offset = conn.config.extended_addressing ? 2 : 1;
    const uint32_t chunk = dlc > offset ? dlc - offset : 0;
    conn.rx_buffer.insert(conn.rx_buffer.end(), data + offset, data + offset + chunk);

    // Check if complete
    if (conn.rx_buffer.size() >= conn.rx_expected_len) {
      conn.rx_buffer.resize(conn.rx_expected_len);
      if (plugin->pdu_publish_fn == nullptr) return;
      BoatPduFrame pf{};
      pf.pdu_id      = conn.nsdu_id;
      pf.payload     = conn.rx_buffer.data();
      pf.payload_len = conn.rx_buffer.size();
      pf.iface       = plugin->iface.c_str();
      plugin->pdu_publish_fn(plugin->pdu_publisher_ctx, &pf);
      conn.rx_state = NsduConnection::RX_IDLE;
    } else {
      conn.rx_next_seq = (seq + 1) & 0x0F;
    }
    return;
  }

  // FC frames are only relevant in TX direction — handled by can_tp_send internals
  (void)kPciFc;
}

}  // anonymous namespace

// ── Standalone CanTp API ─────────────────────────────────────────────────────

int32_t can_tp_configure(void* tp_ctx, const CanTpConfig* config) {
  auto* plugin = static_cast<CanTpPlugin*>(tp_ctx);
  if (plugin == nullptr || config == nullptr) return -1;

  NsduConnection conn;
  conn.nsdu_id   = config->nsdu_id;
  conn.config    = *config;
  conn.rx_state  = NsduConnection::RX_IDLE;
  conn.tx_wait_fc = false;
  plugin->connections[config->nsdu_id] = conn;
  return 0;
}

int32_t can_tp_send(void* tp_ctx, uint32_t nsdu_id,
                    const uint8_t* data, uint32_t len) {
  auto* plugin = static_cast<CanTpPlugin*>(tp_ctx);
  if (plugin == nullptr || data == nullptr) return -1;
  if (plugin->can_publish_fn == nullptr) return -1;

  auto it = plugin->connections.find(nsdu_id);
  if (it == plugin->connections.end()) return -1;

  auto& conn = it->second;
  int32_t frames_sent = 0;

  if (len <= 7) {
    // Single Frame
    BoatCanFrame sf{};
    sf.can_id = nsdu_id;
    uint8_t idx = 0;
    if (conn.config.extended_addressing) {
      sf.data[idx++] = 0x00;  // source address placeholder
    }
    sf.data[idx++] = kPciSf | static_cast<uint8_t>(len);
    std::memcpy(sf.data + idx, data, len);
    sf.dlc = static_cast<uint8_t>(idx + len);
    plugin->can_publish_fn(plugin->can_publisher_ctx, &sf);
    frames_sent = 1;
  } else {
    // First Frame + Consecutive Frames
    const uint8_t dlc = conn.config.can_dlc;
    const uint32_t max_payload = dlc - (conn.config.extended_addressing ? 2 : 1);

    // First Frame
    BoatCanFrame ff{};
    ff.can_id = nsdu_id;
    uint8_t idx = 0;
    if (conn.config.extended_addressing) {
      ff.data[idx++] = 0x00;
    }
    ff.data[idx++] = kPciFf | static_cast<uint8_t>((len >> 8) & 0x0F);
    ff.data[idx++] = static_cast<uint8_t>(len & 0xFF);
    const uint32_t ff_payload = std::min(len, max_payload - 2);
    std::memcpy(ff.data + idx, data, ff_payload);
    ff.dlc = static_cast<uint8_t>(idx + ff_payload);
    plugin->can_publish_fn(plugin->can_publisher_ctx, &ff);
    frames_sent = 1;

    // Consecutive Frames
    uint32_t offset = ff_payload;
    uint8_t seq = 1;
    while (offset < len) {
      BoatCanFrame cf{};
      cf.can_id = nsdu_id;
      idx = 0;
      if (conn.config.extended_addressing) {
        cf.data[idx++] = 0x00;
      }
      cf.data[idx++] = kPciCf | (seq & 0x0F);
      const uint32_t chunk = std::min(len - offset, max_payload - 1);
      std::memcpy(cf.data + idx, data + offset, chunk);
      cf.dlc = static_cast<uint8_t>(idx + chunk);
      plugin->can_publish_fn(plugin->can_publisher_ctx, &cf);
      frames_sent++;
      offset += chunk;
      seq = (seq + 1) & 0x0F;
    }
  }

  return frames_sent;
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

#include "someip_plugin.h"

#include <cstdlib>
#include <cstring>
#include <sstream>
#include <vector>

namespace {

// ── Helpers ─────────────────────────────────────────────────────────────────

void WriteUint16(uint8_t* dst, uint16_t val) {
  dst[0] = static_cast<uint8_t>((val >> 8) & 0xFF);
  dst[1] = static_cast<uint8_t>(val & 0xFF);
}

void WriteUint32(uint8_t* dst, uint32_t val) {
  dst[0] = static_cast<uint8_t>((val >> 24) & 0xFF);
  dst[1] = static_cast<uint8_t>((val >> 16) & 0xFF);
  dst[2] = static_cast<uint8_t>((val >> 8) & 0xFF);
  dst[3] = static_cast<uint8_t>(val & 0xFF);
}

uint16_t ReadUint16(const uint8_t* src) {
  return (static_cast<uint16_t>(src[0]) << 8) | static_cast<uint16_t>(src[1]);
}

uint32_t ReadUint32(const uint8_t* src) {
  return (static_cast<uint32_t>(src[0]) << 24) |
         (static_cast<uint32_t>(src[1]) << 16) |
         (static_cast<uint32_t>(src[2]) << 8)  |
          static_cast<uint32_t>(src[3]);
}

// Build a complete Ethernet frame with UDP + SOME/IP header + payload.
std::vector<uint8_t> BuildSomeipUdpFrame(uint16_t src_port, uint16_t dst_port,
                                          const uint8_t* someip_header_and_payload,
                                          uint32_t total_len) {
  // Minimal UDP/IPv4 header (20 bytes IP + 8 bytes UDP)
  std::vector<uint8_t> frame;
  frame.resize(28 + total_len);

  // IPv4 header (20 bytes)
  frame[0]  = 0x45;  // version=4, IHL=5
  frame[1]  = 0x00;  // DSCP+ECN
  const uint16_t ip_total = static_cast<uint16_t>(20 + 8 + total_len);
  WriteUint16(&frame[2], ip_total);   // total length
  frame[4]  = 0x00;  // identification (high)
  frame[5]  = 0x00;  // identification (low)
  WriteUint16(&frame[6], 0x4000);     // flags + fragment offset (DF)
  frame[8]  = 64;                     // TTL
  frame[9]  = 0x11;                   // UDP protocol (17)
  // checksum = 0 (optional for UDP/IPv4)

  // Source IP and Dest IP are placeholders — actual IP will be set by the host
  // or network configuration. Using 0.0.0.0 as a wildcard here lets the
  // EthernetBusRegistry or downstream handle addressing.

  // UDP header (8 bytes)
  WriteUint16(&frame[20], src_port);
  WriteUint16(&frame[22], dst_port);
  const uint16_t udp_len = static_cast<uint16_t>(8 + total_len);
  WriteUint16(&frame[24], udp_len);
  WriteUint16(&frame[26], 0);  // UDP checksum (optional)

  // SOME/IP payload
  std::memcpy(&frame[28], someip_header_and_payload, total_len);

  return frame;
}

void BuildSomeipHeader(uint8_t* buf, uint16_t service_id, uint16_t method_id,
                       uint32_t payload_len, uint16_t client_id,
                       uint16_t session_id, uint8_t msg_type,
                       uint8_t return_code) {
  WriteUint16(&buf[0], service_id);
  WriteUint16(&buf[2], method_id);
  WriteUint32(&buf[4], payload_len + 8);  // length = payload + client+session+proto+iface+msg+ret
  WriteUint16(&buf[8], client_id);
  WriteUint16(&buf[10], session_id);
  buf[12] = SOMEIP_PROTOCOL_VERSION;
  buf[13] = 1;  // interface version
  buf[14] = msg_type;
  buf[15] = return_code;
}

// ── Plugin callbacks ─────────────────────────────────────────────────────────

int someip_initialize(void* ctx, const char* config_json) {
  auto* plugin = static_cast<SomeipPlugin*>(ctx);
  if (plugin == nullptr) return -1;

  // Parse optional config: {"sd_port": 30490, "services": [...]}
  if (config_json != nullptr) {
    // Extract sd_port
    const char* key = "\"sd_port\"";
    const char* pos = std::strstr(config_json, key);
    if (pos != nullptr) {
      pos += std::strlen(key);
      while (*pos && (*pos < '0' || *pos > '9')) ++pos;
      if (*pos >= '0' && *pos <= '9') {
        plugin->sd_port = static_cast<uint16_t>(std::atoi(pos));
      }
    }
  }
  return 0;
}

void someip_on_tick(void* /*ctx*/, uint64_t /*tick*/) {}

void someip_shutdown(void* ctx) {
  auto* plugin = static_cast<SomeipPlugin*>(ctx);
  if (plugin == nullptr) return;
  plugin->local_services.clear();
  plugin->remote_services.clear();
}

void someip_set_eth_publisher(void* ctx, BoatEthPublishFn fn, void* publisher_ctx) {
  auto* plugin = static_cast<SomeipPlugin*>(ctx);
  if (plugin == nullptr) return;
  plugin->eth_publish_fn    = fn;
  plugin->eth_publisher_ctx = publisher_ctx;
}

void someip_on_eth_frame(void* ctx, const BoatEthFrame* frame, const char* iface) {
  auto* plugin = static_cast<SomeipPlugin*>(ctx);
  if (plugin == nullptr || frame == nullptr) return;

  // Only process UDP frames (ethertype 0x0800 IPv4) on common SOME/IP ports
  if (frame->ethertype != 0x0800 && frame->ethertype != 0x86DD) return;
  if (frame->payload_len < 28) return;  // too small for IP+UDP header

  // Simple check: look for UDP port matching our SD port or well-known range
  const uint8_t* p = frame->payload;
  const uint32_t ip_header_len = (p[0] & 0x0F) * 4;
  if (ip_header_len < 20 || frame->payload_len < ip_header_len + 8) return;

  const uint16_t udp_dst = ReadUint16(p + ip_header_len + 2);
  const uint16_t udp_src = ReadUint16(p + ip_header_len);

  // Check if this is a SOME/IP port we care about
  if (udp_dst != plugin->sd_port && udp_src != plugin->sd_port) return;

  const uint8_t* udp_payload = p + ip_header_len + 8;
  const uint32_t udp_payload_len = frame->payload_len - ip_header_len - 8;
  if (udp_payload_len < 16) return;  // too small for SOME/IP header

  // Detect SOME/IP magic cookie (service_id = 0x0000) → SD message
  uint16_t service_id = ReadUint16(udp_payload);
  if (service_id == SOMEIP_MAGIC_COOKIE) {
    // Service Discovery message (simplified: treat as find/offer)
    // In a full implementation, decode SD entry types
    // (FindService / OfferService / SubscribeEventgroup)
    uint16_t sd_entries_count = ReadUint16(udp_payload + 12);
    (void)sd_entries_count;
    return;
  }

  // Regular SOME/IP request/response/notification
  uint16_t method_id = ReadUint16(udp_payload + 2);
  uint8_t msg_type = udp_payload[14];
  uint8_t return_code = udp_payload[15];

  // If this is a REQUEST for a locally-offered service, generate a stub response
  if (msg_type == SOMEIP_MSG_REQUEST && return_code == 0x00) {
    uint16_t client_id = ReadUint16(udp_payload + 8);
    uint16_t session_id = ReadUint16(udp_payload + 10);
    (void)client_id;
    (void)session_id;

    auto it = plugin->local_services.find(service_id);
    if (it != plugin->local_services.end() && plugin->eth_publish_fn != nullptr) {
      // Build response header + payload echo (simplified loopback)
      uint8_t header[16];
      BuildSomeipHeader(header, service_id, method_id, 4,
                        plugin->next_client_id++, plugin->next_session_id++,
                        SOMEIP_MSG_RESPONSE, 0x00);

      // Echo back first 4 bytes of payload as response
      std::vector<uint8_t> response_payload;
      response_payload.reserve(20);
      response_payload.insert(response_payload.end(), header, header + 16);
      if (udp_payload_len > 16) {
        response_payload.insert(response_payload.end(),
                                udp_payload + 16,
                                udp_payload + std::min(16U + 4, udp_payload_len));
      }

      auto eth_frame = BuildSomeipUdpFrame(udp_dst, udp_src,
                                            response_payload.data(),
                                            response_payload.size());
      BoatEthFrame response{};
      std::memcpy(response.dst_mac, frame->src_mac, 6);
      std::memcpy(response.src_mac, frame->dst_mac, 6);
      response.ethertype   = frame->ethertype;
      response.payload     = eth_frame.data();
      response.payload_len = eth_frame.size();
      plugin->eth_publish_fn(plugin->eth_publisher_ctx, &response);
    }
  }
}

}  // anonymous namespace

// ── Standard BoatPlugin entry points ─────────────────────────────────────────

extern "C" BoatPlugin* boat_plugin_create() {
  static BoatPluginVTable kVTable = [] {
    BoatPluginVTable vt{};
    vt.initialize          = &someip_initialize;
    vt.on_tick             = &someip_on_tick;
    vt.shutdown            = &someip_shutdown;
    vt.set_publisher       = nullptr;
    vt.set_can_publisher   = nullptr;
    vt.on_can_frame        = nullptr;
    vt.set_eth_publisher   = &someip_set_eth_publisher;
    vt.on_eth_frame        = &someip_on_eth_frame;
    vt.set_bus_publisher   = nullptr;
    vt.set_pdu_publisher   = nullptr;
    return vt;
  }();

  auto* state  = new SomeipPlugin{};
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
  delete static_cast<SomeipPlugin*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }

#include "tcp_plugin.h"
#include "tcp_segment.h"

#include <arpa/inet.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>

namespace btcp = boat::tcp;

// ── Helpers ────────────────────────────────────────────────────────────────

static std::mt19937& Rng() {
  static std::mt19937 rng(std::random_device{}());
  return rng;
}

static uint32_t Rand32() {
  return Rng()();
}

static void SendRaw(btcp::TcpPlugin* plugin, const std::vector<uint8_t>& seg) {
  if (!plugin->eth_publish_fn) return;
  BoatEthFrame ef{};
  std::memset(ef.dst_mac, 0xFF, 6);
  std::memset(ef.src_mac, 0x02, 6);
  ef.src_mac[5] = 0x01;
  // Determine ethertype from IP version byte (first nibble)
  ef.ethertype = (seg[0] >> 4 == 6) ? 0x86DD : 0x0800;
  ef.payload = const_cast<uint8_t*>(seg.data());
  ef.payload_len = seg.size();
  plugin->eth_publish_fn(plugin->eth_publisher_ctx, &ef);
}

static int NextId(btcp::TcpPlugin* plugin) {
  return plugin->next_id++;
}

static int ResolveAf(const char* ip) {
  struct in_addr a4;
  struct in6_addr a6;
  if (inet_pton(AF_INET, ip, &a4) == 1) return AF_INET;
  if (inet_pton(AF_INET6, ip, &a6) == 1) return AF_INET6;
  return AF_UNSPEC;
}

static void ParseIp(const char* ip, int af, std::array<uint8_t, 16>& out) {
  out.fill(0);
  if (af == AF_INET) {
    struct in_addr a4;
    inet_pton(AF_INET, ip, &a4);
    std::memcpy(out.data(), &a4, 4);
  } else if (af == AF_INET6) {
    struct in6_addr a6;
    inet_pton(AF_INET6, ip, &a6);
    std::memcpy(out.data(), &a6, 16);
  }
}

// ── Vtable callbacks ───────────────────────────────────────────────────────

static int tp_initialize(void* ctx, const char* config_json) {
  auto* plugin = static_cast<btcp::TcpPlugin*>(ctx);
  if (!plugin) return -1;

  if (config_json) {
    // Parse optional config: {"retry_ms": 1000, "max_retries": 5, "mss": 1460}
    auto get_val = [&](const char* key, uint32_t def) -> uint32_t {
      const char* p = std::strstr(config_json, key);
      if (!p) return def;
      p += std::strlen(key);
      while (*p && (*p < '0' || *p > '9')) ++p;
      return *p ? static_cast<uint32_t>(std::atoi(p)) : def;
    };
    plugin->retry_ms = get_val("\"retry_ms\"", 1000);
    plugin->max_retries = get_val("\"max_retries\"", 5);
    plugin->default_mss = static_cast<int>(get_val("\"mss\"", 1460));
  }

  // Start TX thread
  plugin->running.store(true);
  plugin->tx_thread = std::thread([plugin]() {
    while (plugin->running.load()) {
      std::unique_lock<std::mutex> lock(plugin->mutex);
      plugin->tx_cv.wait_for(lock, std::chrono::milliseconds(100));

      auto now = std::chrono::steady_clock::now();
      for (auto& [id, conn] : plugin->connections) {
        (void)id;
        bool need_send = false;
        std::vector<uint8_t> seg;

        // Send pending data
        if (conn.state == btcp::TCP_ESTABLISHED && !conn.send_buffer.empty()) {
          uint32_t chunk = std::min<uint32_t>(
              static_cast<uint32_t>(conn.send_buffer.size()),
              static_cast<uint32_t>(conn.mss));
          std::vector<uint8_t> data(conn.send_buffer.begin(),
                                     conn.send_buffer.begin() + chunk);
          conn.send_buffer.erase(conn.send_buffer.begin(),
                                  conn.send_buffer.begin() + chunk);

          if (conn.af == AF_INET) {
            seg = btcp::BuildIp4TcpSegment(
                conn.src_ip.data(), conn.dst_ip.data(),
                conn.src_port, conn.dst_port,
                conn.my_seq, conn.my_ack,
                data.data(), static_cast<uint32_t>(data.size()),
                btcp::TCP_FLAG_ACK | btcp::TCP_FLAG_PSH, 65535);
          } else {
            seg = btcp::BuildIp6TcpSegment(
                conn.src_ip.data(), conn.dst_ip.data(),
                conn.src_port, conn.dst_port,
                conn.my_seq, conn.my_ack,
                data.data(), static_cast<uint32_t>(data.size()),
                btcp::TCP_FLAG_ACK | btcp::TCP_FLAG_PSH, 65535);
          }
          conn.my_seq += static_cast<uint32_t>(data.size());
          conn.unacked_segment = seg;
          conn.retransmit_at = now + std::chrono::milliseconds(plugin->retry_ms);
          conn.retry_count = 0;
          need_send = true;
        }

        // Retransmit unacked segment on timeout
        if (!conn.unacked_segment.empty() && now >= conn.retransmit_at) {
          if (conn.retry_count >= static_cast<int>(plugin->max_retries)) {
            if (conn.on_event)
              conn.on_event(conn.user_ctx, conn.conn_id, btcp::TCP_EVENT_ERROR);
            conn.state = btcp::TCP_CLOSED;
            continue;
          }
          seg = conn.unacked_segment;
          conn.retransmit_at = now + std::chrono::milliseconds(
              plugin->retry_ms * (1 << conn.retry_count));
          conn.retry_count++;
          need_send = true;
        }

        if (need_send) {
          lock.unlock();
          SendRaw(plugin, seg);
          lock.lock();
        }
      }
    }
  });

  return 0;
}

static void tp_on_tick(void* ctx, uint64_t /*tick*/) {
  auto* plugin = static_cast<btcp::TcpPlugin*>(ctx);
  if (plugin) plugin->tx_cv.notify_one();
}

static void tp_shutdown(void* ctx) {
  auto* plugin = static_cast<btcp::TcpPlugin*>(ctx);
  if (!plugin) return;
  plugin->running.store(false);
  plugin->tx_cv.notify_one();
  if (plugin->tx_thread.joinable())
    plugin->tx_thread.join();

  std::lock_guard<std::mutex> lock(plugin->mutex);
  for (auto& [id, conn] : plugin->connections) {
    (void)id;
    conn.state = btcp::TCP_CLOSED;
    if (conn.on_event)
      conn.on_event(conn.user_ctx, conn.conn_id, btcp::TCP_EVENT_RST);
  }
  plugin->connections.clear();
  plugin->listeners.clear();
}

static void tp_set_eth_publisher(void* ctx, BoatEthPublishFn fn,
                                  void* publisher_ctx) {
  auto* plugin = static_cast<btcp::TcpPlugin*>(ctx);
  if (!plugin) return;
  plugin->eth_publish_fn = fn;
  plugin->eth_publisher_ctx = publisher_ctx;
}

static void tp_on_eth_frame(void* ctx, const BoatEthFrame* frame,
                             const char* iface) {
  (void)iface;
  auto* plugin = static_cast<btcp::TcpPlugin*>(ctx);
  if (!plugin || !frame || frame->payload_len < 40) return;

  int af;
  const uint8_t* ip_payload;
  const uint8_t* src_ip;
  const uint8_t* dst_ip;
  uint8_t protocol;

  uint16_t ethertype = frame->ethertype;
  if (ethertype == 0x0800) {
    if (frame->payload_len < 20) return;
    af = AF_INET;
    protocol = frame->payload[9];
    src_ip = frame->payload + 12;
    dst_ip = frame->payload + 16;
    uint8_t ihl = (frame->payload[0] & 0x0F) * 4;
    ip_payload = frame->payload + ihl;
  } else if (ethertype == 0x86DD) {
    if (frame->payload_len < 40) return;
    af = AF_INET6;
    protocol = frame->payload[6];
    src_ip = frame->payload + 8;
    dst_ip = frame->payload + 24;
    uint16_t plen = static_cast<uint16_t>((frame->payload[4] << 8) | frame->payload[5]);
    ip_payload = frame->payload + 40;
    if (plen + 40 > frame->payload_len) return;
    (void)plen;
  } else {
    return;
  }

  if (protocol != 6) return;  // Only TCP
  if (ip_payload + 20 > frame->payload + frame->payload_len) return;

  // Parse TCP header
  uint16_t sport = static_cast<uint16_t>((ip_payload[0] << 8) | ip_payload[1]);
  uint16_t dport = static_cast<uint16_t>((ip_payload[2] << 8) | ip_payload[3]);
  uint32_t seq   = (static_cast<uint32_t>(ip_payload[4]) << 24) |
                   (static_cast<uint32_t>(ip_payload[5]) << 16) |
                   (static_cast<uint32_t>(ip_payload[6]) << 8)  |
                    static_cast<uint32_t>(ip_payload[7]);
  uint32_t ack   = (static_cast<uint32_t>(ip_payload[8]) << 24) |
                   (static_cast<uint32_t>(ip_payload[9]) << 16) |
                   (static_cast<uint32_t>(ip_payload[10]) << 8) |
                    static_cast<uint32_t>(ip_payload[11]);
  uint8_t  flags = ip_payload[13];
  uint8_t  data_off = (ip_payload[12] >> 4) * 4;
  uint32_t tcp_payload_len = (frame->payload + frame->payload_len) -
                             (ip_payload + data_off);
  if (tcp_payload_len > 65535) tcp_payload_len = 0;
  const uint8_t* tcp_data = (tcp_payload_len > 0) ? ip_payload + data_off : nullptr;

  std::lock_guard<std::mutex> lock(plugin->mutex);

  // Match connection by (src_ip, src_port, dst_ip, dst_port) or reverse
  auto match = [&](btcp::TcpConnection& c) -> bool {
    int len = (c.af == AF_INET) ? 4 : 16;
    bool forward = (std::memcmp(c.src_ip.data(), dst_ip, len) == 0 &&
                    c.src_port == dport &&
                    std::memcmp(c.dst_ip.data(), src_ip, len) == 0 &&
                    c.dst_port == sport);
    bool reverse = (std::memcmp(c.src_ip.data(), src_ip, len) == 0 &&
                    c.src_port == sport &&
                    std::memcmp(c.dst_ip.data(), dst_ip, len) == 0 &&
                    c.dst_port == dport);
    return forward || reverse;
  };

  // For server: match incoming SYN against listeners
  if (flags & 0x02) {  // SYN
    for (auto& [lid, listener] : plugin->listeners) {
      (void)lid;
      int len = (listener.af == AF_INET) ? 4 : 16;
      if (std::memcmp(listener.bind_ip.data(), dst_ip, len) == 0 &&
          listener.bind_port == dport) {
        // Create new connection
        btcp::TcpConnection conn;
        conn.conn_id = NextId(plugin);
        conn.listener_id = listener.listener_id;
        conn.af = af;
        std::memcpy(conn.src_ip.data(), dst_ip, (af == AF_INET) ? 4 : 16);
        std::memcpy(conn.dst_ip.data(), src_ip, (af == AF_INET) ? 4 : 16);
        conn.src_port = dport;
        conn.dst_port = sport;
        conn.my_seq = Rand32();
        conn.my_ack = seq + 1;
        conn.their_seq = seq;
        conn.their_ack = ack;
        conn.state = btcp::TCP_SYN_RCVD;
        conn.mss = plugin->default_mss;
        conn.user_ctx = listener.user_ctx;
        conn.on_event = listener.on_event;

        // Send SYN-ACK
        auto mss_opt = btcp::BuildMssOption(static_cast<uint16_t>(conn.mss));
        std::vector<uint8_t> seg;
        if (af == AF_INET) {
          seg = btcp::BuildIp4TcpSegment(
              conn.src_ip.data(), conn.dst_ip.data(),
              conn.src_port, conn.dst_port,
              conn.my_seq, conn.my_ack,
              nullptr, 0,
              btcp::TCP_FLAG_SYN | btcp::TCP_FLAG_ACK, 65535,
              mss_opt.data(), static_cast<uint32_t>(mss_opt.size()));
        } else {
          seg = btcp::BuildIp6TcpSegment(
              conn.src_ip.data(), conn.dst_ip.data(),
              conn.src_port, conn.dst_port,
              conn.my_seq, conn.my_ack,
              nullptr, 0,
              btcp::TCP_FLAG_SYN | btcp::TCP_FLAG_ACK, 65535,
              mss_opt.data(), static_cast<uint32_t>(mss_opt.size()));
        }
        conn.my_seq += 1;
        conn.unacked_segment = seg;
        conn.retransmit_at = std::chrono::steady_clock::now() +
                             std::chrono::milliseconds(plugin->retry_ms);
        conn.retry_count = 0;

        int nid = conn.conn_id;
        plugin->connections[nid] = std::move(conn);

        plugin->mutex.unlock();
        SendRaw(plugin, seg);
        plugin->mutex.lock();
        return;
      }
    }
  }

  // Match against existing connections
  for (auto& [id, conn] : plugin->connections) {
    (void)id;
    if (!match(conn)) continue;

    // Detect direction: are they the source or destination?
    int len = (conn.af == AF_INET) ? 4 : 16;
    bool from_them = (std::memcmp(conn.dst_ip.data(), src_ip, len) == 0 &&
                      conn.dst_port == sport &&
                      std::memcmp(conn.src_ip.data(), dst_ip, len) == 0 &&
                      conn.src_port == dport);

    if (from_them) {
      // Incoming from remote peer
      switch (conn.state) {
        case btcp::TCP_SYN_SENT:
          if (flags & 0x12) {  // SYN-ACK
            conn.their_seq = seq;
            conn.my_ack = seq + 1;
            conn.state = btcp::TCP_ESTABLISHED;
            conn.unacked_segment.clear();
            if (conn.on_event)
              conn.on_event(conn.user_ctx, conn.conn_id,
                            btcp::TCP_EVENT_CONNECTED);
          }
          break;

        case btcp::TCP_ESTABLISHED:
        case btcp::TCP_FIN_WAIT_1:
        case btcp::TCP_FIN_WAIT_2:
          if (flags & 0x10) {  // ACK
            if (conn.unacked_segment.size() >= 4) {
              // Update from ACK field
              (void)ack;
            }
            if (tcp_payload_len > 0 && conn.on_data) {
              conn.on_data(conn.user_ctx, conn.conn_id,
                           tcp_data, tcp_payload_len);
            }
            conn.my_ack = seq + tcp_payload_len;
          }
          if (flags & 0x01 && conn.state == btcp::TCP_ESTABLISHED) {  // FIN
            conn.state = btcp::TCP_CLOSE_WAIT;
            conn.my_ack = seq + 1;
            if (conn.on_event)
              conn.on_event(conn.user_ctx, conn.conn_id,
                            btcp::TCP_EVENT_CLOSED);
          }
          if (flags & 0x04) {  // RST
            conn.state = btcp::TCP_CLOSED;
            if (conn.on_event)
              conn.on_event(conn.user_ctx, conn.conn_id,
                            btcp::TCP_EVENT_RST);
          }
          break;

        case btcp::TCP_SYN_RCVD:
          if (flags & 0x10) {  // ACK for our SYN-ACK
            conn.state = btcp::TCP_ESTABLISHED;
            conn.unacked_segment.clear();
            if (conn.on_event)
              conn.on_event(conn.user_ctx, conn.conn_id,
                            btcp::TCP_EVENT_CONNECTED);
          }
          break;

        case btcp::TCP_LAST_ACK:
          if (flags & 0x10) {  // ACK for our FIN
            conn.state = btcp::TCP_CLOSED;
            if (conn.on_event)
              conn.on_event(conn.user_ctx, conn.conn_id,
                            btcp::TCP_EVENT_CLOSED);
          }
          break;

        default:
          break;
      }
    }
  }
}

// ── ABI exports ────────────────────────────────────────────────────────────

static btcp::TcpPlugin g_plugin;

extern "C" BoatPlugin* boat_plugin_create() {
  static BoatPluginVTable vtable;
  vtable.initialize        = tp_initialize;
  vtable.on_tick           = tp_on_tick;
  vtable.shutdown          = tp_shutdown;
  vtable.set_publisher     = nullptr;
  vtable.set_can_publisher = nullptr;
  vtable.on_can_frame      = nullptr;
  vtable.set_eth_publisher = tp_set_eth_publisher;
  vtable.on_eth_frame      = tp_on_eth_frame;
  vtable.set_bus_publisher = nullptr;
  vtable.set_pdu_publisher = nullptr;

  static BoatPlugin bp;
  bp.vtable = &vtable;
  bp.ctx    = &g_plugin;
  return &bp;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (!plugin) return;
  if (plugin->vtable && plugin->vtable->shutdown) {
    plugin->vtable->shutdown(plugin->ctx);
  }
}

extern "C" uint32_t boat_plugin_abi_version() {
  return BOAT_PLUGIN_ABI_VERSION;
}

// ── C API ──────────────────────────────────────────────────────────────────

extern "C" int tcp_connect(void* ctx, const char* src_ip, uint16_t src_port,
                            const char* dst_ip, uint16_t dst_port) {
  auto* plugin = static_cast<btcp::TcpPlugin*>(ctx);
  if (!plugin) return -1;

  int af = ResolveAf(src_ip);
  if (af == AF_UNSPEC || ResolveAf(dst_ip) == AF_UNSPEC) return -1;

  btcp::TcpConnection conn;
  conn.conn_id = NextId(plugin);
  conn.af = af;
  ParseIp(src_ip, af, conn.src_ip);
  ParseIp(dst_ip, af, conn.dst_ip);
  conn.src_port = src_port;
  conn.dst_port = dst_port;
  conn.my_seq = Rand32();
  conn.my_ack = 0;
  conn.state = btcp::TCP_SYN_SENT;
  conn.mss = plugin->default_mss;

  // Build SYN
  auto mss_opt = btcp::BuildMssOption(static_cast<uint16_t>(conn.mss));
  std::vector<uint8_t> seg;
  if (af == AF_INET) {
    seg = btcp::BuildIp4TcpSegment(
        conn.src_ip.data(), conn.dst_ip.data(),
        conn.src_port, conn.dst_port,
        conn.my_seq, conn.my_ack,
        nullptr, 0,
        btcp::TCP_FLAG_SYN, 65535,
        mss_opt.data(), static_cast<uint32_t>(mss_opt.size()));
  } else {
    seg = btcp::BuildIp6TcpSegment(
        conn.src_ip.data(), conn.dst_ip.data(),
        conn.src_port, conn.dst_port,
        conn.my_seq, conn.my_ack,
        nullptr, 0,
        btcp::TCP_FLAG_SYN, 65535,
        mss_opt.data(), static_cast<uint32_t>(mss_opt.size()));
  }
  conn.my_seq += 1;

  int nid = conn.conn_id;
  {
    std::lock_guard<std::mutex> lock(plugin->mutex);
    conn.unacked_segment = seg;
    conn.retransmit_at = std::chrono::steady_clock::now() +
                         std::chrono::milliseconds(plugin->retry_ms);
    conn.retry_count = 0;
    plugin->connections[nid] = std::move(conn);
  }

  SendRaw(plugin, seg);
  return nid;
}

extern "C" int tcp_listen(void* ctx, const char* bind_ip, uint16_t bind_port) {
  auto* plugin = static_cast<btcp::TcpPlugin*>(ctx);
  if (!plugin) return -1;

  int af = ResolveAf(bind_ip);
  if (af == AF_UNSPEC) return -1;

  btcp::TcpListener listener;
  listener.listener_id = NextId(plugin);
  listener.af = af;
  ParseIp(bind_ip, af, listener.bind_ip);
  listener.bind_port = bind_port;

  int lid = listener.listener_id;
  {
    std::lock_guard<std::mutex> lock(plugin->mutex);
    plugin->listeners[lid] = std::move(listener);
  }
  return lid;
}

extern "C" int tcp_send(void* ctx, int conn_id,
                         const uint8_t* data, uint32_t len) {
  auto* plugin = static_cast<btcp::TcpPlugin*>(ctx);
  if (!plugin) return -1;

  std::lock_guard<std::mutex> lock(plugin->mutex);
  auto it = plugin->connections.find(conn_id);
  if (it == plugin->connections.end()) return -1;
  if (it->second.state != btcp::TCP_ESTABLISHED) return -1;

  it->second.send_buffer.insert(it->second.send_buffer.end(), data, data + len);
  plugin->tx_cv.notify_one();
  return static_cast<int>(len);
}

extern "C" void tcp_set_callbacks(void* ctx, int id,
                                   btcp::TcpOnData on_data,
                                   btcp::TcpOnEvent on_event,
                                   void* user_ctx) {
  auto* plugin = static_cast<btcp::TcpPlugin*>(ctx);
  if (!plugin) return;

  std::lock_guard<std::mutex> lock(plugin->mutex);
  auto cit = plugin->connections.find(id);
  if (cit != plugin->connections.end()) {
    cit->second.on_data = on_data;
    cit->second.on_event = on_event;
    cit->second.user_ctx = user_ctx;
    return;
  }
  auto lit = plugin->listeners.find(id);
  if (lit != plugin->listeners.end()) {
    lit->second.on_event = on_event;
    lit->second.user_ctx = user_ctx;
  }
}

extern "C" int tcp_close(void* ctx, int conn_id) {
  auto* plugin = static_cast<btcp::TcpPlugin*>(ctx);
  if (!plugin) return -1;

  std::lock_guard<std::mutex> lock(plugin->mutex);
  auto it = plugin->connections.find(conn_id);
  if (it == plugin->connections.end()) return -1;

  auto& conn = it->second;
  // Send FIN
  std::vector<uint8_t> seg;
  if (conn.af == AF_INET) {
    seg = btcp::BuildIp4TcpSegment(
        conn.src_ip.data(), conn.dst_ip.data(),
        conn.src_port, conn.dst_port,
        conn.my_seq, conn.my_ack,
        nullptr, 0, btcp::TCP_FLAG_FIN | btcp::TCP_FLAG_ACK, 65535);
  } else {
    seg = btcp::BuildIp6TcpSegment(
        conn.src_ip.data(), conn.dst_ip.data(),
        conn.src_port, conn.dst_port,
        conn.my_seq, conn.my_ack,
        nullptr, 0, btcp::TCP_FLAG_FIN | btcp::TCP_FLAG_ACK, 65535);
  }
  conn.my_seq += 1;
  conn.state = btcp::TCP_FIN_WAIT_1;
  conn.unacked_segment = seg;
  conn.retransmit_at = std::chrono::steady_clock::now() +
                       std::chrono::milliseconds(plugin->retry_ms);
  conn.retry_count = 0;

  plugin->mutex.unlock();
  SendRaw(plugin, seg);
  plugin->mutex.lock();

  return 0;
}

extern "C" int tcp_abort(void* ctx, int conn_id) {
  auto* plugin = static_cast<btcp::TcpPlugin*>(ctx);
  if (!plugin) return -1;

  std::lock_guard<std::mutex> lock(plugin->mutex);
  auto it = plugin->connections.find(conn_id);
  if (it == plugin->connections.end()) return -1;

  auto& conn = it->second;
  std::vector<uint8_t> seg;
  if (conn.af == AF_INET) {
    seg = btcp::BuildIp4TcpSegment(
        conn.src_ip.data(), conn.dst_ip.data(),
        conn.src_port, conn.dst_port,
        conn.my_seq, conn.my_ack,
        nullptr, 0, btcp::TCP_FLAG_RST, 65535);
  } else {
    seg = btcp::BuildIp6TcpSegment(
        conn.src_ip.data(), conn.dst_ip.data(),
        conn.src_port, conn.dst_port,
        conn.my_seq, conn.my_ack,
        nullptr, 0, btcp::TCP_FLAG_RST, 65535);
  }
  conn.state = btcp::TCP_CLOSED;

  plugin->mutex.unlock();
  SendRaw(plugin, seg);
  plugin->mutex.lock();

  return 0;
}

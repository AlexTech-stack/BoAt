#include "tcp_plugin.h"
#include "tcp_segment.h"

#include <arpa/inet.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>

#include <linux/if_packet.h>
#include <net/if_arp.h>
#include <linux/sockios.h>
#ifndef PACKET_IGNORE_OUTGOING
#define PACKET_IGNORE_OUTGOING 23
#endif
#include <net/ethernet.h>
#include <net/if.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

namespace btcp = boat::tcp;

// ── Helpers ────────────────────────────────────────────────────────────────

static std::mt19937& Rng() {
  static std::mt19937 rng(std::random_device{}());
  return rng;
}

static uint32_t Rand32() {
  return Rng()();
}

static int FallbackRawSocket(btcp::TcpPlugin* plugin, const char* iface) {
  if (plugin->raw_sock >= 0) return plugin->raw_sock;
  plugin->raw_sock = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
  if (plugin->raw_sock < 0) return -1;
  // Ignore frames we send ourselves (loopback from same socket)
  const int ignore_out = 1;
  setsockopt(plugin->raw_sock, SOL_PACKET, PACKET_IGNORE_OUTGOING,
             &ignore_out, sizeof(ignore_out));
  if (iface) {
    plugin->raw_ifindex = if_nametoindex(iface);
    if (plugin->raw_ifindex > 0) {
      struct sockaddr_ll bind_addr{};
      bind_addr.sll_family  = AF_PACKET;
      bind_addr.sll_protocol = htons(ETH_P_ALL);
      bind_addr.sll_ifindex  = plugin->raw_ifindex;
      if (bind(plugin->raw_sock, reinterpret_cast<struct sockaddr*>(&bind_addr),
               sizeof(bind_addr)) < 0) {
        std::fprintf(stderr, "[TCP] bind(%s) failed: %s\n",
                     iface, std::strerror(errno));
      }
    }
  }
  return plugin->raw_sock;
}

// Resolve a destination MAC via the kernel ARP cache.
// Returns true if a MAC was found.
static bool ResolveMac(const std::string& iface, const uint8_t* ip_bytes, int af,
                        std::array<uint8_t, 6>& mac_out) {
  int sock = socket(AF_INET, SOCK_DGRAM, 0);
  if (sock < 0) return false;

  struct arpreq arp{};
  struct sockaddr_in* sin = reinterpret_cast<struct sockaddr_in*>(&arp.arp_pa);
  sin->sin_family = AF_INET;
  std::memcpy(&sin->sin_addr, ip_bytes, 4);
  std::strncpy(arp.arp_dev, iface.c_str(), IFNAMSIZ - 1);

  bool ok = (ioctl(sock, SIOCGARP, &arp) == 0);
  ::close(sock);
  if (!ok) return false;

  // Check if we got a valid hardware address
  if (arp.arp_ha.sa_family != ARPHRD_ETHER) return false;
  std::memcpy(mac_out.data(), arp.arp_ha.sa_data, 6);
  return true;
}

static void SendRaw(btcp::TcpPlugin* plugin, const std::vector<uint8_t>& seg) {
  if (plugin->eth_publish_fn) {
    BoatEthFrame ef{};
    std::memset(ef.dst_mac, 0xFF, 6);
    std::memset(ef.src_mac, 0x02, 6);
    ef.src_mac[5] = 0x01;
    ef.ethertype = (seg[0] >> 4 == 6) ? 0x86DD : 0x0800;
    ef.payload = const_cast<uint8_t*>(seg.data());
    ef.payload_len = seg.size();
    plugin->eth_publish_fn(plugin->eth_publisher_ctx, &ef);
    return;
  }
  std::fprintf(stderr, "[TCP-SEND] method=%s seg_size=%zu iface=%s\n",
               plugin->eth_publish_fn ? "gateway" : "raw",
               seg.size(), plugin->raw_iface.c_str());
  // Fallback: send via raw AF_PACKET socket
  if (plugin->raw_iface.empty()) {
    std::fprintf(stderr, "[TCP-SEND] no raw_iface, dropping\n");
    return;
  }
  int sock = FallbackRawSocket(plugin, plugin->raw_iface.c_str());
  if (sock < 0) return;
  struct sockaddr_ll dest{};
  dest.sll_family  = AF_PACKET;
  dest.sll_protocol = htons(ETH_P_ALL);
  if (plugin->raw_ifindex > 0) dest.sll_ifindex = plugin->raw_ifindex;
  dest.sll_halen   = 6;
  // Resolve destination MAC from kernel ARP cache
  int ip_len = (seg[0] >> 4 == 6) ? 16 : 4;
  int dst_off = (seg[0] >> 4 == 6) ? 24 : 16;
  const uint8_t* dst_ip = seg.data() + dst_off;
  std::array<uint8_t, 6> resolved_mac{};
  resolved_mac.fill(0xFF);  // default: broadcast
  {
    std::lock_guard<std::mutex> lock(plugin->arp_mutex);
    std::string ip_key(reinterpret_cast<const char*>(dst_ip), ip_len);
    auto it = plugin->arp_cache.find(ip_key);
    if (it != plugin->arp_cache.end()) {
      resolved_mac = it->second;
    } else if (ip_len == 4) {
      std::array<uint8_t, 6> mac{};
      if (ResolveMac(plugin->raw_iface, dst_ip, AF_INET, mac)) {
        resolved_mac = mac;
        plugin->arp_cache[ip_key] = mac;
      }
    }
  }
  std::memcpy(dest.sll_addr, resolved_mac.data(), 6);
  // Build L2 frame: dst(6) + src(6) + ethertype(2) + ip_payload
  uint8_t buf[64 + 65535];
  std::memcpy(buf, resolved_mac.data(), 6);  // dst MAC
  buf[6] = 0x02; buf[7] = 0x00;          // src MAC = 02:00:00:00:00:01
  buf[11] = 0x01;
  uint16_t etype = (seg[0] >> 4 == 6) ? 0x86DD : 0x0800;
  buf[12] = static_cast<uint8_t>(etype >> 8);
  buf[13] = static_cast<uint8_t>(etype & 0xFF);
  std::memcpy(buf + 14, seg.data(), seg.size());
  size_t total = 14 + seg.size();

  dest.sll_protocol = htons(etype);
  ssize_t sent = sendto(sock, buf, total, 0,
                         reinterpret_cast<struct sockaddr*>(&dest), sizeof(dest));
  if (sent < 0)
    std::fprintf(stderr, "[TCP] sendto failed: %s\n", std::strerror(errno));
  std::fprintf(stderr, "[TCP] sendto: total=%zu sent=%zd\n", total, sent);
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

// ── Frame processing (shared between gateway callback and raw RX) ─────────

static void HandleIncoming(btcp::TcpPlugin* plugin, const uint8_t* payload,
                            size_t payload_len, uint16_t ethertype);

static void ProcessFrame(btcp::TcpPlugin* plugin, const uint8_t* data, size_t len) {
  if (!data || len < 14) return;
  uint16_t etype = static_cast<uint16_t>((data[12] << 8) | data[13]);
  HandleIncoming(plugin, data + 14, len - 14, etype);
}

// ── Raw AF_PACKET RX loop ─────────────────────────────────────────────────

static void RawRxLoop(btcp::TcpPlugin* plugin) {
  uint8_t buf[2048];
  while (plugin->raw_rx_running.load()) {
    struct sockaddr_ll addr;
    socklen_t addr_len = sizeof(addr);
    ssize_t n = recvfrom(plugin->raw_sock, buf, sizeof(buf), 0,
                         reinterpret_cast<struct sockaddr*>(&addr), &addr_len);
    if (n > 0) {
      uint16_t etype = (n >= 14) ? static_cast<uint16_t>((buf[12] << 8) | buf[13]) : 0;
      std::fprintf(stderr, "[TCP-RX] recv %zd bytes etype=0x%04x ifindex=%d\n",
                   n, etype, addr.sll_ifindex);
      std::lock_guard<std::mutex> lock(plugin->mutex);
      ProcessFrame(plugin, buf, static_cast<size_t>(n));
    }
  }
}

// ── Vtable callbacks ──────────────────────────────────────────────────────

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
    // Extract raw iface from config {"iface": "eth0", ...}
    const char* if_key = "\"iface\"";
    const char* if_pos = std::strstr(config_json, if_key);
    if (if_pos) {
      if_pos += std::strlen(if_key);
      while (*if_pos && *if_pos != '"') ++if_pos;
      if (*if_pos == '"') {
        ++if_pos;
        const char* end = if_pos;
        while (*end && *end != '"') ++end;
        plugin->raw_iface.assign(if_pos, end);
      }
    }
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

        // Send pending data (fire-and-forget regardless of state)
        if (!conn.send_buffer.empty()) {
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

  // Start standalone raw socket RX when not wired to the gateway
  std::fprintf(stderr, "[TCP] tp_initialize: eth_publish_fn=%p raw_iface=%s\n",
               (void*)plugin->eth_publish_fn, plugin->raw_iface.c_str());
  if (!plugin->eth_publish_fn && !plugin->raw_iface.empty() &&
      FallbackRawSocket(plugin, plugin->raw_iface.c_str()) >= 0) {
    std::fprintf(stderr, "[TCP] Raw socket opened fd=%d ifindex=%d\n",
                 plugin->raw_sock, plugin->raw_ifindex);
    plugin->raw_rx_running.store(true);
    plugin->raw_rx_thread = std::thread(RawRxLoop, plugin);
    std::fprintf(stderr, "[TCP] Raw RX thread started\n");
  } else {
    std::fprintf(stderr, "[TCP] No raw RX: publish=%p iface=%s\n",
                 (void*)plugin->eth_publish_fn, plugin->raw_iface.c_str());
  }

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
  plugin->raw_rx_running.store(false);
  if (plugin->raw_rx_thread.joinable())
    plugin->raw_rx_thread.join();
  if (plugin->raw_sock >= 0) {
    ::close(plugin->raw_sock);
    plugin->raw_sock = -1;
  }

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

// Internal: process an incoming frame (raw payload + ethertype)
static void HandleIncoming(btcp::TcpPlugin* plugin, const uint8_t* payload,
                            size_t payload_len, uint16_t ethertype) {
  if (!plugin) return;
  if (!payload || payload_len < 40) {
    std::fprintf(stderr, "[TCP-HI] too short: len=%zu plugin=%p\n",
                 payload_len, (void*)plugin);
    return;
  }

  int af;
  const uint8_t* ip_payload;
  const uint8_t* src_ip;
  const uint8_t* dst_ip;
  uint8_t protocol;

  if (ethertype == 0x0800) {
    if (payload_len < 20) return;
    uint16_t ip_total = (static_cast<uint16_t>(payload[2]) << 8) | payload[3];
    if (ip_total < payload_len) payload_len = ip_total;  // clamp out Ethernet padding
    af = AF_INET;
    protocol = payload[9];
    src_ip = payload + 12;
    dst_ip = payload + 16;
    uint8_t ihl = (payload[0] & 0x0F) * 4;
    ip_payload = payload + ihl;
  } else if (ethertype == 0x86DD) {
    if (payload_len < 40) return;
    uint16_t plen = static_cast<uint16_t>((payload[4] << 8) | payload[5]);
    uint16_t ip6_total = plen + 40;
    if (ip6_total < payload_len) payload_len = ip6_total;  // clamp out Ethernet padding
    if (plen + 40 > payload_len) return;
    af = AF_INET6;
    protocol = payload[6];
    src_ip = payload + 8;
    dst_ip = payload + 24;
    ip_payload = payload + 40;
  } else {
    return;
  }

  std::fprintf(stderr, "[TCP-HI] proto=%d len=%zu etype=0x%04x plugin=%p\n",
               protocol, payload_len, ethertype, (void*)plugin);
  if (protocol != 6) return;  // Only TCP
  if (ip_payload + 20 > payload + payload_len) {
    std::fprintf(stderr, "[TCP-HI] ip_payload out of bounds\n");
    return;
  }

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
  std::fprintf(stderr, "[TCP-PARSE] sport=%u dport=%u flags=0x%02x data_off=%u\n",
               sport, dport, flags, (ip_payload[12] >> 4) * 4);
  uint8_t  data_off = (ip_payload[12] >> 4) * 4;
  uint32_t tcp_payload_len = (payload + payload_len) - (ip_payload + data_off);
  if (tcp_payload_len > 65535) tcp_payload_len = 0;
  const uint8_t* tcp_data = (tcp_payload_len > 0) ? ip_payload + data_off : nullptr;

  // NOTE: Caller must hold plugin->mutex when calling this function.
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
  // Pure SYN (no ACK): check for duplicates and match against listeners.
  // SYN-ACK (flags & 0x12 == 0x12) is handled below in the connection match loop.
  if ((flags & 0x02) && !(flags & 0x10)) {
    bool already_exists = false;
    for (auto& [eid, econn] : plugin->connections) {
      (void)eid;
      int elen = (econn.af == AF_INET) ? 4 : 16;
      if (econn.state == btcp::TCP_SYN_RCVD || econn.state == btcp::TCP_SYN_SENT) {
        if (std::memcmp(econn.dst_ip.data(), src_ip, elen) == 0 &&
            econn.dst_port == sport &&
            std::memcmp(econn.src_ip.data(), dst_ip, elen) == 0 &&
            econn.src_port == dport) {
          already_exists = true;
          break;
        }
      }
    }
    if (already_exists) goto tcp_rx_done;

    for (auto& [lid, listener] : plugin->listeners) {
      (void)lid;
      int len = (listener.af == AF_INET) ? 4 : 16;
      bool ip_match = (std::memcmp(listener.bind_ip.data(), dst_ip, len) == 0);
      bool port_match = (listener.bind_port == dport);
      std::fprintf(stderr, "[TCP-LISTEN] check listener lid=%d ip_match=%d port_match=%d conns=%zu listeners=%zu plugin=%p\n",
                   lid, ip_match, port_match,
                   plugin->connections.size(), plugin->listeners.size(),
                   (void*)plugin);
      if (ip_match && port_match) {
        std::fprintf(stderr, "[TCP-LISTEN] MATCH on %d! Creating connection\n", lid);
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
        conn.on_data  = listener.on_data;
        conn.on_event = listener.on_event;
        std::fprintf(stderr, "[TCP] accepted conn on_data=%p on_event=%p\n",
                     (void*)conn.on_data, (void*)conn.on_event);

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
  if (plugin->connections.empty())
    std::fprintf(stderr, "[TCP-MATCH] no connections on plugin=%p\n", (void*)plugin);
  for (auto& [id, conn] : plugin->connections) {
    (void)id;
    if (!match(conn)) continue;

    std::fprintf(stderr, "[TCP-MATCH] matched cid=%d state=%d\n", id, conn.state);
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
            // Send pure ACK immediately to complete the 3-way handshake
            {
              std::vector<uint8_t> ack_seg;
              if (af == AF_INET) {
                ack_seg = btcp::BuildIp4TcpSegment(
                    conn.src_ip.data(), conn.dst_ip.data(),
                    conn.src_port, conn.dst_port,
                    conn.my_seq, conn.my_ack,
                    nullptr, 0,
                    btcp::TCP_FLAG_ACK, 65535);
              } else {
                ack_seg = btcp::BuildIp6TcpSegment(
                    conn.src_ip.data(), conn.dst_ip.data(),
                    conn.src_port, conn.dst_port,
                    conn.my_seq, conn.my_ack,
                    nullptr, 0,
                    btcp::TCP_FLAG_ACK, 65535);
              }
              plugin->mutex.unlock();
              SendRaw(plugin, ack_seg);
              plugin->mutex.lock();
            }
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
              std::fprintf(stderr, "[TCP] calling on_data for cid=%d len=%u\n",
                           conn.conn_id, tcp_payload_len);
              conn.on_data(conn.user_ctx, conn.conn_id,
                           tcp_data, tcp_payload_len);
            }
            conn.my_ack = seq + tcp_payload_len;
          }
          if (flags & 0x01) {  // FIN
            if (conn.state == btcp::TCP_ESTABLISHED) {
              conn.state = btcp::TCP_CLOSE_WAIT;
              conn.my_ack = seq + 1;
              if (conn.on_event)
                conn.on_event(conn.user_ctx, conn.conn_id,
                              btcp::TCP_EVENT_CLOSED);
            } else if (conn.state == btcp::TCP_FIN_WAIT_1 ||
                       conn.state == btcp::TCP_FIN_WAIT_2) {
              conn.my_ack = seq + 1;
              // Send ACK for the remote FIN
              std::vector<uint8_t> a_seg;
              if (af == AF_INET) {
                a_seg = btcp::BuildIp4TcpSegment(
                    conn.src_ip.data(), conn.dst_ip.data(),
                    conn.src_port, conn.dst_port,
                    conn.my_seq, conn.my_ack,
                    nullptr, 0, btcp::TCP_FLAG_ACK, 65535);
              } else {
                a_seg = btcp::BuildIp6TcpSegment(
                    conn.src_ip.data(), conn.dst_ip.data(),
                    conn.src_port, conn.dst_port,
                    conn.my_seq, conn.my_ack,
                    nullptr, 0, btcp::TCP_FLAG_ACK, 65535);
              }
              plugin->mutex.unlock();
              SendRaw(plugin, a_seg);
              plugin->mutex.lock();
              conn.state = btcp::TCP_TIME_WAIT;
              if (conn.on_event)
                conn.on_event(conn.user_ctx, conn.conn_id,
                              btcp::TCP_EVENT_CLOSED);
            }
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
          if (flags & 0x04) {  // RST
            conn.state = btcp::TCP_CLOSED;
            if (conn.on_event)
              conn.on_event(conn.user_ctx, conn.conn_id,
                            btcp::TCP_EVENT_RST);
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
  tcp_rx_done:;
}

// Vtable-compatible wrapper for gateway dispatch
static void tp_on_eth_frame(void* ctx, const BoatEthFrame* frame,
                             const char* iface) {
  (void)iface;
  auto* plugin = static_cast<btcp::TcpPlugin*>(ctx);
  if (!plugin || !frame) return;
  std::lock_guard<std::mutex> lock(plugin->mutex);
  HandleIncoming(plugin, frame->payload, frame->payload_len, frame->ethertype);
}

// ── ABI exports ────────────────────────────────────────────────────────────

extern "C" BoatPlugin* boat_plugin_create() {
  auto* plugin = new btcp::TcpPlugin();
  auto* vtable = new BoatPluginVTable();
  vtable->initialize        = tp_initialize;
  vtable->on_tick           = tp_on_tick;
  vtable->shutdown          = tp_shutdown;
  vtable->set_publisher     = nullptr;
  vtable->set_can_publisher = nullptr;
  vtable->on_can_frame      = nullptr;
  vtable->set_eth_publisher = tp_set_eth_publisher;
  vtable->on_eth_frame      = tp_on_eth_frame;
  vtable->set_bus_publisher = nullptr;
  vtable->set_pdu_publisher = nullptr;

  auto* bp = new BoatPlugin();
  bp->vtable = vtable;
  bp->ctx    = plugin;
  return bp;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (!plugin) return;
  if (plugin->vtable && plugin->vtable->shutdown) {
    plugin->vtable->shutdown(plugin->ctx);
  }
  delete static_cast<btcp::TcpPlugin*>(plugin->ctx);
  delete plugin->vtable;
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() {
  return BOAT_PLUGIN_ABI_VERSION;
}

// ── C API ──────────────────────────────────────────────────────────────────

extern "C" int tcp_connect(void* ctx, const char* src_ip, uint16_t src_port,
                            const char* dst_ip, uint16_t dst_port,
                            btcp::TcpOnData on_data,
                            btcp::TcpOnEvent on_event,
                            void* user_ctx) {
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

  std::fprintf(stderr, "[TCP-CONNECT] sending SYN on %s plugin=%p\n",
               plugin->raw_iface.c_str(), (void*)plugin);
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
  conn.on_data  = on_data;
  conn.on_event = on_event;
  conn.user_ctx = user_ctx;
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

extern "C" int tcp_listen(void* ctx, const char* bind_ip, uint16_t bind_port,
                           btcp::TcpOnData on_data,
                           btcp::TcpOnEvent on_event,
                           void* user_ctx) {
  auto* plugin = static_cast<btcp::TcpPlugin*>(ctx);
  if (!plugin) return -1;

  int af = ResolveAf(bind_ip);
  if (af == AF_UNSPEC) return -1;

  btcp::TcpListener listener;
  listener.listener_id = NextId(plugin);
  listener.af = af;
  ParseIp(bind_ip, af, listener.bind_ip);
  listener.bind_port = bind_port;
  listener.on_data = on_data;
  listener.on_event = on_event;
  listener.user_ctx = user_ctx;
  std::fprintf(stderr, "[TCP] tcp_listen: on_data=%p on_event=%p\n",
               (void*)on_data, (void*)on_event);

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
  // Send even if connection is closing/RST'd (fire-and-forget)
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

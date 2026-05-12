#include "ethernet/virtual_ethernet_driver.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <stdexcept>

namespace boat::hil {

// ── Serialisation constants ─────────────────────────────────────────────────
// Header: [6 src_mac][6 dst_mac][2 ethertype BE][2 payload_len BE] = 16 bytes
static constexpr std::size_t kHeaderSize = 16;
static constexpr std::size_t kMaxPayload = 1500;
static constexpr std::size_t kMaxDgram   = kHeaderSize + kMaxPayload;

// ── Construction ─────────────────────────────────────────────────────────────

VirtualEthernetDriver::VirtualEthernetDriver(std::string   iface,
                                             std::string   mcast_addr,
                                             std::uint16_t port)
    : iface_(std::move(iface)),
      mcast_addr_(std::move(mcast_addr)),
      port_(port) {}

/*static*/
std::unique_ptr<VirtualEthernetDriver> VirtualEthernetDriver::FromIndex(
    const std::string& iface, std::size_t index) {
  // 239.255.0.(index+1)  capped at 239.255.0.254
  const std::size_t octet = std::min<std::size_t>(index + 1, 254);
  const std::string addr  = "239.255.0." + std::to_string(octet);
  const std::uint16_t port = static_cast<std::uint16_t>(51000 + index);
  return std::make_unique<VirtualEthernetDriver>(iface, addr, port);
}

// ── Lifecycle ────────────────────────────────────────────────────────────────

bool VirtualEthernetDriver::Open() {
  if (open_.load()) {
    return true;
  }

  sock_ = socket(AF_INET, SOCK_DGRAM, 0);
  if (sock_ < 0) {
    return false;
  }

  // Allow multiple processes to bind the same port (needed on the same host).
  const int reuse = 1;
  setsockopt(sock_, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
#ifdef SO_REUSEPORT
  setsockopt(sock_, SOL_SOCKET, SO_REUSEPORT, &reuse, sizeof(reuse));
#endif

  // Bind to INADDR_ANY:port so we receive multicast datagrams.
  struct sockaddr_in addr{};
  addr.sin_family      = AF_INET;
  addr.sin_port        = htons(port_);
  addr.sin_addr.s_addr = INADDR_ANY;
  if (bind(sock_, reinterpret_cast<struct sockaddr*>(&addr), sizeof(addr)) < 0) {
    ::close(sock_);
    sock_ = -1;
    return false;
  }

  // Join the multicast group.
  struct ip_mreq mreq{};
  inet_pton(AF_INET, mcast_addr_.c_str(), &mreq.imr_multiaddr);
  mreq.imr_interface.s_addr = INADDR_ANY;
  if (setsockopt(sock_, IPPROTO_IP, IP_ADD_MEMBERSHIP, &mreq, sizeof(mreq)) < 0) {
    ::close(sock_);
    sock_ = -1;
    return false;
  }

  // Do NOT enable multicast loopback: the registry dispatches sent frames
  // directly to gRPC subscribers via EthernetBusRegistry::DispatchRx, so
  // socket loopback is not needed and would cause every sent frame to be
  // double-delivered (once via DispatchRx, once via the rx_thread read-back).
  // Other processes' sockets on the same host still receive the multicast.
  const unsigned char loop = 0;
  setsockopt(sock_, IPPROTO_IP, IP_MULTICAST_LOOP, &loop, sizeof(loop));

  // Keep TTL local to avoid leaking onto the network.
  const unsigned char ttl = 1;
  setsockopt(sock_, IPPROTO_IP, IP_MULTICAST_TTL, &ttl, sizeof(ttl));

  open_.store(true);
  return true;
}

void VirtualEthernetDriver::Close() {
  if (open_.exchange(false)) {
    if (sock_ >= 0) {
      ::close(sock_);
      sock_ = -1;
    }
  }
}

// ── I/O ──────────────────────────────────────────────────────────────────────

bool VirtualEthernetDriver::ReadFrame(EthernetFrame& out) {
  if (!open_.load() || sock_ < 0) {
    return false;
  }

  unsigned char buf[kMaxDgram];
  const ssize_t n = recvfrom(sock_, buf, sizeof(buf), 0, nullptr, nullptr);
  if (n < static_cast<ssize_t>(kHeaderSize)) {
    return false;
  }

  std::memcpy(out.src_mac,      buf,     6);
  std::memcpy(out.dst_mac,      buf + 6, 6);
  out.ethertype  = (static_cast<uint16_t>(buf[12]) << 8) | buf[13];
  const uint16_t payload_len =
      (static_cast<uint16_t>(buf[14]) << 8) | buf[15];

  const std::size_t body = static_cast<std::size_t>(n) - kHeaderSize;
  const std::size_t copy_len = std::min<std::size_t>(payload_len, body);
  out.payload.assign(buf + kHeaderSize, buf + kHeaderSize + copy_len);
  out.timestamp_ns = 0;  // filled by the registry
  return true;
}

bool VirtualEthernetDriver::WriteFrame(const EthernetFrame& frame) {
  if (!open_.load() || sock_ < 0) {
    return false;
  }

  const std::size_t payload_len =
      std::min<std::size_t>(frame.payload.size(), kMaxPayload);

  unsigned char buf[kMaxDgram];
  std::memcpy(buf,     frame.src_mac, 6);
  std::memcpy(buf + 6, frame.dst_mac, 6);
  buf[12] = static_cast<unsigned char>(frame.ethertype >> 8);
  buf[13] = static_cast<unsigned char>(frame.ethertype & 0xFF);
  buf[14] = static_cast<unsigned char>(payload_len >> 8);
  buf[15] = static_cast<unsigned char>(payload_len & 0xFF);
  if (payload_len > 0) {
    std::memcpy(buf + kHeaderSize, frame.payload.data(), payload_len);
  }

  struct sockaddr_in dest{};
  dest.sin_family = AF_INET;
  dest.sin_port   = htons(port_);
  inet_pton(AF_INET, mcast_addr_.c_str(), &dest.sin_addr);

  const ssize_t sent = sendto(sock_, buf, kHeaderSize + payload_len, 0,
                               reinterpret_cast<struct sockaddr*>(&dest),
                               sizeof(dest));
  return sent == static_cast<ssize_t>(kHeaderSize + payload_len);
}

}  // namespace boat::hil

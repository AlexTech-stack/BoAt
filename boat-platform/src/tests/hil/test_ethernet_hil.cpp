// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include <catch2/catch_test_macros.hpp>

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <thread>
#include <vector>

#include "ethernet/ethernet_frame.h"
#include "ethernet/virtual_ethernet_driver.h"
#include "ethernet_bus_registry.h"

using namespace boat::hil;

namespace {

/* A bare multicast sender for the group/port FromIndex(_, index) listens on,
 * with IP_MULTICAST_LOOP *enabled*.
 *
 * The driver deliberately disables loopback on its own socket, so two driver
 * instances in one process can never reach each other -- a send is delivered
 * only to other machines. A test that pointed one driver at another therefore
 * could not pass, and blocked forever instead of failing. Driving ReadFrame()
 * from a sender we control tests the half that has no other coverage: parsing
 * the on-wire header. EthernetBusRegistry's own tests below bypass the socket
 * entirely ("SendFrame delivers via DispatchRx directly").
 */
class LoopbackMulticastSender {
 public:
  explicit LoopbackMulticastSender(std::size_t index) {
    sock_ = socket(AF_INET, SOCK_DGRAM, 0);
    const unsigned char loop = 1;
    setsockopt(sock_, IPPROTO_IP, IP_MULTICAST_LOOP, &loop, sizeof(loop));
    addr_.sin_family = AF_INET;
    addr_.sin_port   = htons(static_cast<std::uint16_t>(51000 + index));
    inet_pton(AF_INET, ("239.255.0." + std::to_string(index + 1)).c_str(),
              &addr_.sin_addr);
  }
  ~LoopbackMulticastSender() { if (sock_ >= 0) ::close(sock_); }

  bool valid() const { return sock_ >= 0; }

  bool Send(const std::vector<unsigned char>& datagram) const {
    return sendto(sock_, datagram.data(), datagram.size(), 0,
                  reinterpret_cast<const struct sockaddr*>(&addr_),
                  sizeof(addr_)) == static_cast<ssize_t>(datagram.size());
  }

 private:
  int                sock_{-1};
  struct sockaddr_in addr_{};
};

/* Serialise to the wire layout documented on VirtualEthernetDriver:
 * [6] src_mac, [6] dst_mac, [2] ethertype BE, [2] payload_len BE, [N] payload.
 * Written out by hand rather than reusing the driver's own packer, so a bug in
 * that packer cannot cancel out against the parser under test. */
std::vector<unsigned char> Serialise(const unsigned char (&src)[6],
                                     const unsigned char (&dst)[6],
                                     std::uint16_t ethertype,
                                     const std::vector<unsigned char>& payload) {
  std::vector<unsigned char> d;
  d.insert(d.end(), src, src + 6);
  d.insert(d.end(), dst, dst + 6);
  d.push_back(static_cast<unsigned char>(ethertype >> 8));
  d.push_back(static_cast<unsigned char>(ethertype & 0xFF));
  d.push_back(static_cast<unsigned char>(payload.size() >> 8));
  d.push_back(static_cast<unsigned char>(payload.size() & 0xFF));
  d.insert(d.end(), payload.begin(), payload.end());
  return d;
}

/* ReadFrame() returns false on its 100 ms SO_RCVTIMEO, so poll a bounded
 * number of times instead of assuming the first call wins. Bounded on purpose:
 * this test used to be an unbounded blocking read, which hung CI rather than
 * failing it. */
bool ReadFrameWithin(VirtualEthernetDriver& drv, EthernetFrame& out,
                     int attempts = 20) {
  for (int i = 0; i < attempts; ++i) {
    if (drv.ReadFrame(out)) return true;
  }
  return false;
}

}  // namespace

TEST_CASE("Virtual Ethernet HIL: ReadFrame parses an untagged frame off the wire",
          "[hil][ethernet]") {
  const char* enabled = std::getenv("BOAT_HIL_ENABLED");
  if (enabled == nullptr || *enabled == '\0') {
    SKIP("BOAT_HIL_ENABLED not set");
  }

  auto rx = VirtualEthernetDriver::FromIndex("veth_test", 7);
  REQUIRE(rx->Open());

  LoopbackMulticastSender sender(7);
  REQUIRE(sender.valid());

  const unsigned char src[6] = {0xAA, 0xBB, 0x01, 0x02, 0x03, 0x04};
  const unsigned char dst[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
  const std::vector<unsigned char> payload = {0xDE, 0xAD, 0xBE, 0xEF};
  REQUIRE(sender.Send(Serialise(src, dst, 0x88B5, payload)));

  EthernetFrame received;
  REQUIRE(ReadFrameWithin(*rx, received));
  REQUIRE(received.ethertype  == 0x88B5);
  REQUIRE(received.payload    == payload);
  REQUIRE(received.src_mac[0] == 0xAA);
  REQUIRE(received.src_mac[1] == 0xBB);
  REQUIRE(received.dst_mac[0] == 0xFF);
  REQUIRE(received.vlan_id    == 0);
  REQUIRE(received.vlan_pcp   == 0);
  // ReadFrame() deliberately leaves timestamp_ns at 0 -- "filled by the
  // registry" per its own comment -- and this test calls it directly,
  // bypassing EthernetBusRegistry. The registry's fill-if-zero behavior has
  // its own coverage below ("registry dispatches RX frame to subscriber").
  REQUIRE(received.timestamp_ns == 0);

  rx->Close();
}

TEST_CASE("Virtual Ethernet HIL: ReadFrame unpacks an 802.1Q tagged frame",
          "[hil][ethernet]") {
  const char* enabled = std::getenv("BOAT_HIL_ENABLED");
  if (enabled == nullptr || *enabled == '\0') {
    SKIP("BOAT_HIL_ENABLED not set");
  }

  auto rx = VirtualEthernetDriver::FromIndex("veth_vlan", 10);
  REQUIRE(rx->Open());

  LoopbackMulticastSender sender(10);
  REQUIRE(sender.valid());

  // 802.1Q: outer ethertype 0x8100, then TCI (PCP in bits 15..13, VID in 11..0)
  // and the inner ethertype, before payload_len.
  const unsigned char src[6] = {0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F};
  const unsigned char dst[6] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66};
  const std::uint16_t vid = 0x064;  // 100
  const std::uint8_t  pcp = 5;
  const std::uint16_t tci = static_cast<std::uint16_t>((pcp << 13) | vid);
  const std::vector<unsigned char> payload = {0x01, 0x02, 0x03};

  std::vector<unsigned char> d;
  d.insert(d.end(), src, src + 6);
  d.insert(d.end(), dst, dst + 6);
  d.push_back(0x81); d.push_back(0x00);                                  // 0x8100
  d.push_back(static_cast<unsigned char>(tci >> 8));
  d.push_back(static_cast<unsigned char>(tci & 0xFF));
  d.push_back(0x08); d.push_back(0x00);                                  // inner: IPv4
  d.push_back(static_cast<unsigned char>(payload.size() >> 8));
  d.push_back(static_cast<unsigned char>(payload.size() & 0xFF));
  d.insert(d.end(), payload.begin(), payload.end());
  REQUIRE(sender.Send(d));

  EthernetFrame received;
  REQUIRE(ReadFrameWithin(*rx, received));
  REQUIRE(received.ethertype == 0x0800);  // inner, not the 0x8100 tag
  REQUIRE(received.vlan_id   == vid);
  REQUIRE(received.vlan_pcp  == pcp);
  REQUIRE(received.payload   == payload);

  rx->Close();
}

TEST_CASE("Virtual Ethernet HIL: a send is not looped back to this host",
          "[hil][ethernet]") {
  const char* enabled = std::getenv("BOAT_HIL_ENABLED");
  if (enabled == nullptr || *enabled == '\0') {
    SKIP("BOAT_HIL_ENABLED not set");
  }

  // Pins the invariant the IP_MULTICAST_LOOP=0 in Open() exists to protect:
  // a frame written by one driver must not come back to a driver on the same
  // host, or EthernetBusRegistry -- which already dispatches sent frames via
  // DispatchRx -- would deliver every frame twice.
  auto rx = VirtualEthernetDriver::FromIndex("veth_noloop", 11);
  auto tx = VirtualEthernetDriver::FromIndex("veth_noloop", 11);
  REQUIRE(rx->Open());
  REQUIRE(tx->Open());

  EthernetFrame sent;
  sent.ethertype = 0x88B5;
  sent.payload   = {0x01, 0x02};
  REQUIRE(tx->WriteFrame(sent));

  EthernetFrame received;
  REQUIRE_FALSE(ReadFrameWithin(*rx, received, 3));

  tx->Close();
  rx->Close();
}

TEST_CASE("Virtual Ethernet HIL: registry dispatches RX frame to subscriber", "[hil][ethernet]") {
  const char* enabled = std::getenv("BOAT_HIL_ENABLED");
  if (enabled == nullptr || *enabled == '\0') {
    SKIP("BOAT_HIL_ENABLED not set");
  }

  EthernetBusRegistry registry;
  REQUIRE(registry.Add("veth_reg",
    VirtualEthernetDriver::FromIndex("veth_reg", 8)));

  std::atomic<int>  hits{0};
  EthernetFrame     last_rx;

  registry.Subscribe("veth_reg", 0,
    [&](const EthernetFrame& f, const std::string&) {
      last_rx = f;
      ++hits;
    });

  // SendFrame delivers via DispatchRx directly (no multicast loopback needed).
  EthernetFrame f;
  f.ethertype = 0x0800;
  f.payload   = {0x01, 0x02, 0x03};
  REQUIRE(registry.SendFrame("veth_reg", f));

  REQUIRE(hits == 1);
  REQUIRE(last_rx.ethertype == 0x0800);
  REQUIRE(last_rx.payload   == f.payload);
}

TEST_CASE("Virtual Ethernet HIL: ethertype filter in registry", "[hil][ethernet]") {
  const char* enabled = std::getenv("BOAT_HIL_ENABLED");
  if (enabled == nullptr || *enabled == '\0') {
    SKIP("BOAT_HIL_ENABLED not set");
  }

  EthernetBusRegistry registry;
  REQUIRE(registry.Add("veth_filt",
    VirtualEthernetDriver::FromIndex("veth_filt", 9)));

  int ipv4_hits = 0, all_hits = 0;
  registry.Subscribe("", 0x0800, [&](const EthernetFrame&, const std::string&) { ++ipv4_hits; });
  registry.Subscribe("", 0,      [&](const EthernetFrame&, const std::string&) { ++all_hits; });

  EthernetFrame f;
  f.ethertype = 0x0800;
  registry.SendFrame("veth_filt", f);

  f.ethertype = 0x0806;
  registry.SendFrame("veth_filt", f);

  REQUIRE(ipv4_hits == 1);
  REQUIRE(all_hits  == 2);
}

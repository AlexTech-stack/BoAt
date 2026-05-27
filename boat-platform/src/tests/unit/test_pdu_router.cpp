#include <catch2/catch_test_macros.hpp>

#include <chrono>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include "core/event/event_bus.h"
#include "can_bus_registry.h"
#include "ethernet_bus_registry.h"
#include "hal/hal_driver.h"
#include "ethernet/ethernet_frame.h"
#include "pdu/ipdumcontainer.h"
#include "pdu/pdu_router.h"
#include "pdu/pdu_types.h"

using namespace boat::hil;

// ── Mock drivers ──────────────────────────────────────────────────────────────

class MockCanDriver : public IHalDriver {
 public:
  bool Open()  override { return true; }
  void Close() override {}
  bool ReadFrame(CanFrame&) override {
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    return false;
  }
  bool WriteFrame(const CanFrame& f) override {
    written.push_back(f);
    return true;
  }
  std::vector<CanFrame> written;
};

class MockEthernetDriver : public IEthernetDriver {
 public:
  bool Open()  override { return true; }
  void Close() override {}
  bool ReadFrame(EthernetFrame&) override {
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    return false;
  }
  bool WriteFrame(const EthernetFrame& f) override {
    written.push_back(f);
    return true;
  }
  std::vector<EthernetFrame> written;
};

// ── Fixtures ──────────────────────────────────────────────────────────────────

struct Fixture {
  MockCanDriver*      mock_can  = nullptr;
  MockEthernetDriver* mock_eth  = nullptr;
  boat::core::EventBus event_bus;
  CanBusRegistry      can_reg;
  EthernetBusRegistry eth_reg;
  PduRouter           router{can_reg, eth_reg};

  Fixture() {
    auto can_drv = std::make_shared<MockCanDriver>();
    mock_can = can_drv.get();
    can_reg.Add("vcan0", std::move(can_drv), event_bus);

    auto eth_drv = std::make_unique<MockEthernetDriver>();
    mock_eth = eth_drv.get();
    eth_reg.Add("veth0", std::move(eth_drv));
  }
};

// ── Tests ─────────────────────────────────────────────────────────────────────

TEST_CASE("PduRouter SendPdu over CAN writes correct frame", "[unit][pdu]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x100;
  route.transport = PduTransport::kCan;
  route.iface     = "vcan0";
  f.router.AddRoute(route);

  const std::vector<uint8_t> payload = {0x01, 0x02, 0x03};
  REQUIRE(f.router.SendPdu(0x100, payload));
  REQUIRE(f.mock_can->written.size() == 1);
  const auto& fr = f.mock_can->written[0];
  REQUIRE(fr.can_id == 0x100);
  REQUIRE(fr.dlc    == 3);
  REQUIRE(fr.data[0] == 0x01);
  REQUIRE(fr.data[2] == 0x03);
}

TEST_CASE("PduRouter SendPdu over CAN uses explicit can_id", "[unit][pdu]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x200;
  route.transport = PduTransport::kCan;
  route.iface     = "vcan0";
  route.can_id    = 0x7FF;
  f.router.AddRoute(route);

  REQUIRE(f.router.SendPdu(0x200, {0xAB}));
  REQUIRE(f.mock_can->written[0].can_id == 0x7FF);
}

TEST_CASE("PduRouter SendPdu returns false for unknown pdu_id", "[unit][pdu]") {
  Fixture f;
  REQUIRE_FALSE(f.router.SendPdu(0x999, {0x00}));
}

TEST_CASE("PduRouter SendPdu over Ethernet frames pdu_id as 4-byte big-endian header",
          "[unit][pdu]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x00AA0001;
  route.transport = PduTransport::kEthernet;
  route.iface     = "veth0";
  route.ethertype = 0x88B5;
  route.vlan_id   = 0;
  f.router.AddRoute(route);

  const std::vector<uint8_t> payload = {0xDE, 0xAD};
  REQUIRE(f.router.SendPdu(0x00AA0001, payload));
  REQUIRE(f.mock_eth->written.size() == 1);
  const auto& fr = f.mock_eth->written[0];
  REQUIRE(fr.ethertype == 0x88B5);
  REQUIRE(fr.payload.size() == 6);  // 4-byte header + 2-byte payload
  REQUIRE(fr.payload[0] == 0x00);
  REQUIRE(fr.payload[1] == 0xAA);
  REQUIRE(fr.payload[2] == 0x00);
  REQUIRE(fr.payload[3] == 0x01);
  REQUIRE(fr.payload[4] == 0xDE);
  REQUIRE(fr.payload[5] == 0xAD);
}

TEST_CASE("PduRouter AddRoute defaults ethertype to 0x88B5", "[unit][pdu]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x300;
  route.transport = PduTransport::kEthernet;
  route.iface     = "veth0";
  route.ethertype = 0;  // leave unset
  f.router.AddRoute(route);

  const auto routes = f.router.ListRoutes();
  REQUIRE(routes.size() == 1);
  REQUIRE(routes[0].ethertype == 0x88B5);
}

TEST_CASE("PduRouter Subscribe receives CAN PDU", "[unit][pdu]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x100;
  route.transport = PduTransport::kCan;
  route.iface     = "vcan0";
  f.router.AddRoute(route);

  std::vector<PduFrame> received;
  f.router.Subscribe({0x100}, [&](const PduFrame& pdu) {
    received.push_back(pdu);
  });

  // Inject a CAN frame directly via the registry.
  CanFrame cf{};
  cf.can_id = 0x100;
  cf.dlc    = 2;
  cf.data[0] = 0xBE;
  cf.data[1] = 0xEF;
  f.can_reg.SendFrame("vcan0", cf);

  REQUIRE(received.size() == 1);
  REQUIRE(received[0].pdu_id == 0x100);
  REQUIRE(received[0].payload.size() == 2);
  REQUIRE(received[0].payload[0] == 0xBE);
  REQUIRE(received[0].source == PduTransport::kCan);
}

TEST_CASE("PduRouter Subscribe receives Ethernet PDU", "[unit][pdu]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x00AA0001;
  route.transport = PduTransport::kEthernet;
  route.iface     = "";           // accept any iface
  route.ethertype = 0x88B5;
  route.vlan_id   = 0;
  f.router.AddRoute(route);

  std::vector<PduFrame> received;
  f.router.Subscribe({0x00AA0001}, [&](const PduFrame& pdu) {
    received.push_back(pdu);
  });

  EthernetFrame ef;
  ef.ethertype = 0x88B5;
  ef.vlan_id   = 0;
  ef.payload   = {0x00, 0xAA, 0x00, 0x01, 0xCA, 0xFE};  // pdu_id header + data
  f.eth_reg.SendFrame("veth0", ef);

  REQUIRE(received.size() == 1);
  REQUIRE(received[0].pdu_id == 0x00AA0001);
  REQUIRE(received[0].payload.size() == 2);
  REQUIRE(received[0].payload[0] == 0xCA);
  REQUIRE(received[0].payload[1] == 0xFE);
  REQUIRE(received[0].source == PduTransport::kEthernet);
}

TEST_CASE("PduRouter wildcard subscriber receives all PDUs", "[unit][pdu]") {
  Fixture f;
  PduRoute r1; r1.pdu_id = 0x01; r1.transport = PduTransport::kCan; r1.iface = "vcan0";
  PduRoute r2; r2.pdu_id = 0x02; r2.transport = PduTransport::kCan; r2.iface = "vcan0";
  f.router.AddRoute(r1);
  f.router.AddRoute(r2);

  int count = 0;
  f.router.Subscribe({}, [&](const PduFrame&) { ++count; });

  CanFrame cf1{}; cf1.can_id = 0x01; cf1.dlc = 1; f.can_reg.SendFrame("vcan0", cf1);
  CanFrame cf2{}; cf2.can_id = 0x02; cf2.dlc = 1; f.can_reg.SendFrame("vcan0", cf2);
  REQUIRE(count == 2);
}

TEST_CASE("PduRouter Unsubscribe stops delivery", "[unit][pdu]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x100;
  route.transport = PduTransport::kCan;
  route.iface     = "vcan0";
  f.router.AddRoute(route);

  int count = 0;
  const auto sid = f.router.Subscribe({0x100}, [&](const PduFrame&) { ++count; });

  CanFrame cf{}; cf.can_id = 0x100; cf.dlc = 1;
  f.can_reg.SendFrame("vcan0", cf);
  REQUIRE(count == 1);

  f.router.Unsubscribe(sid);
  f.can_reg.SendFrame("vcan0", cf);
  REQUIRE(count == 1);  // no second delivery
}

TEST_CASE("PduRouter ListRoutes returns all configured routes", "[unit][pdu]") {
  Fixture f;
  PduRoute r1; r1.pdu_id = 0x10; r1.transport = PduTransport::kCan;      r1.iface = "vcan0";
  PduRoute r2; r2.pdu_id = 0x20; r2.transport = PduTransport::kEthernet; r2.iface = "veth0";
  f.router.AddRoute(r1);
  f.router.AddRoute(r2);

  const auto routes = f.router.ListRoutes();
  REQUIRE(routes.size() == 2);
}

// ── IpduM container unit tests ────────────────────────────────────────────────

TEST_CASE("IpduMSerialize / IpduMDeserialize round-trip single PDU", "[unit][ipdumcontainer]") {
  const IpduMEntry entry{0x00AA0001, {0x01, 0x02, 0x03}};
  const auto buf = IpduMSerialize({entry});

  // Header: 4 bytes ID + 4 bytes DLC = 8 bytes; payload: 3 bytes
  REQUIRE(buf.size() == 11);
  // PDU ID big-endian
  REQUIRE(buf[0] == 0x00);
  REQUIRE(buf[1] == 0xAA);
  REQUIRE(buf[2] == 0x00);
  REQUIRE(buf[3] == 0x01);
  // DLC big-endian
  REQUIRE(buf[4] == 0x00);
  REQUIRE(buf[5] == 0x00);
  REQUIRE(buf[6] == 0x00);
  REQUIRE(buf[7] == 0x03);

  std::vector<IpduMEntry> out;
  REQUIRE(IpduMDeserialize(buf.data(), buf.size(), out));
  REQUIRE(out.size() == 1);
  REQUIRE(out[0].pdu_id == 0x00AA0001);
  REQUIRE(out[0].payload == std::vector<uint8_t>({0x01, 0x02, 0x03}));
}

TEST_CASE("IpduMSerialize / IpduMDeserialize round-trip multiple PDUs", "[unit][ipdumcontainer]") {
  const std::vector<IpduMEntry> entries = {
      {0x00000001, {0xAA, 0xBB}},
      {0x00000002, {0xCC}},
      {0x00000003, {0xDE, 0xAD, 0xBE, 0xEF}},
  };
  const auto buf = IpduMSerialize(entries);

  std::vector<IpduMEntry> out;
  REQUIRE(IpduMDeserialize(buf.data(), buf.size(), out));
  REQUIRE(out.size() == 3);
  REQUIRE(out[0].pdu_id == 0x00000001);
  REQUIRE(out[1].pdu_id == 0x00000002);
  REQUIRE(out[2].payload == std::vector<uint8_t>({0xDE, 0xAD, 0xBE, 0xEF}));
}

TEST_CASE("IpduMDeserialize rejects truncated header", "[unit][ipdumcontainer]") {
  // Only 5 bytes — not enough for an 8-byte header
  const uint8_t buf[] = {0x00, 0x00, 0x00, 0x01, 0x00};
  std::vector<IpduMEntry> out;
  REQUIRE_FALSE(IpduMDeserialize(buf, sizeof(buf), out));
}

TEST_CASE("IpduMDeserialize rejects truncated payload", "[unit][ipdumcontainer]") {
  // Header claims DLC=10 but only 2 payload bytes follow
  const uint8_t buf[] = {
      0x00, 0x00, 0x00, 0x01,  // PDU ID
      0x00, 0x00, 0x00, 0x0A,  // DLC = 10
      0xAA, 0xBB               // only 2 bytes
  };
  std::vector<IpduMEntry> out;
  REQUIRE_FALSE(IpduMDeserialize(buf, sizeof(buf), out));
}

// ── IP/UDP/IpduM send path tests ──────────────────────────────────────────────

TEST_CASE("PduRouter SendPdu over IPv4/UDP builds correct Ethernet frame", "[unit][pdu][ipdumcontainer]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x00AA0001;
  route.transport = PduTransport::kEthernet;
  route.iface     = "veth0";
  route.src_ip    = {10, 0, 0, 1};
  route.dst_ip    = {10, 0, 0, 2};
  route.src_port  = 1234;
  route.dst_port  = 5678;
  route.ttl       = 64;
  f.router.AddRoute(route);

  REQUIRE(f.router.SendPdu(0x00AA0001, {0xDE, 0xAD}));
  REQUIRE(f.mock_eth->written.size() == 1);

  const auto& fr = f.mock_eth->written[0];
  REQUIRE(fr.ethertype == 0x0800);  // IPv4

  // Parse the IP/UDP/IpduM content
  uint16_t sp = 0, dp = 0;
  std::vector<IpduMEntry> entries;
  REQUIRE(ParseUdpIpPacket(fr.payload.data(), fr.payload.size(), &sp, &dp, entries));
  REQUIRE(sp == 1234);
  REQUIRE(dp == 5678);
  REQUIRE(entries.size() == 1);
  REQUIRE(entries[0].pdu_id == 0x00AA0001);
  REQUIRE(entries[0].payload == std::vector<uint8_t>({0xDE, 0xAD}));
}

TEST_CASE("PduRouter SendPdu over IPv6/UDP builds correct Ethernet frame", "[unit][pdu][ipdumcontainer]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x00BB0001;
  route.transport = PduTransport::kEthernet;
  route.iface     = "veth0";
  // ::1 → ::2
  route.src_ip = {0,0,0,0, 0,0,0,0, 0,0,0,0, 0,0,0,1};
  route.dst_ip = {0,0,0,0, 0,0,0,0, 0,0,0,0, 0,0,0,2};
  route.src_port = 4000;
  route.dst_port = 5000;
  route.ttl      = 128;
  f.router.AddRoute(route);

  REQUIRE(f.router.SendPdu(0x00BB0001, {0xCA, 0xFE}));
  REQUIRE(f.mock_eth->written.size() == 1);

  const auto& fr = f.mock_eth->written[0];
  REQUIRE(fr.ethertype == 0x86DD);  // IPv6

  uint16_t sp = 0, dp = 0;
  std::vector<IpduMEntry> entries;
  REQUIRE(ParseUdpIpPacket(fr.payload.data(), fr.payload.size(), &sp, &dp, entries));
  REQUIRE(sp == 4000);
  REQUIRE(dp == 5000);
  REQUIRE(entries.size() == 1);
  REQUIRE(entries[0].pdu_id == 0x00BB0001);
  REQUIRE(entries[0].payload == std::vector<uint8_t>({0xCA, 0xFE}));
}

TEST_CASE("PduRouter receives PDU from IPv4/UDP/IpduM Ethernet frame", "[unit][pdu][ipdumcontainer]") {
  Fixture f;
  PduRoute route;
  route.pdu_id    = 0x00AA0001;
  route.transport = PduTransport::kEthernet;
  route.iface     = "";
  route.dst_port  = 5678;
  route.src_ip    = {10, 0, 0, 1};
  route.dst_ip    = {10, 0, 0, 2};
  f.router.AddRoute(route);

  std::vector<PduFrame> received;
  f.router.Subscribe({0x00AA0001}, [&](const PduFrame& p) { received.push_back(p); });

  // Build a real IP/UDP/IpduM frame and inject it
  const auto container = IpduMSerialize({{0x00AA0001, {0x11, 0x22}}});
  const uint8_t src4[4] = {10, 0, 0, 1};
  const uint8_t dst4[4] = {10, 0, 0, 2};
  const auto ip_pkt = BuildUdpIpv4(src4, dst4, 1234, 5678, 64, container);

  EthernetFrame ef;
  ef.ethertype = 0x0800;
  ef.payload   = ip_pkt;
  f.eth_reg.SendFrame("veth0", ef);

  REQUIRE(received.size() == 1);
  REQUIRE(received[0].pdu_id == 0x00AA0001);
  REQUIRE(received[0].payload == std::vector<uint8_t>({0x11, 0x22}));
}

TEST_CASE("PduRouter receives multiple PDUs from one IpduM container", "[unit][pdu][ipdumcontainer]") {
  Fixture f;
  PduRoute r1; r1.pdu_id = 0x01; r1.transport = PduTransport::kEthernet;
               r1.dst_ip = {10,0,0,2}; r1.dst_port = 9000;
  PduRoute r2; r2.pdu_id = 0x02; r2.transport = PduTransport::kEthernet;
               r2.dst_ip = {10,0,0,2}; r2.dst_port = 9000;
  f.router.AddRoute(r1);
  f.router.AddRoute(r2);

  std::vector<uint32_t> ids;
  f.router.Subscribe({}, [&](const PduFrame& p) { ids.push_back(p.pdu_id); });

  // Pack two PDUs into one container
  const auto container = IpduMSerialize({{0x01, {0xAA}}, {0x02, {0xBB}}});
  const uint8_t src4[4] = {10,0,0,1};
  const uint8_t dst4[4] = {10,0,0,2};
  const auto ip_pkt = BuildUdpIpv4(src4, dst4, 0, 9000, 64, container);

  EthernetFrame ef;
  ef.ethertype = 0x0800;
  ef.payload   = ip_pkt;
  f.eth_reg.SendFrame("veth0", ef);

  REQUIRE(ids.size() == 2);
  REQUIRE((ids[0] == 0x01 || ids[0] == 0x02));
  REQUIRE((ids[1] == 0x01 || ids[1] == 0x02));
  REQUIRE(ids[0] != ids[1]);
}

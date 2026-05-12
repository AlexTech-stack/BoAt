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

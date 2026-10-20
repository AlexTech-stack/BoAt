// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <chrono>
#include <future>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include "ethernet_bus_registry.h"
#include "ethernet/ethernet_frame.h"

using namespace boat::hil;

// ── Mock driver ───────────────────────────────────────────────────────────────
// Sleeps briefly in ReadFrame so the registry's RX thread doesn't busy-spin.

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

// ── Helpers ───────────────────────────────────────────────────────────────────

static EthernetFrame make_frame(uint16_t ethertype, uint8_t byte = 0xAB) {
  EthernetFrame f;
  f.src_mac[0] = 0xAA;
  f.dst_mac[0] = 0xFF;
  f.ethertype  = ethertype;
  f.payload    = {byte};
  return f;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

TEST_CASE("EthernetBusRegistry SendFrame dispatches to subscriber", "[unit][ethernet]") {
  EthernetBusRegistry registry;
  REQUIRE(registry.Add("veth0", std::make_unique<MockEthernetDriver>()));

  int hits = 0;
  registry.Subscribe("veth0", 0, [&](const EthernetFrame&, const std::string& iface) {
    REQUIRE(iface == "veth0");
    ++hits;
  });

  REQUIRE(registry.SendFrame("veth0", make_frame(0x0800)));
  REQUIRE(hits == 1);
}

TEST_CASE("EthernetBusRegistry SendFrame returns false for unknown iface", "[unit][ethernet]") {
  EthernetBusRegistry registry;
  REQUIRE_FALSE(registry.SendFrame("veth99", make_frame(0x0800)));
}

TEST_CASE("EthernetBusRegistry iface filter excludes non-matching iface", "[unit][ethernet]") {
  EthernetBusRegistry registry;
  REQUIRE(registry.Add("veth0", std::make_unique<MockEthernetDriver>()));
  REQUIRE(registry.Add("veth1", std::make_unique<MockEthernetDriver>()));

  int hits = 0;
  registry.Subscribe("veth0", 0, [&](const EthernetFrame&, const std::string&) { ++hits; });

  registry.SendFrame("veth0", make_frame(0x0800));
  registry.SendFrame("veth1", make_frame(0x0800));
  REQUIRE(hits == 1);
}

TEST_CASE("EthernetBusRegistry ethertype filter passes only matching type", "[unit][ethernet]") {
  EthernetBusRegistry registry;
  REQUIRE(registry.Add("veth0", std::make_unique<MockEthernetDriver>()));

  int ipv4 = 0, arp = 0;
  registry.Subscribe("", 0x0800, [&](const EthernetFrame&, const std::string&) { ++ipv4; });
  registry.Subscribe("", 0x0806, [&](const EthernetFrame&, const std::string&) { ++arp; });

  registry.SendFrame("veth0", make_frame(0x0800));
  registry.SendFrame("veth0", make_frame(0x0806));
  registry.SendFrame("veth0", make_frame(0x86DD));  // IPv6 — neither filter matches

  REQUIRE(ipv4 == 1);
  REQUIRE(arp  == 1);
}

TEST_CASE("EthernetBusRegistry wildcard subscriber receives all ethertypes", "[unit][ethernet]") {
  EthernetBusRegistry registry;
  REQUIRE(registry.Add("veth0", std::make_unique<MockEthernetDriver>()));

  int hits = 0;
  registry.Subscribe("", 0, [&](const EthernetFrame&, const std::string&) { ++hits; });

  registry.SendFrame("veth0", make_frame(0x0800));
  registry.SendFrame("veth0", make_frame(0x0806));
  registry.SendFrame("veth0", make_frame(0x86DD));
  REQUIRE(hits == 3);
}

TEST_CASE("EthernetBusRegistry unsubscribe stops delivery", "[unit][ethernet]") {
  EthernetBusRegistry registry;
  REQUIRE(registry.Add("veth0", std::make_unique<MockEthernetDriver>()));

  int hits = 0;
  const auto id = registry.Subscribe("", 0,
    [&](const EthernetFrame&, const std::string&) { ++hits; });

  registry.SendFrame("veth0", make_frame(0x0800));
  REQUIRE(hits == 1);

  registry.Unsubscribe(id);
  registry.SendFrame("veth0", make_frame(0x0800));
  REQUIRE(hits == 1);  // unchanged after unsubscribe
}

TEST_CASE("EthernetBusRegistry multiple subscribers all receive the frame", "[unit][ethernet]") {
  EthernetBusRegistry registry;
  REQUIRE(registry.Add("veth0", std::make_unique<MockEthernetDriver>()));

  int a = 0, b = 0, c = 0;
  registry.Subscribe("", 0, [&](const EthernetFrame&, const std::string&) { ++a; });
  registry.Subscribe("", 0, [&](const EthernetFrame&, const std::string&) { ++b; });
  registry.Subscribe("", 0, [&](const EthernetFrame&, const std::string&) { ++c; });

  registry.SendFrame("veth0", make_frame(0x0800));
  REQUIRE(a == 1);
  REQUIRE(b == 1);
  REQUIRE(c == 1);
}

TEST_CASE("EthernetBusRegistry SendFrame delivers frame payload correctly", "[unit][ethernet]") {
  EthernetBusRegistry registry;
  REQUIRE(registry.Add("veth0", std::make_unique<MockEthernetDriver>()));

  EthernetFrame received;
  registry.Subscribe("", 0, [&](const EthernetFrame& f, const std::string&) { received = f; });

  EthernetFrame sent = make_frame(0x88B5, 0xDE);
  sent.payload = {0xDE, 0xAD, 0xBE, 0xEF};
  registry.SendFrame("veth0", sent);

  REQUIRE(received.ethertype == 0x88B5);
  REQUIRE(received.payload   == std::vector<uint8_t>{0xDE, 0xAD, 0xBE, 0xEF});
}

TEST_CASE("EthernetBusRegistry Has and Interfaces reflect registered ifaces", "[unit][ethernet]") {
  EthernetBusRegistry registry;
  REQUIRE_FALSE(registry.Has("veth0"));

  registry.Add("veth0", std::make_unique<MockEthernetDriver>());
  registry.Add("veth1", std::make_unique<MockEthernetDriver>());

  REQUIRE(registry.Has("veth0"));
  REQUIRE(registry.Has("veth1"));
  REQUIRE_FALSE(registry.Has("veth2"));
  REQUIRE(registry.Interfaces().size() == 2);
}

TEST_CASE("EthernetBusRegistry SendFrameAll reaches all registered ifaces", "[unit][ethernet]") {
  EthernetBusRegistry registry;
  REQUIRE(registry.Add("veth0", std::make_unique<MockEthernetDriver>()));
  REQUIRE(registry.Add("veth1", std::make_unique<MockEthernetDriver>()));

  int hits = 0;
  registry.Subscribe("", 0, [&](const EthernetFrame&, const std::string&) { ++hits; });

  registry.SendFrameAll(make_frame(0x0800));
  REQUIRE(hits == 2);
}

namespace {

/* Produces one frame, then idles. The RX callback blocks so StopAll must join
   a thread that may call SendFrame from inside the callback. */
class OneFrameEthernetDriver : public IEthernetDriver {
 public:
  bool Open() override { return true; }
  void Close() override {}
  bool ReadFrame(EthernetFrame& frame) override {
    if (!produced.exchange(true)) {
      frame = EthernetFrame{};
      frame.ethertype = 0x0800;
      frame.payload = {0xAA};
      return true;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    return false;
  }
  bool WriteFrame(const EthernetFrame&) override { return true; }

 private:
  std::atomic<bool> produced{false};
};

}  // namespace

TEST_CASE("EthernetBusRegistry StopAll joins without holding the send mutex",
          "[unit][ethernet]") {
  EthernetBusRegistry registry;

  std::atomic<bool> entered{false};
  std::atomic<bool> release{false};
  std::atomic<bool> sent{false};

  registry.Subscribe("", 0, [&](const EthernetFrame&, const std::string&) {
    entered.store(true);
    while (!release.load(std::memory_order_relaxed)) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    if (!sent.exchange(true)) {
      (void)registry.SendFrame("veth9", make_frame(0x0800));
    }
  });

  // Subscribe before Add. Add starts the RX thread, which can deliver the
  // driver's single frame before a subscription made afterwards exists.
  REQUIRE(registry.Add("veth9", std::make_unique<OneFrameEthernetDriver>()));

  for (int i = 0; i < 500 && !entered.load(); ++i) {
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
  }
  REQUIRE(entered.load());

  auto stopping = std::async(std::launch::async, [&]() { registry.StopAll(); });
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  release.store(true);

  REQUIRE(stopping.wait_for(std::chrono::seconds(5)) == std::future_status::ready);
  stopping.get();

  // Idempotent: the destructor calls it again.
  registry.StopAll();
}

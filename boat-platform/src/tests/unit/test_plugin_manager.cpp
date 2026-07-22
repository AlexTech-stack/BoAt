#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <atomic>
#include <thread>
#include <vector>

#include "core/can_tp_interface.h"
#include "plugin/plugin_manager.h"

// A minimal mock plugin .so built by the test harness (see CMakeLists.txt).
// If MOCK_PLUGIN_SO is not defined the load tests are skipped.
#ifndef MOCK_PLUGIN_SO
#define MOCK_PLUGIN_SO ""
#endif

TEST_CASE("PluginManager safe behavior with no plugins", "[unit][plugin_manager]") {
  boat::core::PluginManager manager;

  SECTION("List is empty on initialization") { REQUIRE(manager.List().empty()); }

  SECTION("Unload unknown name is safe") {
    manager.Unload("does-not-exist");
    REQUIRE(manager.List().empty());
  }

  SECTION("TickAll with zero plugins is a no-op") {
    manager.TickAll(123);
    REQUIRE(manager.List().empty());
  }
}

TEST_CASE("PluginManager thread safety under concurrent access", "[unit][plugin_manager]") {
  boat::core::PluginManager manager;

  // Wire a no-op publisher so the setter path is exercised
  manager.SetPublisher([](const char*, std::uint64_t, double) {});
  manager.SetFramePublisher([](const BoatFrame&) {});
  manager.SetBusPublisher([](const char*, double) {});
  manager.SetPduPublisher([](const BoatPduFrame&) {});

  std::atomic<bool> done{false};

  // Background thread continuously calls TickAll
  std::thread ticker([&]() {
    while (!done.load(std::memory_order_acquire)) {
      manager.TickAll(1);
      manager.DispatchFrame(BoatFrame{});
    }
  });

  // Foreground thread loads and unloads repeatedly via ShutdownAll
  // (which uses Unload internally) and List
  for (int i = 0; i < 100; ++i) {
    // Load a dummy handle to populate the map (simulating load without real .so)
    // We cannot call Load without a real .so, so we exercise ShutdownAll/List
    // on an empty map — the main goal is to exercise the mutex paths.
    manager.ShutdownAll();
    auto names = manager.List();
    (void)names;
  }

  done.store(true, std::memory_order_release);
  ticker.join();
  REQUIRE(manager.List().empty());
}

#ifdef PDU_ROUTER_SO
TEST_CASE("PluginManager auto-registers and unregisters a plugin's exported service",
          "[unit][plugin_manager]") {
  // Exercises the real boat_plugin_service_name/boat_plugin_service_ptr
  // dlsym-based auto-registration path end-to-end, using the actual
  // pdu_router.so built by this same build -- this is the exact mechanism
  // that fixes PduService's gRPC RPCs previously always returning NOT_FOUND.
  boat::core::PluginManager manager;

  REQUIRE(manager.FindService("pdu_router") == nullptr);

  manager.Load(PDU_ROUTER_SO, "{}");
  REQUIRE(manager.FindService("pdu_router") != nullptr);

  // Unload must remove the registration too, or FindService would hand out
  // a dangling pointer into the now-destroyed plugin.
  manager.Unload(PDU_ROUTER_SO);
  REQUIRE(manager.FindService("pdu_router") == nullptr);
}

TEST_CASE("PluginManager::Unload does not erase a newer plugin's live service registration",
          "[unit][plugin_manager]") {
  // Regression test for a compare-and-erase bug: if a second, newer plugin
  // instance re-registers the same service name (e.g. two CanTp instances
  // both exporting "can_tp" before iface-scoping, or any two plugins that
  // happen to share a service name), unloading the *original* stale handle
  // must not delete the newer, still-live registration. Simulate "a newer
  // instance registered over this name" by directly calling the public
  // RegisterService() API after a real Load() -- Unload()'s own bookkeeping
  // (registered_services) still only knows about the original pointer, so
  // this exercises the exact mismatch the fix guards against.
  boat::core::PluginManager manager;

  manager.Load(PDU_ROUTER_SO, "{}");
  REQUIRE(manager.FindService("pdu_router") != nullptr);

  int newer_instance_marker = 0;
  void* newer_ptr = &newer_instance_marker;
  manager.RegisterService("pdu_router", newer_ptr);
  REQUIRE(manager.FindService("pdu_router") == newer_ptr);

  // Unloading the original (stale) handle must not erase the newer
  // registration it no longer owns.
  manager.Unload(PDU_ROUTER_SO);
  REQUIRE(manager.FindService("pdu_router") == newer_ptr);
}
#endif

#ifdef CAN_TP_SO
TEST_CASE("PluginManager keys plugins by so_path+iface, not so_path alone",
          "[unit][plugin_manager]") {
  // Two CanTp instances loaded from the same .so with different "iface"
  // config must get distinct entries (and distinct service registrations,
  // via the ctx-aware boat_plugin_service_name) instead of the second
  // silently overwriting the first.
  boat::core::PluginManager manager;

  manager.Load(CAN_TP_SO, R"({"iface":"vcan0"})");
  manager.Load(CAN_TP_SO, R"({"iface":"vcan1"})");

  REQUIRE(manager.List().size() == 2);
  REQUIRE(manager.FindService("can_tp:vcan0") != nullptr);
  REQUIRE(manager.FindService("can_tp:vcan1") != nullptr);
  REQUIRE(manager.FindService("can_tp:vcan0") != manager.FindService("can_tp:vcan1"));

  auto services = manager.ListServices();
  REQUIRE(std::find(services.begin(), services.end(), "can_tp:vcan0") != services.end());
  REQUIRE(std::find(services.begin(), services.end(), "can_tp:vcan1") != services.end());
}

TEST_CASE("PluginManager still collides two instances with an identical iface",
          "[unit][plugin_manager]") {
  // Two instances configured for the *same* iface are a real bus-level
  // conflict, not just a bookkeeping one -- colliding (second overwrites
  // first) is the intended, correct behavior here.
  boat::core::PluginManager manager;

  manager.Load(CAN_TP_SO, R"({"iface":"vcan0"})");
  manager.Load(CAN_TP_SO, R"({"iface":"vcan0"})");

  REQUIRE(manager.List().size() == 1);
}

TEST_CASE("CanTp triggers a segmented send from a matching PDU-bus frame",
          "[unit][plugin_manager][can_tp]") {
  boat::core::PluginManager manager;

  std::vector<std::vector<uint8_t>> published_payloads;
  manager.SetFramePublisher([&](const BoatFrame& f) {
    published_payloads.emplace_back(f.payload, f.payload + f.payload_len);
  });

  manager.Load(CAN_TP_SO, R"({"iface":"vcan0"})");
  auto* can_tp = static_cast<boat::core::ICanTp*>(manager.FindService("can_tp:vcan0"));
  REQUIRE(can_tp != nullptr);

  CanTpConfig cfg{};
  cfg.nsdu_id = 0x100;
  cfg.source_addr = 0x100;
  cfg.target_addr = 0x200;
  cfg.can_dlc = 8;
  REQUIRE(can_tp->Configure(cfg) == 0);

  const std::vector<uint8_t> payload = {0xAA, 0xBB};
  BoatFrame pdu_frame{};
  pdu_frame.bus_type = BOAT_BUS_PDU;
  pdu_frame.iface = "vcan0";  // must match to avoid the self-echo guard
  pdu_frame.meta.pdu.pdu_id = 0x100;
  pdu_frame.payload = const_cast<uint8_t*>(payload.data());
  pdu_frame.payload_len = payload.size();

  manager.DispatchFrame(pdu_frame);

  REQUIRE(published_payloads.size() == 1);
  // PCI 0x02 (Single Frame, len 2) + payload, padded to can_dlc=8 with 0x55.
  REQUIRE(published_payloads[0] ==
          std::vector<uint8_t>{0x02, 0xAA, 0xBB, 0x55, 0x55, 0x55, 0x55, 0x55});
}

TEST_CASE("CanTp ignores PDU-bus frames with no iface or a mismatched iface",
          "[unit][plugin_manager][can_tp]") {
  // No iface set is exactly the shape of this plugin's own RX-reassembly
  // echo (main.cpp's PduPublisher wiring never sets one) -- must not
  // trigger a send, or a completed reassembly would immediately
  // re-segment and retransmit itself.
  boat::core::PluginManager manager;

  int publish_count = 0;
  manager.SetFramePublisher([&](const BoatFrame&) { ++publish_count; });

  manager.Load(CAN_TP_SO, R"({"iface":"vcan0"})");
  auto* can_tp = static_cast<boat::core::ICanTp*>(manager.FindService("can_tp:vcan0"));
  REQUIRE(can_tp != nullptr);

  CanTpConfig cfg{};
  cfg.nsdu_id = 0x100;
  cfg.source_addr = 0x100;
  cfg.target_addr = 0x200;
  cfg.can_dlc = 8;
  REQUIRE(can_tp->Configure(cfg) == 0);

  const std::vector<uint8_t> payload = {0xAA, 0xBB};

  SECTION("no iface at all") {
    BoatFrame pdu_frame{};
    pdu_frame.bus_type = BOAT_BUS_PDU;
    pdu_frame.iface = nullptr;
    pdu_frame.meta.pdu.pdu_id = 0x100;
    pdu_frame.payload = const_cast<uint8_t*>(payload.data());
    pdu_frame.payload_len = payload.size();
    manager.DispatchFrame(pdu_frame);
  }

  SECTION("mismatched iface") {
    BoatFrame pdu_frame{};
    pdu_frame.bus_type = BOAT_BUS_PDU;
    pdu_frame.iface = "vcan1";
    pdu_frame.meta.pdu.pdu_id = 0x100;
    pdu_frame.payload = const_cast<uint8_t*>(payload.data());
    pdu_frame.payload_len = payload.size();
    manager.DispatchFrame(pdu_frame);
  }

  REQUIRE(publish_count == 0);
}

TEST_CASE("CanTp ListSessions reports configured connections and their state",
          "[unit][plugin_manager][can_tp]") {
  boat::core::PluginManager manager;
  manager.Load(CAN_TP_SO, R"({"iface":"vcan0"})");
  auto* can_tp = static_cast<boat::core::ICanTp*>(manager.FindService("can_tp:vcan0"));
  REQUIRE(can_tp != nullptr);

  REQUIRE(can_tp->ListSessions().empty());

  CanTpConfig cfg{};
  cfg.nsdu_id = 0x7E0;
  cfg.source_addr = 0x7E0;
  cfg.target_addr = 0x7E8;
  cfg.can_dlc = 8;
  cfg.block_size = 4;
  cfg.st_min = 10;
  REQUIRE(can_tp->Configure(cfg) == 0);

  auto sessions = can_tp->ListSessions();
  REQUIRE(sessions.size() == 1);
  REQUIRE(sessions[0].nsdu_id == 0x7E0);
  REQUIRE(sessions[0].source_addr == 0x7E0);
  REQUIRE(sessions[0].target_addr == 0x7E8);
  REQUIRE(sessions[0].block_size == 4);
  REQUIRE(sessions[0].st_min == 10);
  REQUIRE(sessions[0].can_dlc == 8);
  REQUIRE(sessions[0].rx_state == "IDLE");
  REQUIRE(sessions[0].tx_state == "IDLE");

  // A second connection on the same instance shows up alongside the first.
  CanTpConfig cfg2{};
  cfg2.nsdu_id = 0x300;
  cfg2.source_addr = 0x300;
  cfg2.target_addr = 0x400;
  cfg2.can_dlc = 8;
  REQUIRE(can_tp->Configure(cfg2) == 0);
  REQUIRE(can_tp->ListSessions().size() == 2);
}
#endif

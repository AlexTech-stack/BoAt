#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <thread>
#include <vector>

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
  manager.SetCanPublisher([](const BoatCanFrame&, const std::string&) {});
  manager.SetEthPublisher([](const BoatEthFrame&) {});
  manager.SetBusPublisher([](const char*, double) {});
  manager.SetPduPublisher([](const BoatPduFrame&) {});

  std::atomic<bool> done{false};

  // Background thread continuously calls TickAll
  std::thread ticker([&]() {
    while (!done.load(std::memory_order_acquire)) {
      manager.TickAll(1);
      manager.DispatchCanFrame(BoatCanFrame{}, "vcan0");
      manager.DispatchEthFrame(BoatEthFrame{}, "veth0");
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

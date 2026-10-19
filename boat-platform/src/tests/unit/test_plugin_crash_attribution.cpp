// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* Plugin crash attribution (issue #19), the parts that can be checked without
   crashing anything: the image registry PluginManager maintains, and the
   per-thread callback marker. The fatal-signal handler itself is exercised in
   boat_integration_plugin_crash, which spawns processes that really die. */

#include <catch2/catch_test_macros.hpp>

#include <dlfcn.h>

#include <algorithm>
#include <cstdint>
#include <string>
#include <vector>

#include "plugin/crash_attribution.h"
#include "plugin/plugin_manager.h"

#ifndef CRASH_PLUGIN_SO
#error "CRASH_PLUGIN_SO must point at the crash_plugin fixture"
#endif
#ifndef STALE_ABI_PLUGIN_SO
#error "STALE_ABI_PLUGIN_SO must point at the stale_abi_plugin fixture"
#endif

namespace crash = boat::core::crash;

namespace {

const std::string kSo = CRASH_PLUGIN_SO;

bool Contains(const std::vector<std::string>& v, const std::string& s) {
  return std::find(v.begin(), v.end(), s) != v.end();
}

/* An address inside the already-loaded fixture. RTLD_NOLOAD only takes an
   extra reference to the existing mapping, which the dlclose drops again. */
std::uintptr_t FixtureAddress() {
  void* h = dlopen(kSo.c_str(), RTLD_NOW | RTLD_NOLOAD);
  REQUIRE(h != nullptr);
  void* sym = dlsym(h, "boat_plugin_create");
  dlclose(h);
  REQUIRE(sym != nullptr);
  return reinterpret_cast<std::uintptr_t>(sym);
}

[[gnu::noinline]] int HostFunction(int x) { return x + 1; }

}  // namespace

TEST_CASE("Crash attribution registers a loaded plugin and forgets it on unload",
          "[unit][plugin_crash]") {
  boat::core::PluginManager manager;
  manager.Load(kSo, R"({"mode":"none"})");

  REQUIRE(Contains(crash::RegisteredKeys(), kSo));
  const std::uintptr_t addr = FixtureAddress();
  REQUIRE(Contains(crash::AttributeAddress(addr), kSo));

  manager.Unload(kSo);
  REQUIRE_FALSE(Contains(crash::RegisteredKeys(), kSo));
  // The mapping is gone, and so is any claim on its old addresses.
  REQUIRE_FALSE(Contains(crash::AttributeAddress(addr), kSo));
}

TEST_CASE("Crash attribution maps a shared .so to every instance using it",
          "[unit][plugin_crash]") {
  boat::core::PluginManager manager;
  manager.Load(kSo, R"({"mode":"none","iface":"vcan0"})");
  manager.Load(kSo, R"({"mode":"none","iface":"vcan1"})");
  const std::string key0 = kSo + "?iface=vcan0";
  const std::string key1 = kSo + "?iface=vcan1";

  // Two instances, one dlopen mapping: an address alone cannot tell them apart.
  const std::uintptr_t addr = FixtureAddress();
  auto keys = crash::AttributeAddress(addr);
  REQUIRE(Contains(keys, key0));
  REQUIRE(Contains(keys, key1));

  manager.Unload(key0);
  keys = crash::AttributeAddress(addr);
  REQUIRE_FALSE(Contains(keys, key0));
  REQUIRE(Contains(keys, key1));
}

TEST_CASE("Crash attribution never blames a plugin for host code",
          "[unit][plugin_crash]") {
  boat::core::PluginManager manager;
  manager.Load(kSo, R"({"mode":"none"})");
  REQUIRE(HostFunction(1) == 2);
  REQUIRE(crash::AttributeAddress(reinterpret_cast<std::uintptr_t>(&HostFunction)).empty());
  REQUIRE(crash::AttributeAddress(0).empty());
}

TEST_CASE("A full crash-attribution registry degrades attribution, not loading",
          "[unit][plugin_crash]") {
  struct Fillers {
    std::vector<int> slots;
    ~Fillers() {
      for (int s : slots) crash::UnregisterPluginImage(s);
    }
  } fillers;
  for (int i = 0; i <= crash::kMaxPluginImages; ++i) {
    const int s = crash::RegisterPluginImage("filler-" + std::to_string(i), "filler", nullptr);
    if (s < 0) break;
    fillers.slots.push_back(s);
  }
  REQUIRE(fillers.slots.size() <= static_cast<std::size_t>(crash::kMaxPluginImages));
  REQUIRE(crash::RegisterPluginImage("one-too-many", "filler", nullptr) == -1);

  boat::core::PluginManager manager;
  REQUIRE_NOTHROW(manager.Load(kSo, R"({"mode":"none"})"));
  REQUIRE(Contains(manager.List(), kSo));
  REQUIRE_FALSE(Contains(crash::RegisteredKeys(), kSo));
  manager.TickAll(1);
  manager.Unload(kSo);
  REQUIRE(manager.List().empty());
}

TEST_CASE("The callback marker names the plugin inside on_tick and nests",
          "[unit][plugin_crash]") {
  const int nested_slot = crash::RegisterPluginImage("nested-test", "nested", nullptr);
  REQUIRE(nested_slot >= 0);

  crash::ActiveCallback seen;
  crash::ActiveCallback nested_seen;
  crash::ActiveCallback after_nested;
  int publishes = 0;

  boat::core::PluginManager manager;
  // Host code the plugin calls back into from on_tick -- exactly the place a
  // fault would have its PC outside the plugin.
  manager.SetFramePublisher([&](const BoatFrame&) {
    ++publishes;
    seen = crash::CurrentCallback();
    {
      crash::ActiveCallbackScope inner(nested_slot, crash::CallbackKind::kOnFrame);
      nested_seen = crash::CurrentCallback();
    }
    after_nested = crash::CurrentCallback();
  });
  manager.Load(kSo, R"({"mode":"publish_on_tick"})");

  REQUIRE(crash::CurrentCallback().slot == -1);
  manager.TickAll(1);
  REQUIRE(publishes == 1);

  CHECK(seen.key == kSo);
  CHECK(seen.kind == crash::CallbackKind::kOnTick);
  CHECK(nested_seen.key == "nested-test");
  CHECK(nested_seen.kind == crash::CallbackKind::kOnFrame);
  CHECK(after_nested.key == kSo);
  CHECK(after_nested.kind == crash::CallbackKind::kOnTick);

  const auto outside = crash::CurrentCallback();
  CHECK(outside.slot == -1);
  CHECK(outside.kind == crash::CallbackKind::kNone);
  CHECK(outside.key.empty());

  manager.Unload(kSo);
  crash::UnregisterPluginImage(nested_slot);
}

TEST_CASE("A failed plugin load leaves no crash-attribution registration",
          "[unit][plugin_crash]") {
  boat::core::PluginManager manager;

  REQUIRE_THROWS(manager.Load(kSo, R"({"mode":"fail_initialize"})"));
  CHECK_FALSE(Contains(crash::RegisteredKeys(), kSo));
  CHECK(crash::CurrentCallback().slot == -1);

  const std::string stale = STALE_ABI_PLUGIN_SO;
  REQUIRE_THROWS(manager.Load(stale, "{}"));
  CHECK_FALSE(Contains(crash::RegisteredKeys(), stale));
  CHECK(manager.List().empty());
}

TEST_CASE("PluginManager does not take over process signal dispositions",
          "[unit][plugin_crash]") {
  // Only the gateway's main() installs the fatal-signal handler; loading and
  // ticking plugins through the library must not.
  boat::core::PluginManager manager;
  manager.Load(kSo, R"({"mode":"none"})");
  manager.TickAll(1);
  REQUIRE_FALSE(crash::FatalSignalHandlerInstalled());
}

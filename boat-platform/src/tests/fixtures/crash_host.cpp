// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* A minimal plugin host that installs the gateway's fatal-signal handler,
   loads one plugin and drives it -- the process boat_integration_plugin_crash
   spawns and expects to die. A separate executable rather than a fork() of
   the Catch2 process, so the crash happens in a clean single-purpose process
   and the test only has to read its stderr and exit status.

   Usage: boat_crash_host <plugin.so> <config_json> <action>
     load       load the plugin and exit
     tick       TickAll(1..1000)
     frame      DispatchFrame a CAN frame 1000 times
     host_segv  fault in this host's own code, outside any plugin callback

   Exits 0 with "[crash_host] completed" if nothing crashed. */

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <exception>
#include <string>

#include "plugin/crash_attribution.h"
#include "plugin/plugin_manager.h"

namespace {

volatile std::uintptr_t g_bad_address = 8;

[[gnu::noinline]] void HostSegv() {
  *reinterpret_cast<volatile int*>(g_bad_address) = 1;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc < 4) {
    std::fprintf(stderr, "usage: %s <plugin.so> <config_json> <load|tick|frame|host_segv>\n",
                 argv[0]);
    return 2;
  }
  const std::string action = argv[3];

  if (!boat::core::crash::InstallFatalSignalHandler()) {
    std::fprintf(stderr, "[crash_host] fatal-signal handler install failed\n");
    return 3;
  }

  boat::core::PluginManager manager;
  // Reads every payload byte, so a plugin that hands over a bad pointer
  // faults here -- in host code, with the plugin's callback on the stack.
  manager.SetFramePublisher([](const BoatFrame& f) {
    volatile std::uint8_t sum = 0;
    for (std::size_t i = 0; i < f.payload_len; ++i) sum = static_cast<std::uint8_t>(sum ^ f.payload[i]);
    (void)sum;
  });

  try {
    manager.Load(argv[1], argv[2]);
  } catch (const std::exception& ex) {
    std::fprintf(stderr, "[crash_host] load failed: %s\n", ex.what());
    return 4;
  }

  // Deliberately no try/catch from here on: an exception escaping a plugin
  // callback must reach std::terminate, exactly as it would in the gateway.
  if (action == "tick") {
    for (std::uint64_t tick = 1; tick <= 1000; ++tick) manager.TickAll(tick);
  } else if (action == "frame") {
    std::uint8_t data[8] = {0};
    BoatFrame f{};
    f.bus_type = BOAT_BUS_CAN;
    f.payload = data;
    f.payload_len = sizeof(data);
    for (int i = 0; i < 1000; ++i) manager.DispatchFrame(f);
  } else if (action == "host_segv") {
    HostSegv();
  } else if (action != "load") {
    std::fprintf(stderr, "[crash_host] unknown action '%s'\n", action.c_str());
    return 2;
  }

  std::fprintf(stderr, "[crash_host] completed\n");
  return 0;
}

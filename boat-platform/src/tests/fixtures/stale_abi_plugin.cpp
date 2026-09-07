// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* A plugin built against the previous ABI. Exists only so the loader's
   version check can be tested: an out-of-tree plugin compiled against v8 must
   be refused with a clear message rather than loaded and called through a
   vtable that has since grown a field. */

#include <boat/plugin.h>

namespace {

BoatPluginVTable gVTable = [] {
  BoatPluginVTable vt{};
  vt.initialize = [](void*, const char*) { return 0; };
  vt.on_tick    = [](void*, uint64_t) {};
  vt.shutdown   = [](void*) {};
  return vt;
}();

}  // namespace

extern "C" BoatPlugin* boat_plugin_create() {
  auto* plugin = new BoatPlugin{};
  plugin->vtable = &gVTable;
  plugin->ctx    = nullptr;
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) { delete plugin; }

/* Deliberately stale: one behind whatever the host currently requires. */
extern "C" uint32_t boat_plugin_abi_version() {
  return BOAT_PLUGIN_ABI_VERSION - 1;
}

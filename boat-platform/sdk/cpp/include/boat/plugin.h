#pragma once

#include <stdint.h>

#define BOAT_PLUGIN_ABI_VERSION 1

#ifdef __cplusplus
extern "C" {
#endif

typedef struct BoatPluginVTable {
  int (*initialize)(void* ctx, const char* config_json);
  void (*on_tick)(void* ctx, uint64_t tick);
  void (*shutdown)(void* ctx);
} BoatPluginVTable;

typedef struct BoatPlugin {
  BoatPluginVTable* vtable;
  void* ctx;
} BoatPlugin;

typedef BoatPlugin* (*boat_plugin_create_fn)();
typedef void (*boat_plugin_destroy_fn)(BoatPlugin* plugin);
typedef uint32_t (*boat_plugin_abi_version_fn)();

BoatPlugin* boat_plugin_create();
void boat_plugin_destroy(BoatPlugin* plugin);
uint32_t boat_plugin_abi_version();

#ifdef __cplusplus
}
#endif

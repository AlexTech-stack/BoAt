# ABI v8 Cleanup — Removing All v7 Fallbacks

## Goal

Remove every v7 element from the codebase. No fallback dispatch, no deprecated frame types, no legacy callback paths. After cleanup, the gateway only supports v8 plugins via `on_frame`/`set_frame_publisher`.

---

## Task 1 — Strip v7 from `boat/plugin.h`

**File:** `boat-platform/sdk/cpp/include/boat/plugin.h`

**Remove — types and typedefs:**

| Line(s) | Content | Reason |
|---------|---------|--------|
| 19–24 | `typedef struct BoatCanFrame { ... } BoatCanFrame;` | Replaced by `BoatFrame` with `bus_type=CAN` |
| 27 | `typedef void (*BoatCanPublishFn)(...);` | Replaced by `BoatFramePublishFn` |
| 31 | `typedef void (*BoatCanReceiveFn)(...);` | Replaced by `BoatFrameReceiveFn` |
| 33–40 | `typedef struct BoatEthFrame { ... } BoatEthFrame;` | Replaced by `BoatFrame` with `bus_type=ETHERNET` |
| 42 | `typedef void (*BoatEthPublishFn)(...);` | Replaced by `BoatFramePublishFn` |
| 47 | `typedef void (*BoatEthReceiveFn)(...);` | Replaced by `BoatFrameReceiveFn` |

**Remove — vtable fields (must be removed as a contiguous block to preserve offsets of later fields):**

| Old offset | Field | New offset after removal |
|-----------|-------|--------------------------|
| off+32 | `set_can_publisher` | **REMOVED** |
| off+40 | `on_can_frame` | **REMOVED** |
| off+48 | `set_eth_publisher` | **REMOVED** |
| off+56 | `on_eth_frame` | **REMOVED** |

**Remaining vtable (compact form):**

```c
typedef struct BoatPluginVTable {
  int  (*initialize)(void* ctx, const char* config_json);       // offset  0
  void (*on_tick)(void* ctx, uint64_t tick);                    // offset  8
  void (*shutdown)(void* ctx);                                  // offset 16
  void (*set_publisher)(void* ctx, BoatPublishFn fn,
                        void* publisher_ctx);                    // offset 24
  void (*set_bus_publisher)(void* ctx, BoatBusPublishFn fn,
                            void* publisher_ctx);                // offset 32
  void (*set_pdu_publisher)(void* ctx, BoatPduPublishFn fn,
                            void* publisher_ctx);                // offset 40
  BoatFrameReceiveFn on_frame;                                   // offset 48
  void (*set_frame_publisher)(void* ctx, BoatFramePublishFn fn,
                              void* publisher_ctx);              // offset 56
  BoatDeclaredBusesFn declared_buses;                            // offset 64
} BoatPluginVTable;
```

**Bump:**

```c
#define BOAT_PLUGIN_ABI_VERSION 8
```

**KEEP** (still used by non-frame functionality):
- `BoatPublishFn` — signal publishing (vehicle_dynamics)
- `BoatPduFrame`, `BoatPduPublishFn` — PDU delivery (can_tp reassembly)
- `BoatBusPublishFn` — bus-signal publishing (vehicle_dynamics)
- `BOAT_CAN_FLAG_SELF_SENT 0x08` — used by can_tp and can_bus_registry
- `BoatPluginVTable` entries: `initialize`, `on_tick`, `shutdown`, `set_publisher`, `set_bus_publisher`, `set_pdu_publisher`, `on_frame`, `set_frame_publisher`, `declared_buses`
- `BoatPlugin` struct, entry point typedefs
- All `BoatFrame` types (from `boat/frame.h`)

---

## Task 2 — Strip v7 from PluginManager

**File:** `boat-platform/src/core/plugin/plugin_manager.h`

**Remove:**

| Location | Content |
|----------|---------|
| Lines 31–33 | `using CanPublishFn = std::function<void(const BoatCanFrame&, const std::string&)>;` |
| Lines 36—    | `using EthPublishFn = std::function<void(const BoatEthFrame&)>;` |
| Lines 50–51 | `void SetCanPublisher(CanPublishFn fn);` declaration |
| Lines 56–57 | `void SetEthPublisher(EthPublishFn fn);` declaration |
| Lines 72–73 | `void DispatchCanFrame(const BoatCanFrame&, const std::string&);` declaration |
| Lines 74–75 | `void DispatchEthFrame(const BoatEthFrame&, const std::string&);` declaration |
| Private member | `CanPublishFn can_publisher_fn_;` |
| Private member | `EthPublishFn eth_publisher_fn_;` |

**Keep:** `SetPduPublisher`, `SetBusPublisher`, `SetPublisher`, `SetFramePublisher`, `DispatchFrame`, `TickAll`, `ShutdownAll`, `List`, `Load`, `Unload`, `RegisterService`, `FindService`.

**File:** `boat-platform/src/core/plugin/plugin_manager.cpp`

**Remove implementations:**
1. `void PluginManager::SetCanPublisher(CanPublishFn fn)` — entire body
2. `void PluginManager::SetEthPublisher(EthPublishFn fn)` — entire body
3. `void PluginManager::DispatchCanFrame(const BoatCanFrame&, const std::string&)` — entire body
4. `void PluginManager::DispatchEthFrame(const BoatEthFrame&, const std::string&)` — entire body

**Remove from `Load()`:** the CAN publisher wiring block (~30 lines that parse `"iface"` from config JSON and wire the CAN publisher trampoline). Also remove the ETH publisher wiring block (~15 lines that wire the Ethernet publisher trampoline).

**Keep in `Load()`:** signal publisher (`set_publisher`), bus publisher (`set_bus_publisher`), PDU publisher (`set_pdu_publisher`), frame publisher (`set_frame_publisher`, v8).

**Simplify `DispatchFrame()`:** remove the entire v7 fallback block. After removal, the method becomes:

```cpp
void PluginManager::DispatchFrame(const BoatFrame& frame) {
  std::vector<BoatPlugin*> snapshot;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    snapshot.reserve(plugins_.size());
    for (auto& [name, handle] : plugins_) {
      (void)name;
      snapshot.push_back(handle.plugin);
    }
  }
  for (auto* plugin : snapshot) {
    if (plugin->vtable->on_frame != nullptr) {
      plugin->vtable->on_frame(plugin->ctx, &frame);
    }
  }
}
```

**Update ABI version check:** In `Load()`, change:

```cpp
// OLD:
if (abi_version != BOAT_PLUGIN_ABI_VERSION) {
    dlclose(dl_handle);
    throw std::runtime_error("Plugin ABI version mismatch");
}

// NEW:
if (abi_version != BOAT_PLUGIN_ABI_VERSION) {
    dlclose(dl_handle);
    throw std::runtime_error("Plugin ABI version mismatch (" +
                             std::to_string(abi_version) +
                             " != " + std::to_string(BOAT_PLUGIN_ABI_VERSION) + ")");
}
```

(Error message improvement — returns the actual and expected version numbers.)

**Remove:** `#include` for types that become unnecessary (none needed — `BoatFrame` is already included via `boat/frame.h` through `boat/plugin.h`).

---

## Task 3 — Strip v7 wiring from main.cpp

**File:** `boat-platform/src/gateway/grpc_gateway/main.cpp`

**Remove v7 publisher wiring** (old lines 183–215 approximately):

1. Remove `node_manager.SetCanPublisher(...)` and its lambda — 12 lines
2. Remove `can_registry.Subscribe("", [...DispatchCanFrame...])` — 9 lines
3. Remove `eth_registry.Subscribe("", 0, [...DispatchEthFrame...])` — 11 lines
4. Remove `node_manager.SetEthPublisher(...)` and its lambda — 8 lines

Total: ~40 lines removed.

**Keep** (v8 wiring — must remain):

```cpp
// v8 frame publisher (CAN + Ethernet output)
node_manager.SetFramePublisher([&can_registry, &eth_registry](const BoatFrame& f) { ... });

// v8 frame bridge (CAN/Ethernet input → DispatchFrame)
can_registry.SubscribeFrame([&node_manager](const boat::core::Frame& f) { ... });
eth_registry.SubscribeFrame([&node_manager](const boat::core::Frame& f) { ... });
```

**Keep** (other publishers — still needed):
```cpp
node_manager.SetBusPublisher([&signal_bus](...) { ... });
node_manager.SetPduPublisher([&node_manager](...) { ... });
node_manager.SetFramePublisher([...](...) { ... });
```

---

## Task 4 — Update all plugins' vtable aggregate initializers

All 7 plugins use aggregate initialization for their vtable. Since 4 fields are removed from the vtable, ALL aggregate initializers must be updated to omit those fields.

**Plugin vtable before** (example: can_responder):
```cpp
BoatPluginVTable vt{};
vt.initialize           = &responder_initialize;
vt.on_tick              = &responder_on_tick;
vt.shutdown             = &responder_shutdown;
vt.set_publisher        = nullptr;
vt.set_can_publisher    = nullptr;  // REMOVE
vt.on_can_frame         = nullptr;  // REMOVE
vt.set_frame_publisher  = &responder_set_frame_publisher;
vt.on_frame             = &responder_on_frame;
vt.declared_buses       = &responder_declared_buses;
```

**Plugin vtable after** (removing the two `nullptr` lines):
```cpp
BoatPluginVTable vt{};
vt.initialize           = &responder_initialize;
vt.on_tick              = &responder_on_tick;
vt.shutdown             = &responder_shutdown;
vt.set_publisher        = nullptr;
vt.set_bus_publisher    = nullptr;    // explicit zero
vt.set_pdu_publisher    = nullptr;    // explicit zero
vt.set_frame_publisher  = &responder_set_frame_publisher;
vt.on_frame             = &responder_on_frame;
vt.declared_buses       = &responder_declared_buses;
```

Note: fields that were previously implicitly zero-initialized by the aggregate (`set_bus_publisher`, `set_pdu_publisher`) must now be EXPLICITLY set since their offset in the vtable changed. The aggregate initializer `vt{}` zeroes everything, but any field that was previously relying on zero-init from a trailing position may now be in a different position.

**All affected plugins:**

| Plugin | v7 fields to remove from vtable init | New explicit zero fields |
|--------|--------------------------------------|--------------------------|
| `network_sim` | (none set — aggregate init only set first 3 fields, v7 fields were zero-init) | Must now explicitly set `set_bus_publisher=nullptr`, `set_pdu_publisher=nullptr`, `on_frame=nullptr`, `set_frame_publisher=nullptr`, `declared_buses=nullptr` since aggregate zero-init may not cover them |
| `sensor_model` | Same as network_sim | Same |
| `can_responder` | Remove `set_can_publisher=nullptr`, `on_can_frame=nullptr` | Add explicit `set_bus_publisher=nullptr`, `set_pdu_publisher=nullptr` |
| `vehicle_dynamics` | Remove `set_can_publisher=nullptr`, `set_eth_publisher=nullptr`, `on_can_frame=nullptr`, `on_eth_frame=nullptr` | Ensure `set_bus_publisher`, `set_pdu_publisher`, `on_frame`, `set_frame_publisher`, `declared_buses` are correctly set |
| `someip` | Remove `set_can_publisher=nullptr`, `on_can_frame=nullptr`, `set_eth_publisher=nullptr`, `on_eth_frame=nullptr` | Same |
| `can_tp` | Remove `set_can_publisher=nullptr`, `on_can_frame=nullptr`, `set_eth_publisher=nullptr`, `on_eth_frame=nullptr` | Same |
| `tcp` | Remove `set_can_publisher=nullptr`, `on_can_frame=nullptr`, `set_eth_publisher=nullptr`, `on_eth_frame=nullptr` | Same |
| `pdu_router` | (already has none of the v7 fields set) | Ensure all fields are explicitly set |

**Critical:** `vehicle_dynamics` uses namespace-scope aggregate init:
```cpp
BoatPluginVTable kVehicleDynamicsVTable = {
    &vehicle_initialize,
    &vehicle_on_tick,
    &vehicle_shutdown,
    &vehicle_set_publisher,
    ...
};
```

This MUST be rewritten with explicit field names because the positional aggregate initializer relies on vtable field order which has changed.

**All plugins that use positional aggregate init must switch to designated init or field-by-field assignment:**

- `vehicle_dynamics` — namespace-scope aggregate → **REWRITE as designated init**
- `sensor_model` — same
- `network_sim` — same
- `can_responder` — already uses field-by-field (lambda pattern) → safe
- `someip` — already uses field-by-field → safe
- `can_tp` — already uses field-by-field → safe
- `tcp` — already uses field-by-field → safe
- `pdu_router` — already uses field-by-field → safe

---

## Task 5 — Remove deprecated v7 types from any remaining references

Search for and remove any remaining references to:
- `BoatCanFrame` — grep codebase, remove any includes or usages
- `BoatEthFrame` — same
- `BoatCanPublishFn` — same
- `BoatEthPublishFn` — same
- `BoatCanReceiveFn` — same
- `BoatEthReceiveFn` — same

Files that may reference these (check each):
- `src/gateway/grpc_gateway/main.cpp` — removed in Task 3
- `src/core/plugin/plugin_manager.cpp` — removed in Task 2
- `src/hil/can_bus_registry.cpp` — uses `BoatCanFrame`? Check. The registry's `SubscribeFrame` converts to `core::Frame`, not `BoatCanFrame`. Should be safe.
- `src/hil/ethernet_bus_registry.cpp` — check for `BoatEthFrame`
- Any test files

---

## Task 6 — Rebuild and test

After all changes:
1. `cmake --preset debug`
2. `cmake --build --preset debug`
3. Fix all compilation errors (primarily from plugins with stale vtable layouts)
4. Run all unit tests
5. Verify gateway starts

---

## Task 7 — Document the break

Update `AGENTS.md` Quirks section:
- `BOAT_PLUGIN_ABI_VERSION` is now 8
- v7 plugins will NOT load — error message will include version numbers
- List the removed types and their v8 replacements

---

## File Change Inventory

| File | Action | Description |
|------|--------|-------------|
| `sdk/cpp/include/boat/plugin.h` | **Modify** | Remove v7 types, vtable fields; bump ABI to 8 |
| `src/core/plugin/plugin_manager.h` | **Modify** | Remove v7-only methods and members |
| `src/core/plugin/plugin_manager.cpp` | **Modify** | Remove v7 impl, simplify DispatchFrame, update ABI check |
| `src/gateway/grpc_gateway/main.cpp` | **Modify** | Remove v7 wiring (~40 lines) |
| `src/plugins/network_sim/network_sim_plugin.cpp` | **Modify** | Rewrite vtable init (aggregate → explicit fields) |
| `src/plugins/sensor_model/sensor_model_plugin.cpp` | **Modify** | Same |
| `src/plugins/vehicle_dynamics/vehicle_dynamics_plugin.cpp` | **Modify** | Rewrite vtable init |
| `src/plugins/can_responder/can_responder_plugin.cpp` | **Modify** | Remove v7 nullptr lines from vtable |
| `src/plugins/someip/someip_plugin.cpp` | **Modify** | Remove v7 nullptr lines from vtable |
| `src/plugins/can_tp/can_tp_plugin.cpp` | **Modify** | Remove v7 nullptr lines from vtable |
| `src/plugins/tcp/tcp_plugin.cpp` | **Modify** | Remove v7 nullptr lines from vtable |
| `src/plugins/pdu_router/pdu_router_plugin.cpp` | **Modify** | Ensure all vtable fields explicitly set |
| `AGENTS.md` | **Modify** | Update v8 docs, list removed types |

**No changes needed** (already v8-only or independent):
- `src/hil/can_bus_registry.*` — uses internal `hil::CanFrame`, not plugin types
- `src/hil/ethernet_bus_registry.*` — same
- `src/core/frame.*` — independent
- `proto/boat/v1/frame.proto` — independent
- `sdk/python/boat/frame_node.py` — independent
- `cli/boat_cli/frame.py` — independent

---

## Verification Checklist

After completion, verify:

- [ ] `BOAT_PLUGIN_ABI_VERSION == 8`
- [ ] `sizeof(BoatPluginVTable)` is 72 bytes (9 pointers × 8)
- [ ] No references to `BoatCanFrame`, `BoatEthFrame`, `BoatCanPublishFn`, `BoatEthPublishFn` in codebase
- [ ] Loading a v7 plugin returns error with version numbers
- [ ] All plugins compile against new vtable layout
- [ ] All unit tests pass
- [ ] Gateway starts with all plugins via `BOAT_NODE_PLUGINS`
- [ ] `boat frame send` and `boat frame subscribe` work
- [ ] `boat pdu` commands work (PduRouter plugin must be loaded)
- [ ] Gateway starts WITHOUT PduRouter plugin — PDU RPCs return NOT_FOUND gracefully

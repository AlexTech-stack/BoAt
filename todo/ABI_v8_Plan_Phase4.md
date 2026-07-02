# Phase 4 — Domain Extraction (PduRouter → Plugin)

**Goal:** Move the PduRouter, TransmissionEngine, COM signal library, IpduM containers, and deadline monitoring out of the core `src/hil/pdu/` directory into gateway-resident plugins. gRPC PduService delegates to the plugin. The core GatewayContext loses its PduRouter dependency.

**Subagents needed:** plugin-sdk, pdu-database, cpp-build-test, proto-codegen, e2e-integration, py-sdk-cli, docs-arch

**Dependencies:** Phase 3 complete (all plugins migrated to v8, unified frame dispatch running)

---

## Task 4.1 — Create PduRouter Plugin (`src/plugins/pdu_router/`)

**Subagent:** plugin-sdk + pdu-database

### Task 4.1.1 — Move source files

| Source (old) | Destination (new) |
|---|---|
| `src/hil/pdu/pdu_router.h` | `src/plugins/pdu_router/pdu_router.h` |
| `src/hil/pdu/pdu_router.cpp` | `src/plugins/pdu_router/pdu_router.cpp` |
| `src/hil/pdu/pdu_types.h` | `src/plugins/pdu_router/pdu_types.h` |
| `src/hil/pdu/transmission_engine.h` | `src/plugins/pdu_router/transmission_engine.h` |
| `src/hil/pdu/transmission_engine.cpp` | `src/plugins/pdu_router/transmission_engine.cpp` |
| `src/hil/pdu/ipdumcontainer.h` | `src/plugins/pdu_router/ipdumcontainer.h` |
| `src/hil/pdu/ipdumcontainer.cpp` | `src/plugins/pdu_router/ipdumcontainer.cpp` |

**What stays in core:** `tick_timer.h/.cpp` (infrastructure, used by both core and plugins)

### Task 4.1.2 — Adapt PduRouter to v8 plugin ABI

**Changes to PduRouter class:**

```cpp
// OLD: PduRouter(CanBusRegistry& can, EthernetBusRegistry& eth)
// NEW: PduRouter constructor takes no registries — all I/O via frame bus

class PduRouterPlugin {
 public:
  // VTable callbacks
  int  initialize(void* ctx, const char* config_json);
  void on_tick(void* ctx, uint64_t tick);
  void shutdown(void* ctx);

  // Frame I/O (replaces registries)
  void on_frame(void* ctx, const BoatFrame* frame);       // receive frame from bus
  void set_frame_publisher(void* ctx, BoatFramePublishFn fn, void* pub_ctx);  // publish frame to bus
  const char* declared_buses(void* ctx);                   // "[\"can\",\"eth\",\"pdu\"]"

  // gRPC service (see Task 4.3)
  void register_grpc_services(void* ctx, void* server_builder);
};
```

**PduRouter::on_frame — Receive path (was OnCanFrame + OnEthernetFrame):**
```
on_frame(frame):
  if frame.bus_type == CAN:
    route_by_can_id(frame.meta.can.can_id, frame.payload)
  else if frame.bus_type == ETHERNET:
    if frame.meta.eth contains IpduM container:
      parse_container_entries(frame.payload)
    else:
      route_by_ethertype(frame.meta.eth.ethertype, frame.payload)
  // Dispatch to PDU subscribers (internal callbacks, gRPC PduService)
```

**PduRouter::publish_frame — Send path (was SendPdu → CanBusRegistry/EtherBusRegistry):**
```
publish_frame(pdu_frame):
  // Build appropriate bus frame from PDU route
  route = routes_[pdu_frame.pdu_id]
  if route.transport == CAN:
    bus_frame = Frame{CAN, iface, can_id=route.can_id, data=payload}
  else if route.transport == ETHERNET:
    bus_frame = Frame{ETH, iface, ethertype=route.ethertype, ...}
  set_frame_publisher_fn_(bus_frame)
```

**PduRouter::on_tick (unchanged):**
```
on_tick(tick):
  tx_engine_.OnTick(tick)       // drives cyclic/onchange schedules
  check_deadlines(tick)         // PDU-level deadline monitoring
```

### Task 4.1.3 — Build system

```cmake
# src/plugins/pdu_router/CMakeLists.txt
add_boat_plugin(pdu_router
  pdu_router.cpp
  pdu_router_plugin.cpp       # new: vtable + create/destroy
  transmission_engine.cpp
  ipdumcontainer.cpp
)
target_link_libraries(pdu_router PRIVATE boat_plugin_sdk)
target_include_directories(pdu_router PRIVATE ${CMAKE_SOURCE_DIR}/src)
```

**Acceptance criteria:**
- `pdu_router.so` builds as a standalone plugin
- Plugin loads via `BOAT_NODE_PLUGINS` or `RegisterPlugin`
- CAN → PDU routing works: CAN frame on bus → PduRouter.on_frame → gRPC PduService subscriber receives PduFrame
- PDU → CAN routing works: gRPC SendPdu → PduRouter → Frame{SendCAN} → CAN bus
- Cyclic transmission works (ticks drive scheduled sends)
- Deadline monitoring works (timeout detected, callback fired)
- I-PDU groups work (enable/disable gating)
- IpduM containers work (serialize/deserialize)

---

## Task 4.2 — Create COM Signal Plugin (`src/plugins/pdu_com/`)

**Subagent:** pdu-database

### Task 4.2.1 — Move source files

| Source (old) | Destination (new) |
|---|---|
| `src/hil/pdu/com/com_signal.h` | `src/plugins/pdu_com/com_signal.h` |
| `src/hil/pdu/com/com_signal.cpp` | `src/plugins/pdu_com/com_signal.cpp` |

### Task 4.2.2 — Adaptation

The COM signal library (`SignalDef`, `MessageDef`, `PackSignals`, `UnpackSignals`, E2E CRC) was a standalone utility — not a plugin. It becomes a **static library** (`boat_com`) that other plugins link against. The PduRouter plugin already uses it for signal routing.

```cmake
# src/plugins/pdu_com/CMakeLists.txt
add_library(boat_com STATIC com_signal.cpp)
target_compile_features(boat_com PUBLIC cxx_std_20)
target_include_directories(boat_com PUBLIC ${CMAKE_SOURCE_DIR}/src)
```

`pdu_router` links: `target_link_libraries(pdu_router PRIVATE boat_com)`

**Acceptance criteria:**
- `boat_com` builds as a static library
- `pdu_router.so` links against `boat_com` and uses `PackSignals`/`UnpackSignals`
- Existing COM tests compile and pass from new location

---

## Task 4.3 — gRPC PduService Delegation

**Subagent:** proto-codegen + cpp-build-test

**Option B (chosen — lower risk, Phase 4a):** `PduServiceImpl` stays in the gateway but delegates to the PduRouter plugin via a "service provider" interface.

### Task 4.3.1 — Define PduRouter interface

```cpp
// src/core/pdu_router_interface.h
class IPduRouter {
 public:
  virtual ~IPduRouter() = default;
  virtual bool SendPdu(uint32_t pdu_id, const std::vector<uint8_t>& payload) = 0;
  virtual SubId Subscribe(std::vector<uint32_t> pdu_ids, RxCallback cb) = 0;
  virtual void Unsubscribe(SubId id) = 0;
  virtual void AddRoute(const PduRoute& route) = 0;
  virtual void RemoveRoute(uint32_t pdu_id) = 0;
  virtual void AddContainer(const PduContainerDef& def) = 0;
  virtual void AddGroup(const PduGroup& group) = 0;
  virtual void EnableGroup(uint32_t group_id) = 0;
  virtual void DisableGroup(uint32_t group_id) = 0;
  virtual bool IsGroupEnabled(uint32_t group_id) const = 0;
  virtual std::vector<PduRoute> ListRoutes() const = 0;
  virtual std::vector<PduGroup> ListGroups() const = 0;
  virtual void ConfigureDeadline(uint32_t pdu_id, const PduDeadlineConfig& cfg) = 0;
};
```

### Task 4.3.2 — PluginManager: service provider registry

```cpp
// Added to PluginManager
class PluginManager {
 public:
  template<typename T>
  void RegisterService(const std::string& name, std::shared_ptr<T> service);
  
  template<typename T>
  std::shared_ptr<T> FindService(const std::string& name) const;
};
```

The PduRouter plugin, after initialization, registers itself:
```cpp
// pdu_router_plugin.cpp: initialize()
ctx->plugin_manager->RegisterService<IPduRouter>("pdu_router", this);
```

### Task 4.3.3 — PduServiceImpl delegates

```cpp
// OLD:
ctx_.pdu_router.SendPdu(pdu_id, payload);

// NEW:
auto router = ctx_.plugin_manager.FindService<IPduRouter>("pdu_router");
if (router) {
  router->SendPdu(pdu_id, payload);
} else {
  return grpc::Status(NOT_FOUND, "PduRouter plugin not loaded");
}
```

**Acceptance criteria:**
- PDU gRPC RPCs return NOT_FOUND when PduRouter plugin not loaded
- PDU gRPC RPCs work correctly when PduRouter plugin IS loaded
- Hot-plug: load PduRouter plugin mid-session, PDU RPCs become available
- Hot-unplug: unload PduRouter plugin, PDU RPCs return NOT_FOUND again

---

## Task 4.4 — GatewayContext Simplification

**Subagent:** docs-arch + cpp-build-test

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `src/gateway/grpc_gateway/gateway_context.h` | Modify | Remove PduRouter, CanBusRegistry, EthernetBusRegistry references |

```cpp
// BEFORE:
struct GatewayContext {
  SimulationContext&       sim;
  SignalBus&              signal_bus;
  ScenarioLoader&         scenario_loader;
  SqliteEventStore&       event_store;
  FlatFileTraceStore&     trace_store;
  ReplayController&       replay_controller;
  CanBusRegistry&         can_bus_registry;       // REMOVED
  EthernetBusRegistry&    ethernet_bus_registry;  // REMOVED
  PduRouter&              pdu_router;             // REMOVED
  RpcAuditLog&            audit_log;
};

// AFTER:
struct GatewayContext {
  SimulationContext&       sim;
  SignalBus&              signal_bus;
  ScenarioLoader&         scenario_loader;
  SqliteEventStore&       event_store;
  FlatFileTraceStore&     trace_store;
  ReplayController&       replay_controller;
  PluginManager&          plugin_manager;   // unified
  RpcAuditLog&            audit_log;
};
```

**What breaks and must be updated:**
- `CanServiceImpl` → previously used `ctx_.can_bus_registry`, now uses `ctx_.plugin_manager.FindService<ICanRegistry>("can")`
- `EthernetServiceImpl` → same pattern
- `SimulationServiceImpl` → same pattern
- `PduServiceImpl` → `ctx_.plugin_manager.FindService<IPduRouter>("pdu_router")`
- `main.cpp` → all service instantiation updated to use plugin_manager.dereference paths

**Actually, safer approach for Phase 4a:** Keep CanBusRegistry and EthernetBusRegistry in GatewayContext but REMOVE PduRouter. The CAN/Eth registries are infrastructure (like tick timer). PduRouter is the domain-specific component that should become a plugin. Full registry extraction can be Phase 4b or Phase 5.

```cpp
// Phase 4a (safer):
struct GatewayContext {
  SimulationContext&       sim;
  SignalBus&              signal_bus;
  ScenarioLoader&         scenario_loader;
  SqliteEventStore&       event_store;
  FlatFileTraceStore&     trace_store;
  ReplayController&       replay_controller;
  CanBusRegistry&         can_bus_registry;       // KEPT (infrastructure)
  EthernetBusRegistry&    ethernet_bus_registry;  // KEPT (infrastructure)
  PluginManager&          plugin_manager;          // ADDED (for plugin access)
  // PduRouter& pdu_router;                       // REMOVED
  RpcAuditLog&            audit_log;
};
```

**Acceptance criteria:**
- Gateway compiles with PduRouter removed from GatewayContext
- `PduServiceImpl` delegates to plugin via PluginManager
- CanService/EthernetService still work (registries kept)
- All existing gRPC integration tests pass

---

## Task 4.5 — Update main.cpp

**Subagent:** cpp-build-test

**Changes to main.cpp:**

1. **Remove PduRouter instantiation** (line 258):
```cpp
// DELETED: boat::hil::PduRouter pdu_router(can_registry, eth_registry);
```

2. **Remove PduRouter from tick thread** (line 382):
```cpp
// DELETED: pdu_router.OnTick(elapsed_ms);
// PluginManager::TickAll() handles this for PduRouter plugin
```

3. **Update PDU publisher wiring** (lines 339-343):
```cpp
// Instead of: pdu_router.SendPdu(pdu_id, payload)
// Use: publish frame to generic bus, PduRouter plugin picks it up
node_manager.SetPduPublisher([&node_manager](const BoatPduFrame& f) {
  BoatFrame frame{};
  frame.bus_type = BOAT_BUS_PDU;
  frame.meta.pdu.pdu_id = f.pdu_id;
  frame.payload = f.payload;
  frame.payload_len = f.payload_len;
  node_manager.DispatchFrame(frame);
});
```

4. **Update replay forwarder** (lines 321-324):
```cpp
// OLD: pdu_router.SendPdu(pdu_id, payload)
// NEW: publish Frame{PDU} to bus
```

5. **Update GatewayContext assembly** (lines 387-398):
```cpp
boat::gateway::GatewayContext ctx{
  .sim                  = sim,
  .signal_bus           = signal_bus,
  .scenario_loader      = scenario_loader,
  .event_store          = event_store,
  .trace_store          = trace_store,
  .replay_controller    = replay_controller,
  .can_bus_registry     = can_registry,
  .ethernet_bus_registry = eth_registry,
  .plugin_manager       = node_manager,
  // .pdu_router is gone
  .audit_log            = audit_log,
};
```

**Acceptance criteria:**
- Gateway compiles and starts without PduRouter in core
- Tick thread drives PduRouter plugin ticks through PluginManager::TickAll
- Replay events are delivered as PDU frames to the bus
- No crashes or null dereferences

---

## Task 4.6 — Migration & Verification

**Subagent:** e2e-integration + cpp-build-test

### Task 4.6.1 — Existing tests

- Move PduRouter unit tests from `src/tests/unit/test_pdu_router.cpp` to `src/plugins/pdu_router/test/`
- Move COM signal tests from `src/tests/unit/test_com_signal.cpp` to `src/plugins/pdu_com/test/`
- All existing Catch2 test cases must pass from new locations

### Task 4.6.2 — New integration tests

| Test | Description |
|------|-------------|
| Gateway starts without PduRouter plugin | Graceful: PDU gRPC returns NOT_FOUND |
| Gateway starts with PduRouter plugin | PDU routing works |
| Hot-load PduRouter plugin | PDU gRPC becomes available mid-session |
| Hot-unload PduRouter plugin | PDU gRPC returns NOT_FOUND, existing routes cleaned up |
| CanTp plugin publishes PDU → PduRouter routes it | End-to-end: CAN segment → CanTp reassembly → PduRouter route → Ethernet IpduM container |
| VehicleDynamics publishes CAN → PduRouter routes → PDU subscriber receives | Cross-plugin PDU path |

---

## Phase 4 — File Change Summary

| File | Action | Lines (est.) |
|------|--------|:-----------:|
| `src/hil/pdu/pdu_router.h` | **Moved** → `src/plugins/pdu_router/` | — |
| `src/hil/pdu/pdu_router.cpp` | **Moved** → `src/plugins/pdu_router/` | — |
| `src/hil/pdu/pdu_types.h` | **Moved** → `src/plugins/pdu_router/` | — |
| `src/hil/pdu/transmission_engine.h` | **Moved** → `src/plugins/pdu_router/` | — |
| `src/hil/pdu/transmission_engine.cpp` | **Moved** → `src/plugins/pdu_router/` | — |
| `src/hil/pdu/ipdumcontainer.h` | **Moved** → `src/plugins/pdu_router/` | — |
| `src/hil/pdu/ipdumcontainer.cpp` | **Moved** → `src/plugins/pdu_router/` | — |
| `src/hil/pdu/com/com_signal.h` | **Moved** → `src/plugins/pdu_com/` | — |
| `src/hil/pdu/com/com_signal.cpp` | **Moved** → `src/plugins/pdu_com/` | — |
| `src/plugins/pdu_router/pdu_router_plugin.cpp` | **New** | ~150 |
| `src/plugins/pdu_router/CMakeLists.txt` | **New** | ~8 |
| `src/plugins/pdu_com/CMakeLists.txt` | **New** | ~6 |
| `src/plugins/CMakeLists.txt` | Modify | +2 |
| `src/hil/CMakeLists.txt` | Modify | Remove PduRouter/com sources |
| `src/core/pdu_router_interface.h` | **New** | ~40 |
| `src/core/plugin/plugin_manager.h` | Modify | +20 (service provider registry) |
| `src/core/plugin/plugin_manager.cpp` | Modify | +30 |
| `src/gateway/grpc_gateway/gateway_context.h` | Modify | -1 member, +0 |
| `src/gateway/grpc_gateway/main.cpp` | Modify | -30, +10 |
| `src/gateway/grpc_gateway/pdu_service_impl.cpp` | Modify | ~80 (delegation boilerplate) |
| `src/tests/unit/test_pdu_router.cpp` | **Moved** → plugin directory | — |
| `src/tests/unit/test_com_signal.cpp` | **Moved** → plugin directory | — |
| `src/tests/integration/test_pdu_plugin.cpp` | **New** | ~200 |
| **Total** | | **~550 new, ~0 net if moves counted as 0** |

**Risk:** High — Single largest code motion. PduRouter is the central routing hub. Mitigation: Phase 4a keeps registries in core, only extracts PduRouter. All existing tests move with the code.

**Build impact:** `boat_hil` shrinks (no pdu/ sources). Two new plugin targets (`pdu_router.so`, `boat_com` static lib). `boat_gateway` rebuilt (new includes). All PDU tests move.

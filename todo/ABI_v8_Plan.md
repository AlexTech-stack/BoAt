# ABI v8 — Frame Unification & Major Refactor

## Vision

Transform `boat_gateway` from a CAN/Ethernet-specific PDU gateway into a **generic simulation bus core** that routes typed frames (CAN, Ethernet, TCP, PDU, ...) between plugins without owning domain-specific transport or protocol logic. The core provides: **a clock, a frame bus, a plugin lifecycle, and an external API** — nothing more.

```
Before (v7):                        After (v8):
┌──────────────────────┐           ┌──────────────────────┐
│ boat_gateway (core)   │           │ boat_gateway (core)  │
│ ┌──────────────────┐ │           │ ┌──────────────────┐│
│ │ CanBusRegistry   │ │           │ │ FrameBusRegistry  ││
│ │ EtherBusRegistry │ │           │ │ (any frame type)  ││
│ │ PduRouter        │ │           │ │ PluginManager     ││
│ │ PluginManager    │ │           │ │ FrameServicegRPC  ││
│ │ gRPC services    │ │           │ │ ReplayController  ││
│ └──────────────────┘ │           │ └──────────────────┘│
│                      │           │                      │
│ Plugin ABI:          │           │ Plugin ABI v8:       │
│ on_can_frame()       │           │ on_frame(Frame)     │
│ on_eth_frame()       │           │ send_frame(Frame)   │
│ set_can_publisher()  │           │ declare_buses()     │
│ set_eth_publisher()  │           └──────────────────────┘
└──────────────────────┘                      │
                                              ▼
                                     ┌──────────────────────┐
                                     │ Plugins:              │
                                     │ PduRouter              │
                                     │ CanTp / SOME/IP / TCP │
                                     │ E2E / SecOC / Signal  │
                                     │ Gateway / VehicleDyn   │
                                     │ Sensor / NetworkSim   │
                                     │ ...                    │
                                     └──────────────────────┘
```

---

## Scope & File Inventory

| Area | Files | Change |
|------|-------|--------|
| Plugin ABI (C header) | `sdk/cpp/include/boat/plugin.h` | Append v8 fields |
| Frame types | Add: `sdk/cpp/include/boat/frame.h`, `src/core/frame.h`, `proto/boat/v1/frame.proto` | New files |
| PluginManager | `src/core/plugin/plugin_manager.h/.cpp` | Add unified dispatch |
| HIL Bus Registries | `src/hil/can_bus_registry.h/.cpp`, `ethernet_bus_registry.h/.cpp` | Add Frame subscriber |
| Frame gRPC Service | Add: `src/gateway/grpc_gateway/frame_service_impl.h/.cpp` | New service |
| main.cpp | `src/gateway/grpc_gateway/main.cpp` | Rewire caps |
| GatewayContext | `src/gateway/grpc_gateway/gateway_context.h` | Simplify |
| All plugins (7) | `src/plugins/*/` | Migrate to v8 |
| TCP plugin | `src/plugins/tcp/` | Full rewrite |
| PduRouter extraction | `src/hil/pdu/` → `src/plugins/pdu_router/` | Code motion + gRPC |
| COM signals | `src/hil/pdu/com/` → `src/plugins/pdu_com/` | Code motion |
| Python SDK | `sdk/python/boat/can_node.py`, `ethernet_node.py`, `pdu_node.py`, `tcp.py`, `trace_replay.py`, etc. | Update |
| Python CLI | `cli/boat_cli/can.py`, `eth.py`, `pdu.py`, `plugin.py` | Update |
| Demo scripts | `demo/tcp_plugin/`, `demo/can_responder_node.py`, etc. | Update |
| Build system | `src/plugins/CMakeLists.txt`, `src/hil/CMakeLists.txt`, `src/core/CMakeLists.txt` | Adjust |

---

## Phase 1 — Design & ABI v8 (no runtime changes)

### 1.1 `BoatFrame` — generic bus frame

```c
// C ABI (sdk/cpp/include/boat/frame.h) — stable, embedded-ABI-friendly
typedef enum {
  BOAT_BUS_CAN      = 0,
  BOAT_BUS_CANFD    = 1,
  BOAT_BUS_ETHERNET = 2,
  BOAT_BUS_TCP      = 3,
  BOAT_BUS_PDU      = 4,
  // Future: LIN, FLEXRAY, ...
} BoatBusType;

typedef struct BoatCanMeta {
  uint32_t can_id;
  uint8_t  dlc;
  uint8_t  flags;      // BRS/FDF/ESI
} BoatCanMeta;

typedef struct BoatEthMeta {
  uint8_t  dst_mac[6];
  uint8_t  src_mac[6];
  uint16_t ethertype;
  uint16_t vlan_id;
  uint8_t  ip_buf[16]; // src_ip || dst_ip (4+4 v4, 16+16 v6)
} BoatEthMeta;

typedef struct BoatTcpMeta {
  uint8_t  src_ip[16]; // 4 for v4, 16 for v6
  uint8_t  dst_ip[16];
  uint16_t src_port;
  uint16_t dst_port;
  int32_t  conn_id;    // -1 = new connection
} BoatTcpMeta;

typedef struct BoatPduMeta {
  uint32_t pdu_id;
} BoatPduMeta;

typedef struct BoatFrame {
  BoatBusType  bus_type;
  const char*  iface;          // interface name ("" = auto)
  uint64_t     timestamp_ns;
  uint8_t*     payload;
  size_t       payload_len;
  // Metadata — only the union member matching bus_type is valid
  union {
    BoatCanMeta   can;
    BoatEthMeta   eth;
    BoatTcpMeta   tcp;
    BoatPduMeta   pdu;
  } meta;
} BoatFrame;
```

**Ownership:** `payload` is NOT owned by `BoatFrame`. The sender owns it. On the plugin boundary, the core ensures the buffer lives for the duration of the callback.

### 1.2 `core::Frame` — C++ internal type

```cpp
// src/core/frame.h
class Frame {
 public:
  enum class BusType { kCan, kCanFd, kEthernet, kTcp, kPdu };

  // Constructors from specific frame types
  static Frame FromCan(std::string iface, uint32_t can_id, uint8_t dlc,
                        uint8_t flags, std::vector<uint8_t> payload);
  static Frame FromEthernet(std::string iface, /* ... */);
  static Frame FromTcp(std::string iface, /* ... */);
  static Frame FromPdu(std::string iface, uint32_t pdu_id,
                        std::vector<uint8_t> payload);

  // Conversion
  void ToAbi(BoatFrame* out) const;  // zero-copy if payload already Pinned
  static Frame FromAbi(const BoatFrame& abi);

  // Accessors
  BusType bus_type() const;
  const std::string& iface() const;
  // ...

 private:
  BusType bus_type_;
  std::string iface_;
  uint64_t timestamp_ns_;
  std::variant<CanMeta, EthMeta, TcpMeta, PduMeta> meta_;
  std::vector<uint8_t> payload_;
};
```

### 1.3 `frame.proto` — wire format

```protobuf
syntax = "proto3";
package boat.v1;

message Frame {
  enum BusType { CAN = 0; CANFD = 1; ETHERNET = 2; TCP = 3; PDU = 4; }
  BusType bus_type = 1;
  string  iface    = 2;
  uint64  timestamp_ns = 3;
  bytes   payload  = 4;
  oneof metadata {
    CanMetadata   can  = 10;
    EthMetadata   eth  = 11;
    TcpMetadata   tcp  = 12;
    PduMetadata   pdu  = 13;
  }
}

message CanMetadata {
  uint32 can_id  = 1;
  uint32 dlc     = 2;
  uint32 flags   = 3;
}

message EthMetadata {
  bytes  dst_mac   = 1; // 6 bytes
  bytes  src_mac   = 2; // 6 bytes
  uint32 ethertype = 3;
  uint32 vlan_id   = 4;
  bytes  src_ip    = 5; // 4 (v4) or 16 (v6)
  bytes  dst_ip    = 6; // same
}

message TcpMetadata {
  bytes  src_ip    = 1; // 4 (v4) or 16 (v6)
  bytes  dst_ip    = 2; // same
  uint32 src_port  = 3;
  uint32 dst_port  = 4;
  int32  conn_id   = 5; // -1 = new connection request
}

message PduMetadata {
  uint32 pdu_id = 1;
}

// New gRPC service (additive, alongside old CanService/EthernetService)
service FrameService {
  rpc SendFrame(Frame) returns (SendFrameResponse);
  rpc SubscribeFrames(SubscribeFramesRequest) returns (stream Frame);
}

message SendFrameRequest  { Frame frame = 1; }
message SendFrameResponse { bool accepted = 1; }
message SubscribeFramesRequest {
  repeated Frame.BusType bus_types = 1;  // empty = all
  string iface_filter = 2;              // empty = all
}
```

### 1.4 Plugin ABI v8 — additions to `BoatPluginVTable`

```c
// Appended to BoatPluginVTable (existing v1-v7 fields unchanged)
typedef struct BoatPluginVTable {
  // v1-v7 fields (unchanged, same offsets)
  int  (*initialize)(void* ctx, const char* config_json);
  void (*on_tick)(void* ctx, uint64_t tick);
  void (*shutdown)(void* ctx);
  void (*set_publisher)(void* ctx, BoatPublishFn fn, void* publisher_ctx);
  void (*set_can_publisher)(void* ctx, BoatCanPublishFn fn, void* publisher_ctx);
  BoatCanReceiveFn on_can_frame;
  void (*set_eth_publisher)(void* ctx, BoatEthPublishFn fn, void* publisher_ctx);
  BoatEthReceiveFn on_eth_frame;
  void (*set_bus_publisher)(void* ctx, BoatBusPublishFn fn, void* publisher_ctx);
  void (*set_pdu_publisher)(void* ctx, BoatPduPublishFn fn, void* publisher_ctx);

  // v8 fields start at offset 88 (11th pointer)
  BoatFrameReceiveFn on_frame;           // replaces on_can_frame + on_eth_frame
  void (*set_frame_publisher)(void* ctx, BoatFramePublishFn fn, void* publisher_ctx);
  const char* (*declared_buses)(void* ctx);  // returns JSON array of bus types
  // v8.1 (future): void (*register_grpc_services)(void* ctx, void* server_builder);
} BoatPluginVTable;
```

**Backward compat rule:** PluginManager checks `on_frame != nullptr`. If null, falls back to the existing v7 `on_can_frame`/`on_eth_frame` dispatch paths. A single gateway can host both v7 and v8 plugins simultaneously.

### Phase 1 artifacts

| File | Action |
|------|--------|
| `sdk/cpp/include/boat/frame.h` | **New** — `BoatFrame`, `BoatBusType`, metadata structs |
| `sdk/cpp/include/boat/plugin.h` | **Append** — v8 fields |
| `src/core/frame.h` | **New** — `core::Frame` class with `static FromXxx()` factories |
| `src/core/frame.cpp` | **New** — ABI conversion, proto conversion |
| `proto/boat/v1/frame.proto` | **New** — Frame message + FrameService |
| `sdk/python/boat/stubs/boat/v1/frame_pb2.py` | **Generated** — `generate_stubs.sh` |

---

## Phase 2 — Core Frame Dispatch Unification

### 2.1 PluginManager: unified frame dispatch

```
PluginManager:
  - Add: SetFramePublisher(FramePublishFn)
  - Add: DispatchFrame(const BoatFrame&)
  - Dispatch logic:
      for plugin in plugins:
        if plugin.vtable.on_frame != nullptr:
          if plugin.declared_buses matches frame.bus_type:
            plugin.vtable.on_frame(plugin.ctx, &frame)
        else:
          // fallback to v7 dispatch
          plugin.vtable.on_can_frame(plugin.ctx, &can_frame, iface)  // if CAN
          plugin.vtable.on_eth_frame(plugin.ctx, &eth_frame, iface)  // if ETH
```

### 2.2 CanBusRegistry / EthernetBusRegistry

```cpp
// New methods on both registries:
struct FrameSubscriber {
  SubId id;
  // callback receives core::Frame, not type-specific
  std::function<void(const Frame&)> on_frame;
};

SubId SubscribeFrame(std::function<void(const Frame&)> cb);
void UnsubscribeFrame(SubId id);
```

Internal conversion:
```
hil::CanFrame → core::Frame::FromCan()  (at dispatch boundary)
core::Frame   → hil::CanFrame (via frame.CanMeta if bus_type == CAN)
```

### 2.3 main.cpp wiring

```
// v7 wiring (unchanged for backward compat):
node_manager.SetCanPublisher(...)
node_manager.SetEthPublisher(...)
can_registry.Subscribe("", [&node_manager](CanFrame, iface) { ... })
eth_registry.Subscribe("", 0, [&node_manager](EthFrame, iface) { ... })

// v8 wiring (new):
node_manager.SetFramePublisher([&registries](const BoatFrame* f) {
  switch (f->bus_type) {
    case BOAT_BUS_CAN:  can_registry.SendFrame(...); break;
    case BOAT_BUS_ETHERNET: eth_registry.SendFrame(...); break;
    // TCP, PDU routed via registries or direct plugin channels
  }
});
can_registry.SubscribeFrame([&node_manager](const Frame& f) {
  BoatFrame bf; f.ToAbi(&bf);
  node_manager.DispatchFrame(bf);
});
eth_registry.SubscribeFrame([&node_manager](const Frame& f) {
  BoatFrame bf; f.ToAbi(&bf);
  node_manager.DispatchFrame(bf);
});
```

### 2.4 FrameService gRPC

New service implementation `FrameServiceImpl` using `GatewayContext`:

```
FrameServiceImpl::SendFrame(request):
  core::Frame frame = core::Frame::FromProto(request->frame())
  publish to can_registry or eth_registry depending on bus_type

FrameServiceImpl::SubscribeFrames(request):
  subscribe to can_registry.SubscribeFrame() + eth_registry.SubscribeFrame()
  filter by bus_types[] if specified
  stream Frame protos to client
```

### Phase 2 artifacts

| File | Action |
|------|--------|
| `src/core/plugin/plugin_manager.h/.cpp` | Add `SetFramePublisher`, `DispatchFrame` |
| `src/hil/can_bus_registry.h/.cpp` | Add `SubscribeFrame` / `UnsubscribeFrame` |
| `src/hil/ethernet_bus_registry.h/.cpp` | Add `SubscribeFrame` / `UnsubscribeFrame` |
| `src/gateway/grpc_gateway/frame_service_impl.h/.cpp` | New |
| `src/gateway/grpc_gateway/main.cpp` | New v8 wiring + FrameService registration |
| `src/gateway/grpc_gateway/gateway_context.h` | Add `frame_bus_registry` (or keep separate registries) |
| `sdk/python/boat/stubs/` | Regenerate |

---

## Phase 3 — Plugin Migration to v8

### 3.1 Migration order

| # | Plugin | Effort | v8 changes | Config JSON |
|---|--------|:------:|------------|-------------|
| 1 | `network_sim` | Trivial | `declared_buses → ""`, `on_frame → null` | `{"bus_load_percent": 25}` |
| 2 | `sensor_model` | Trivial | Same pattern | `{"sensor_type":"LIDAR"}` |
| 3 | `can_responder` | Low | `on_frame` filters `BUS_CAN`, maps `can_id` | None |
| 4 | `vehicle_dynamics` | Medium | `set_frame_publisher` → CAN + ETH frames | `{"initial_speed_kmh": 0}` |
| 5 | `someip` | Low | `on_frame` filters `BUS_ETHERNET` | `{"sd_port": 30490}` |
| 6 | `can_tp` | Medium | `on_frame` filters `BUS_CAN`, `set_frame_publisher` | `{"iface":"vcan0"}` |
| 7 | `tcp` | High | Full rewrite (§3.2 below) | `{"mode":"server","listen_port":8080}` |

### 3.2 TCP Plugin — gateway-resident rewrite

**Current:** loaded by Python `ctypes.CDLL()`, uses raw sockets, exposes C API (`tcp_connect`, `tcp_listen`, `tcp_send`, `tcp_close`, `tcp_abort`). Lifetime managed by Python process.

**New:** loaded by PluginManager (either `BOAT_NODE_PLUGINS` at startup or `RegisterPlugin` at runtime). Config-driven. No C API. All I/O via `on_frame`/`set_frame_publisher`.

```
Application Node (Python SDK)
  → FrameService.SendFrame(Frame{bus_type=TCP, dst_ip, dst_port, payload})
    → FrameBusRegistry
      → TCP Plugin.on_frame()
        → creates/reuses TCP connection
        → sends raw segment
        → publishes Frame{bus_type=TCP, meta.status=OK/NOK} back
```

Server mode:
```
TCP listener socket
  → incoming connection accept
    → receive data
      → TCP Plugin publishes Frame{bus_type=TCP, src_ip, src_port, data}
        → FrameBusRegistry → SubscribeFrames client
```

**Config JSON:**
```json
{
  "iface": "eth0",
  "mode": "server",
  "listen_port": 8080,
  "retry_ms": 1000,
  "max_retries": 5,
  "mss": 1460,
  "time_wait_ms": 120000,
  "rx_window": 65535,
  "nagle": 1,
  "keepalive_idle_ms": 7200000,
  "keepalive_interval_ms": 75000,
  "keepalive_retry_count": 9
}
```

**What stays:**
- TCP state machine (11 states, 3WHS, segmented send, retransmission, Nagle, window tracking, keepalive)
- Raw socket I/O threads (AF_PACKET, ARP, IP checksums)
- Segment builders (IPv4/IPv6)

**What goes:**
- `tcp_connect()` C API → replaced by config-driven connection management
- `tcp_listen()` C API → replaced by config `"mode":"server"` + `"listen_port"`
- `tcp_send()` / `tcp_close()` / `tcp_abort()` C API → all driven by incoming `Frame{bus_type=TCP}`
- `TcpHandle` Python class → deleted (no longer needed)
- `tcp.py` (ctypes wrapper) → deleted
- `tcp_set_callbacks()` C API → replaced by `set_frame_publisher`

### 3.3 Python SDK updates for Phase 3

| Class | Change |
|-------|--------|
| `CanNode` | Keep but internally build `Frame{can:CanFrame}` → FrameService (backward compat) |
| `EthernetNode` | Same pattern |
| `FrameNode` | **New** base class using FrameService directly |
| `PduNode` | Unchanged (uses PduService, which will move with the plugin in Phase 4) |
| `TcpHandle` | Deleted |
| `CanTpHandle` | Keep as-is (ctypes) or migrate to FrameService usage |

### Phase 3 artifacts

| File | Action |
|------|--------|
| `src/plugins/network_sim/*` | Update to v8 |
| `src/plugins/sensor_model/*` | Update to v8 |
| `src/plugins/can_responder/*` | Update to v8 |
| `src/plugins/vehicle_dynamics/*` | Update to v8 |
| `src/plugins/someip/*` | Update to v8 |
| `src/plugins/can_tp/*` | Update to v8 |
| `src/plugins/tcp/*` | Full rewrite (remove C API, add on_frame) |
| `sdk/python/boat/tcp.py` | Delete |
| `sdk/python/boat/frame_node.py` | New |
| `sdk/python/boat/can_node.py` | Refactor to use FrameNode internally |
| `sdk/python/boat/ethernet_node.py` | Same |
| `demo/tcp_plugin/*.py` | Rewrite to use `FrameNode` |
| `cli/boat_cli/can.py` | Add `--bus-type can` to unified frame commands |
| `cli/boat_cli/eth.py` | Same |
| `cli/boat_cli/frame.py` | New unified frame CLI |
| `cli/boat_cli/plugin.py` | Update for v8 plugin info |

---

## Phase 4 — Domain Extraction (PduRouter → Plugin)

### 4.1 What moves out of `src/hil/pdu/`

| Current path | New path |
|-------------|----------|
| `src/hil/pdu/pdu_router.h/.cpp` | `src/plugins/pdu_router/pdu_router_plugin.h/.cpp` |
| `src/hil/pdu/pdu_types.h` | `src/plugins/pdu_router/pdu_types.h` |
| `src/hil/pdu/transmission_engine.h/.cpp` | `src/plugins/pdu_router/transmission_engine.h/.cpp` |
| `src/hil/pdu/ipdumcontainer.h/.cpp` | `src/plugins/pdu_router/ipdumcontainer.h/.cpp` |
| `src/hil/pdu/com/com_signal.h/.cpp` | `src/plugins/pdu_com/com_signal.h/.cpp` |
| `src/hil/pdu/tick_timer.h/.cpp` | **Stays in core** (infrastructure) |

### 4.2 PduRouterPlugin architecture

The PduRouter becomes a plugin that sits on the generic frame bus:

```
                  Frame bus
                     │
     ┌───────────────┴───────────────┐
     │  PduRouter Plugin              │
     │  ┌──────────────────────────┐  │
     │  │ on_frame(Frame) →         │  │
     │  │   if CAN/Eth: route PDU   │  │
     │  │   if PDU:    transmit     │  │
     │  │                           │  │
     │  │ set_frame_publisher →     │  │
     │  │   publish CAN/Eth/PDU     │  │
     │  │                           │  │
     │  │ Internal:                  │  │
     │  │   PduRoutingTable          │  │
     │  │   TransmissionEngine       │  │
     │  │   IpduM containers         │  │
     │  │   PduGroups, Deadlines     │  │
     │  └──────────────────────────┘  │
     └───────────────────────────────┘
```

**Data flow:**

```
INCOMING:
CAN Frame → FrameBusRegistry → PduRouterPlugin.on_frame(Frame{CAN})
  → match can_id → pdu_id
  → gate check (group enabled?)
  → deadline update
  → dispatch to PduRouter subscribers (Frame{PDU})

OUTGOING:
PduRouterPlugin.set_frame_publisher(Frame{PDU, pdu_id, payload})
  → look up PduRoute
  → Frame{ETH} or Frame{CAN} depending on transport
  → FrameBusRegistry → send to bus

CYCLIC/ONCHANGE:
PduRouterPlugin.on_tick(tick)
  → TransmissionEngine::OnTick()
  → for expired schedules: set_frame_publisher(Frame{CAN/ETH})
```

### 4.3 gRPC PduService

Two options (user decides):

**Option A (cleaner): Plugin registers its own gRPC service**
- ABI v8.1 adds `register_grpc_services` to the vtable
- PduRouterPlugin calls `builder->RegisterService(&pdu_service_impl)` during initialization
- No PduService in core `main.cpp`

**Option B (simpler, lower risk): Core delegates**
- `PduServiceImpl` stays in `src/gateway/grpc_gateway/`
- But instead of `ctx_.pdu_router.xxx()`, it calls `ctx_.plugin_manager().FindService("pdu_router")`
- Plugin registers itself as a "service provider" via core API

**Recommendation:** Option A for the final architecture, but Option B as an intermediate step. Implement Option B first (Phase 4a), then Option A (Phase 4b).

### 4.4 What stays in core after Phase 4

After extraction, `GatewayContext` simplifies:

```cpp
struct GatewayContext {
  boat::core::SimulationContext&  sim;
  boat::core::SignalBus&          signal_bus;
  boat::core::ScenarioLoader&     scenario_loader;
  boat::store::SqliteEventStore&  event_store;
  boat::store::FlatFileTraceStore& trace_store;
  boat::replay::ReplayController& replay_controller;
  boat::core::PluginManager&      plugin_manager;   // unified
  RpcAuditLog&                    audit_log;
  // REMOVED: can_bus_registry, ethernet_bus_registry (now plugins)
  // REMOVED: pdu_router (now a plugin)
};
```

The CAN and Ethernet registries move into a "bus plugin" (or stay as infra, user decides). Either way, they're no longer in the top-level GatewayContext — they're managed by the PluginManager.

### Phase 4 artifacts

| File | Action |
|------|--------|
| `src/hil/pdu/` (whole dir) | Move to `src/plugins/pdu_router/` and `src/plugins/pdu_com/` |
| `src/plugins/pdu_router/CMakeLists.txt` | New — `add_boat_plugin(pdu_router ...)` |
| `src/plugins/pdu_com/CMakeLists.txt` | New — `add_boat_plugin(pdu_com ...)` |
| `src/plugins/CMakeLists.txt` | Add `add_subdirectory(pdu_router)` + `add_subdirectory(pdu_com)` |
| `src/hil/CMakeLists.txt` | Remove PduRouter-related source files |
| `src/gateway/grpc_gateway/pdu_service_impl.h/.cpp` | Option A: move to plugin. Option B: refactor to delegate |
| `src/gateway/grpc_gateway/gateway_context.h` | Remove PduRouter, can_bus, ethernet references |
| `src/gateway/grpc_gateway/main.cpp` | Remove PduRouter instantiation + wiring |
| `config/pdu_db_*.json` | Unchanged (loaded by plugin, not core) |

---

## Phase 5 — Cleanup & Documentation

### 5.1 ABI cleanup

| Step | When |
|------|------|
| Remove `set_can_publisher`, `on_can_frame`, `set_eth_publisher`, `on_eth_frame` from vtable | All plugins migrated to v8 |
| Bump `BOAT_PLUGIN_ABI_VERSION` to 8 | After removals |
| Remove fallback dispatch in PluginManager | After bump |

### 5.2 Python SDK cleanup

| File | Action |
|------|--------|
| `sdk/python/boat/tcp.py` | Delete |
| `sdk/python/boat/can_tp.py` | Optional: delete if all users migrate to Frame-based CanTp |
| `sdk/python/boat/frame_node.py` | Made the primary base class |
| `sdk/python/boat/can_node.py` | Kept as backward-compat stub wrapping FrameNode |
| `sdk/python/boat/ethernet_node.py` | Same |

### 5.3 CLI cleanup

| File | Action |
|------|--------|
| `cli/boat_cli/can.py` | Deprecated in favor of `frame.py` |
| `cli/boat_cli/eth.py` | Same |
| `cli/boat_cli/frame.py` | Primary frame CLI |
| `cli/boat_cli/tcp.py` | New — TCP frame commands |

### 5.4 Demo cleanup

| File | Action |
|------|--------|
| `demo/tcp_plugin/tcp_relay.py` | Rewrite using FrameNode |
| `demo/tcp_plugin/tcp_send_client.py` | Rewrite using FrameNode |
| `demo/tcp_plugin/tcp_listen_server.py` | Rewrite using FrameNode |
| `demo/can_responder_node.py` | Keep but internally uses FrameNode |
| `demo/cyclic_sender_node.py` | Same |
| `demo/eth_cyclic_sender_node.py` | Same |

### 5.5 Documentation

| File | Update |
|------|--------|
| `backlog/pdu_gap_analysis.md` | Add v8 architecture section, update simulation posture |
| `AGENTS.md` | Update build/run/plugin docs |
| `sdk/cpp/include/boat/plugin.h` | Doxygen for v8 fields |
| `sdk/python/boat/frame_node.py` | Docstring with usage examples |

---

## Replay Impact Assessment

Replay stays in the core. Changes are minimal.

| Component | Change needed |
|-----------|---------------|
| **ReplayController** (core) | **None.** Event type constants, seek/pause/stream/schedule logic unchanged. |
| **Forwarder lambda** (`main.cpp:260-335`) | **Simplifies.** Three branches (`if ETH → eth_registry`, `if PDU → pdu_router`, `if CAN → can_registry`) become one: `frame_bus_registry.Publish(Frame{event_type, payload})`. |
| **ReplayServiceImpl** (gRPC) | **None.** RPCs are Start, Seek, Stream, Pause, Resume, Stop, Import, FromEvents — none carry frame type definitions. |
| **`replay.proto`** | **None.** No frame types in replay proto. |
| **`trace_replay.py`** (Python) | **Medium.** Builds `frame_pb2.Frame` instead of `can_pb2.CanFrame`. TCP replay path via FrameService instead of TcpHandle. |

New forwarder pseudocode (after refactor):

```cpp
replay_controller.SetEventForwarder(
    [&frame_bus_registry](uint32_t event_type, uint64_t tick,
                          const std::vector<uint8_t>& payload) {
      Frame frame = DecodeEvent(event_type, tick, payload);
      // Replay doesn't discriminate — it publishes to the generic bus.
      // The PduRouter plugin (if loaded) handles PDU frames,
      // CanTp plugin handles CAN frames, etc.
      frame_bus_registry.Publish(std::move(frame));
    });
```

Where `DecodeEvent` converts the replay's binary event format into a `core::Frame`. This is the only new code needed for replay.

---

## Risk Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| Breaking all plugins at once | Critical | v7 fallback in PluginManager allows mixed v7+v8 plugins during transition |
| Regression in simulation determinism | High | Determinism tests (bit-exact replay) run at every phase; Phase 2 (frame dispatch) tested before Phase 3 (plugin migration) |
| gRPC API breakage for clients | High | FrameService is additive. CanService/EthernetService stay. Old clients work unchanged. |
| TCP demo scripts break | Medium | Old TcpHandle + ctypes path stays functional until Phase 5 (final cleanup). New FrameService-based TCP coexists. |
| PduRouter extraction breaks PDU routing | Critical | PduRouter unit tests (Catch2) migrate with the code. Integration test boots gateway with no PduRouter plugin → confirms graceful degradation (PDU RPCs return NOT_FOUND). |
| Plugin config gRPC gap | Medium | `RegisterPluginRequest` currently has no `config_json` field. Fix this in Phase 1 (add field to `plugin.proto`) to support config for all plugins. |
| Replay event format coupling | Medium | Replay's binary format stores event types, not frame types. As long as `DecodeEvent` is updated to produce `core::Frame`, the format on disk doesn't change. |

---

## Dependency Graph

```
                     ┌─────────────┐
                     │ Phase 1      │ Design & ABI v8
                     │ (no runtime) │
                     └──────┬──────┘
                            │
                     ┌──────▼──────┐
                     │ Phase 2      │ Core frame dispatch
                     │ FrameService │ can live alongside v7
                     └──────┬──────┘
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                  │
     ┌────▼────┐     ┌──────▼──────┐    ┌──────▼──────┐
     │ Phase 3a │     │ Phase 3b    │    │ Phase 4     │
     │ Simple   │     │ TCP rewrite │    │ PduRouter   │
     │ plugins  │     │             │    │ extraction  │
     └────┬────┘     └──────┬──────┘    └──────┬──────┘
          │                 │                  │
          └─────────────────┼──────────────────┘
                            │
                     ┌──────▼──────┐
                     │ Phase 5     │ Cleanup
                     │ v7 removal  │ docs, CLI, SDK
                     └─────────────┘
```

Phases 2, 3a, and 3b can run in parallel. Phase 4 depends on Phase 3a (all plugins migrated to v8). Phase 5 depends on everything else.

---

## Effort Estimates

| Phase | Estimated days | Risk level |
|-------|:-------------:|:----------:|
| P1 — Design & ABI v8 | 2-3 | Low |
| P2 — Core frame dispatch | 5-7 | Medium |
| P3a — Simple plugins (1-6) | 5-7 | Medium |
| P3b — TCP rewrite | 7-10 | High |
| P4 — PduRouter extraction | 5-7 | High |
| P5 — Cleanup & docs | 3-5 | Low |
| **Total** | **27-39** | |

---

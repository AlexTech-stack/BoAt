# Phase 2 — Core Frame Dispatch Unification

**Goal:** Wire the new `BoatFrame` type through the PluginManager, bus registries, and gRPC layer. v7 plugins continue to work via fallback dispatch. Additive changes only.

**Subagents needed:** plugin-sdk, cpp-build-test, proto-codegen, hil-testing, e2e-integration, py-sdk-cli

**Dependencies:** Phase 1 complete (`BoatFrame`, `core::Frame`, `frame.proto` exist and build)

---

## Task 2.1 — PluginManager: unified frame dispatch

**Subagent:** plugin-sdk + cpp-build-test

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `src/core/plugin/plugin_manager.h` | Modify | Add new methods: `SetFramePublisher`, `DispatchFrame` |
| `src/core/plugin/plugin_manager.cpp` | Modify | Implement unified dispatch with v7 fallback |
| `src/tests/unit/test_plugin_frames.cpp` | **New** | Unit tests for mixed v7+v8 dispatch |

**Implementation details:**

```cpp
// New callback types
using FramePublishFn = std::function<void(const BoatFrame& frame)>;

class PluginManager {
  // ...existing methods...

  void SetFramePublisher(FramePublishFn fn);
  void DispatchFrame(const BoatFrame& frame);
};
```

**Dispatch logic:**
```
DispatchFrame(frame):
  snapshot = copy plugin pointers (under lock)
  for each plugin in snapshot:
    if plugin.vtable.on_frame != nullptr:
      // v8 path
      buses = if plugin.vtable.declared_buses
                then parse declared_buses(plugin.ctx)
                else ""  (accept all)
      if buses is empty OR frame.bus_type is in buses:
        plugin.vtable.on_frame(plugin.ctx, &frame)
    else:
      // v7 fallback
      if frame.bus_type == CAN:
        if plugin.vtable.on_can_frame != nullptr:
          convert BoatFrame → BoatCanFrame
          plugin.vtable.on_can_frame(plugin.ctx, &can_frame, iface)
      if frame.bus_type == ETHERNET:
        if plugin.vtable.on_eth_frame != nullptr:
          convert BoatFrame → BoatEthFrame
          plugin.vtable.on_eth_frame(plugin.ctx, &eth_frame, iface)

SetFramePublisher(fn):
  for each plugin:
    if plugin.vtable.set_frame_publisher != nullptr:
      plugin.vtable.set_frame_publisher(plugin.ctx, trampoline, fn_ptr)
    else:
      // v7 fallback: also wire CAN/Eth publishers as before
      (existing wiring kept)
```

**Acceptance criteria:**
- v7 plugin (`can_responder`) receives CAN frames via v7 path when loaded alongside v8 wiring
- v8 plugin (`can_responder` rewritten) receives CAN frames via `on_frame`
- Mixed test: one v7 plugin + one v8 plugin, both receive appropriate frames
- `test_plugin_frames` unit test passes all dispatch scenarios

---

## Task 2.2 — CanBusRegistry: add Frame subscriber

**Subagent:** hil-testing

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `src/hil/can_bus_registry.h` | Modify | Add `SubscribeFrame`/`UnsubscribeFrame` |
| `src/hil/can_bus_registry.cpp` | Modify | Frame-based dispatch |

**Implementation details:**

```cpp
class CanBusRegistry {
 public:
  // ...existing Subscribe(SendFrame) methods...

  using FrameCallback = std::function<void(const core::Frame&)>;
  SubId SubscribeFrame(FrameCallback cb);
  void UnsubscribeFrame(SubId id);
};
```

**SubscribeFrame implementation:** wraps the existing `Subscribe("", ...)` — when a `hil::CanFrame` arrives, it converts to `core::Frame::FromCan()` and calls the subscriber callback. The conversion macro/function is the one from Phase 1 Task 1.2.

**Acceptance criteria:**
- `SubscribeFrame` delivers correctly converted `core::Frame` objects
- CAN-specific fields (can_id, dlc, flags) survive the conversion
- Existing `Subscribe` method still works (for v7 backward compat)
- Multiple Frame subscribers can coexist

---

## Task 2.3 — EthernetBusRegistry: add Frame subscriber

**Subagent:** hil-testing

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `src/hil/ethernet_bus_registry.h` | Modify | Add `SubscribeFrame`/`UnsubscribeFrame` |
| `src/hil/ethernet_bus_registry.cpp` | Modify | Frame-based dispatch |

Same pattern as CanBusRegistry: wraps existing `Subscribe("", 0, ...)`, converts `hil::EthernetFrame` → `core::Frame::FromEthernet()`.

**Acceptance criteria:**
- `SubscribeFrame` delivers correctly converted `core::Frame` objects
- MAC addresses, VLAN tags, IP addresses survive the conversion
- Existing `Subscribe` method still works

---

## Task 2.4 — FrameService gRPC implementation

**Subagent:** proto-codegen

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `src/gateway/grpc_gateway/frame_service_impl.h` | **New** | FrameService impl declaration |
| `src/gateway/grpc_gateway/frame_service_impl.cpp` | **New** | FrameService impl |
| `src/gateway/grpc_gateway/CMakeLists.txt` | Modify | Add new files |

**Contents:**

```cpp
class FrameServiceImpl final : public boat::v1::FrameService::Service {
 public:
  explicit FrameServiceImpl(GatewayContext& ctx);

  grpc::Status SendFrame(grpc::ServerContext*, const SendFrameRequest*,
                         SendFrameResponse*) override;
  grpc::Status SubscribeFrames(grpc::ServerContext*,
                               const SubscribeFramesRequest*,
                               grpc::ServerWriter<Frame>*) override;

 private:
  GatewayContext& ctx_;
};
```

**SendFrame:**
- Convert `boat::v1::Frame` proto → `core::Frame`
- Route to correct bus registry by `bus_type`:
  - CAN/CANFD → `ctx_.can_bus_registry.SendFrame()`
  - ETHERNET → `ctx_.ethernet_bus_registry.SendFrame()`
  - TCP/PDU → publish to PluginManager frame bus (for plugins to handle)
- Return `accepted = true` on success, NOT_FOUND if bus/interface not available

**SubscribeFrames:**
- Filter by `request.bus_types()` (empty = all)
- Subscribe to `can_bus_registry.SubscribeFrame()` and `eth_bus_registry.SubscribeFrame()`
- Push converted `Frame` protos to `grpc::ServerWriter`
- Handle cancellation cleanly (unsubscribe both)

**Acceptance criteria:**
- `SendFrame(CanFrame)` reaches `can_bus_registry`
- `SendFrame(EthernetFrame)` reaches `eth_bus_registry`
- `SubscribeFrames` streams both CAN and Ethernet frames
- Filter by `bus_types = [CAN]` excludes Ethernet frames
- Error on unknown `bus_type` returns appropriate gRPC status

---

## Task 2.5 — main.cpp wiring

**Subagent:** cpp-build-test

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `src/gateway/grpc_gateway/main.cpp` | Modify | Wire PluginManager for v8, keep v7 wiring |

**Changes (alongside existing code — nothing removed):**

```cpp
// Existing v7 wiring (lines 183-253): KEPT AS-IS
// New v8 wiring added after:

// Wire frame publisher for node plugins
node_manager.SetFramePublisher([&can_registry, &eth_registry](const BoatFrame& frame) {
  switch (frame.bus_type) {
    case BOAT_BUS_CAN:
    case BOAT_BUS_CANFD: {
      hil::CanFrame cf{};
      cf.can_id = frame.meta.can.can_id;
      cf.dlc    = frame.meta.can.dlc;
      cf.flags  = frame.meta.can.flags;
      memcpy(cf.data, frame.payload, std::min(frame.payload_len, 64UL));
      if (frame.iface && frame.iface[0])
        can_registry.SendFrame(frame.iface, cf);
      else
        can_registry.SendFrameAll(cf);
      break;
    }
    case BOAT_BUS_ETHERNET: {
      // Build EthernetFrame and send via eth_registry
      break;
    }
    // TCP and PDU go to plugin dispatch, not a hardware bus
  }
});

// Frame-based subscriptions for PDU traffic
can_registry.SubscribeFrame([&node_manager](const core::Frame& f) {
  BoatFrame bf; f.ToAbi(&bf);
  node_manager.DispatchFrame(bf);
});
eth_registry.SubscribeFrame([&node_manager](const core::Frame& f) {
  BoatFrame bf; f.ToAbi(&bf);
  node_manager.DispatchFrame(bf);
});

// Register FrameService (alongside existing CanService/EthernetService)
boat::gateway::FrameServiceImpl frame_impl(ctx);
builder.RegisterService(&frame_impl);
```

**Acceptance criteria:**
- Gateway compiles with new includes
- Gateway starts up with existing v7 plugin(s) loaded
- Gateway starts up WITHOUT any plugins (graceful degradation)
- `boat frame send ...` works via CLI (see Task 2.7)
- Existing `boat can send ...` and `boat eth send ...` still work

---

## Task 2.6 — GatewayContext update

**Subagent:** docs-arch

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `src/gateway/grpc_gateway/gateway_context.h` | Modify | Verify FrameService can access registries |

Existing `GatewayContext` already has `can_bus_registry` and `ethernet_bus_registry` references — no changes needed for Phase 2. The `FrameServiceImpl` can access both through `ctx`.

**Acceptance criteria:**
- `FrameServiceImpl` compiles with `#include "gateway_context.h"`
- No new members added to `GatewayContext` in Phase 2

---

## Task 2.7 — Python frame_node.py (SDK + CLI)

**Subagent:** py-sdk-cli

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `sdk/python/boat/frame_node.py` | **New** | FrameNode base class |
| `cli/boat_cli/frame.py` | **New** | `boat frame send` / `boat frame subscribe` CLI |
| `cli/boat_cli/main.py` | Modify | Add `frame` subcommand group |
| `sdk/python/boat/client.py` | Modify | Add `self.frame` stub |

**frame_node.py:**
```python
class FrameNode:
    """Base class for nodes using the unified FrameService."""
    def __init__(self, address="localhost:50051", bus_types=None):
        self._client = BoAtClient(address)
        self._bus_types = bus_types or []

    def send(self, frame: Frame) -> bool:
        req = frame_pb2.SendFrameRequest(frame=frame)
        resp = self._client.frame.SendFrame(req)
        return resp.accepted

    def subscribe(self, callback, bus_types=None, iface_filter=""):
        # streaming RPC in background thread
        ...

    def run(self):
        # block on subscription
        ...
```

**Acceptance criteria:**
- `frame_node.send(Frame(CAN, can_id=0x123, data=b"hello"))` sends via FrameService
- `frame_node.subscribe(callback, bus_types=[CAN])` receives CAN frames
- `boat frame send --bus-type can --can-id 0x123 --data AABB` works from CLI
- `boat frame subscribe --bus-type can` streams frames

---

## Task 2.8 — Integration tests (Phase 2)

**Subagent:** e2e-integration

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `src/tests/integration/test_frame_service.cpp` | **New** | Gateway integration tests |

**Test cases:**
1. **Gateway starts with FrameService** — verify `FrameService` responds on port 50051
2. **SendFrame CAN round trip** — send CAN frame via FrameService, verify it appears on bus
3. **SubscribeFrames filter** — subscribe to CAN only, verify no Ethernet frames arrive
4. **v7 backward compat** — load `can_responder_v7.so`, send CAN frame via CanService, verify response via CanService subscription
5. **Mixed v7+v8** — load one v7 plugin + one v8 plugin, verify both receive appropriate frames
6. **Concurrent subscribers** — two SubscribeFrames clients, both receive the same frames

**Acceptance criteria:**
- All 6 test cases pass
- `ctest -R test_frame_service --output-on-failure`

---

## Phase 2 — File Change Summary

| File | Action | Lines (est.) |
|------|--------|:-----------:|
| `src/core/plugin/plugin_manager.h` | Modify | +10 |
| `src/core/plugin/plugin_manager.cpp` | Modify | +60 |
| `src/hil/can_bus_registry.h` | Modify | +5 |
| `src/hil/can_bus_registry.cpp` | Modify | +30 |
| `src/hil/ethernet_bus_registry.h` | Modify | +5 |
| `src/hil/ethernet_bus_registry.cpp` | Modify | +30 |
| `src/gateway/grpc_gateway/frame_service_impl.h` | New | ~40 |
| `src/gateway/grpc_gateway/frame_service_impl.cpp` | New | ~200 |
| `src/gateway/grpc_gateway/CMakeLists.txt` | Modify | +2 |
| `src/gateway/grpc_gateway/main.cpp` | Modify | +50 |
| `sdk/python/boat/frame_node.py` | New | ~100 |
| `sdk/python/boat/client.py` | Modify | +3 |
| `cli/boat_cli/frame.py` | New | ~150 |
| `cli/boat_cli/main.py` | Modify | +3 |
| `src/tests/unit/test_plugin_frames.cpp` | New | ~120 |
| `src/tests/integration/test_frame_service.cpp` | New | ~200 |
| **Total** | | **~1,008** |

**Risk:** Medium — Core dispatch path changes. Existing CAN/Ethernet paths unchanged. v7 fallback ensures backward compat.

**Build impact:** `boat_core` (modified), `boat_gateway` (modified), new test binaries. Python stubs regenerated with FrameService.

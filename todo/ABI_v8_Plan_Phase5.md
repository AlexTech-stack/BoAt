# Phase 5 — Cleanup & Documentation

**Goal:** Remove v7 ABI (all plugins migrated), clean up Python SDK (delete TcpHandle ctypes wrapper), update CLI and demos, regenerate docs. The final state: a minimal core gateway with a generic frame bus, and all domain logic in plugins.

**Subagents needed:** plugin-sdk, cpp-build-test, py-sdk-cli, docs-arch, spec-reference, e2e-integration

**Dependencies:** Phases 1-4 complete

---

## Task 5.1 — Remove v7 ABI from plugin.h

**Subagent:** plugin-sdk

### Task 5.1.1 — Remove deprecated vtable entries

Once ALL plugins (including third-party) have migrated to v8:

```c
// REMOVED from BoatPluginVTable:
void (*set_can_publisher)(void* ctx, BoatCanPublishFn fn, void* pub_ctx);
BoatCanReceiveFn on_can_frame;
void (*set_eth_publisher)(void* ctx, BoatEthPublishFn fn, void* pub_ctx);
BoatEthReceiveFn on_eth_frame;
```

### Task 5.1.2 — Bump ABI version

```c
#define BOAT_PLUGIN_ABI_VERSION 8
```

Update `PluginManager::Load()` to reject ABI versions < 8 (fail with clear error: "Plugin ABI v7 is no longer supported. Update your plugin to v8.").

### Task 5.1.3 — Remove v7 dispatch fallback

Remove `DispatchFrame`'s fallback code that converts `BoatFrame` → `BoatCanFrame`/`BoatEthFrame` and calls `on_can_frame`/`on_eth_frame`.

### Task 5.1.4 — Clean up deprecated typedefs

```c
// REMOVED typedefs (move to deprecated.h for migration doc reference):
typedef void (*BoatCanPublishFn)(...);
typedef BoatCanReceiveFn;
typedef void (*BoatEthPublishFn)(...);
typedef BoatEthReceiveFn;
```

**Acceptance criteria:**
- `sizeof(BoatPluginVTable)` reduced by 4 pointer fields (-32 bytes on 64-bit)
- Loading a v7 plugin returns error "ABI version 7 is no longer supported"
- Loading a v8 plugin works
- All gate tests pass

**Rollback plan:** If any third-party plugin still needs v7, revert this task and keep v7 fallback indefinitely.

---

## Task 5.2 — Python SDK Cleanup

**Subagent:** py-sdk-cli

### Task 5.2.1 — Delete TcpHandle (ctypes wrapper)

| File | Action | Description |
|------|--------|-------------|
| `sdk/python/boat/tcp.py` | **Delete** | No longer needed — TCP is now via FrameService |

### Task 5.2.2 — Deprecate CanNode/EthernetNode

| File | Action | Description |
|------|--------|-------------|
| `sdk/python/boat/can_node.py` | Modify | Add `DeprecationWarning`, delegate to FrameNode internally |
| `sdk/python/boat/ethernet_node.py` | Modify | Same |

```python
# can_node.py
class CanNode(FrameNode):
    def __init__(self, **kwargs):
        import warnings
        warnings.warn("CanNode is deprecated. Use FrameNode(bus_types=[CAN]) instead.",
                      DeprecationWarning, stacklevel=2)
        super().__init__(bus_types=[frame_pb2.Frame.CAN], **kwargs)
```

### Task 5.2.3 — FrameNode becomes primary

| File | Action | Description |
|------|--------|-------------|
| `sdk/python/boat/frame_node.py` | Modify | Add full documentation, examples |
| `sdk/python/boat/__init__.py` | Modify | Export FrameNode, TcpNode |

### Task 5.2.4 — CanTpHandle decision

Keep `boat/can_tp.py` for now (it wraps the C API which still exists). If the CanTp plugin becomes fully frame-driven in the future, remove it then.

**Acceptance criteria:**
- `from boat import FrameNode, TcpNode` works
- `CanNode` works but prints DeprecationWarning
- `TcpHandle` is gone
- No `import boat.tcp` succeeds (ModuleNotFoundError)
- All existing Python tests updated

---

## Task 5.3 — CLI Cleanup

**Subagent:** py-sdk-cli

### Task 5.3.1 — `boat frame` becomes the recommended command

| File | Action | Description |
|------|--------|-------------|
| `cli/boat_cli/frame.py` | Modify | Add `--help` with examples, deprecation notes |
| `cli/boat_cli/can.py` | Modify | Add deprecation note: `(use 'boat frame send --bus-type can' instead)` |
| `cli/boat_cli/eth.py` | Modify | Same |
| `cli/boat_cli/pdu.py` | Modify | Add note: `(PduRouter is now a plugin; load pdu_router.so to use PDU commands)` |

### Task 5.3.2 — `boat tcp` commands

| File | Action | Description |
|------|--------|-------------|
| `cli/boat_cli/tcp.py` | Finalize | `boat tcp register-plugin`, `boat tcp send`, `boat tcp listen` |
| `cli/boat_cli/main.py` | Modify | Add `tcp` subcommand group |

### Task 5.3.3 — `boat plugin` config support

| File | Action | Description |
|------|--------|-------------|
| `cli/boat_cli/plugin.py` | Modify | `--config` flag with JSON string support |
| `proto/boat/v1/plugin.proto` | Modify | Add `string config_json = 2` to `RegisterPluginRequest` |
| `src/gateway/grpc_gateway/plugin_service_impl.cpp` | Modify | Pass `request->config_json()` instead of `"{}"` |

**Acceptance criteria:**
- `boat frame send --bus-type can --can-id 0x123 --data AABB` works
- `boat frame subscribe --bus-types can,eth` works
- `boat can send ...` works with deprecation notice
- `boat tcp register-plugin` / `boat tcp send` / `boat tcp listen` work
- `boat plugin register foo.so --config '{"mode":"server"}'` passes config

---

## Task 5.4 — Demo Scripts Update

**Subagent:** py-sdk-cli

### Task 5.4.1 — TCP demos (complete rewrite)

| File | Action | Description |
|------|--------|-------------|
| `demo/tcp_plugin/tcp_send_client.py` | **Rewrite** | Uses FrameNode + TcpNode, sends via FrameService |
| `demo/tcp_plugin/tcp_listen_server.py` | **Rewrite** | Uses FrameNode + TcpNode, listens via FrameService |
| `demo/tcp_plugin/tcp_relay.py` | **Rewrite** | Uses FrameNode, relays between two connections |

No more `import boat.tcp; TcpHandle(so_path)` — just `from boat import TcpNode`.

### Task 5.4.2 — CAN/Ethernet demos (minimal changes)

| File | Action | Description |
|------|--------|-------------|
| `demo/can_responder_node.py` | Modify | Update import (stays on CanNode) |
| `demo/cyclic_sender_node.py` | Modify | Same |
| `demo/eth_cyclic_sender_node.py` | Modify | Same |
| `demo/restbus_simulator.py` | Modify | Update PDU route configuration to use plugin-loading |

### Task 5.4.3 — New v8 demo

| File | Action | Description |
|------|--------|-------------|
| `demo/frame_node_demo.py` | **New** | Demonstrates FrameNode for CAN + Ethernet |
| `demo/tcp_frame_demo.py` | **New** | Demonstrates TCP via FrameService |

**Acceptance criteria:**
- All 3 TCP demos work without TcpHandle
- `python3 demo/can_responder_node.py` works unchanged
- New `frame_node_demo.py` runs and shows CAN/Eth output

---

## Task 5.5 — Documentation Updates

**Subagent:** docs-arch

### Task 5.5.1 — AGENTS.md

| Section | Update |
|---------|--------|
| Build & Run | Add v8 plugin loading instructions |
| Plugin ABI | v8 documentation with code examples |
| Repository structure | Updated directory tree (PduRouter moved) |
| PDU Features | Note: PduRouter is now a plugin |
| Quirks | v7→v8 migration notes |

### Task 5.5.2 — Plugin ABI documentation (plugin.h)

Add comprehensive Doxygen comments:
- VTable field descriptions for v8
- `BoatFrame` struct with bus type diagrams
- Migration guide from v7 to v8 (code examples)
- `declared_buses` format specification

### Task 5.5.3 — New architecture document

| File | Action | Description |
|------|--------|-------------|
| `docs/architecture.md` | **New** | End-to-end architecture: core, plugin ABI, frame types, data flow |

### Task 5.5.4 — Backlog update

| File | Action | Description |
|------|--------|-------------|
| `backlog/pdu_gap_analysis.md` | Modify | Update architecture section, note which gaps are addressed by the refactor |

**Acceptance criteria:**
- `AGENTS.md` references v8 ABI correctly
- `plugin.h` has complete v8 documentation
- New developer can understand the architecture from `docs/architecture.md`

---

## Task 5.6 — Spec Reassessment

**Subagent:** spec-reference

Now that the refactor is complete, reassess the PDU gap analysis to update what gaps were addressed:

| Gap | Status after refactor |
|-----|----------------------|
| **CAN3** (DLC encoding) | Still Tier A core fix — unchanged by this refactor |
| **CAN4** (BRS flag) | Still Tier A core fix — unchanged |
| **ETH2** (SHORT header) | Still Tier A core fix — unchanged |
| **ETH4** (IPv6 ext headers) | Still Tier A core fix — unchanged |
| **CC1** (PduR routing table) | **ADDRESSED** — PduRouter is now a plugin; structured routing can be implemented without core changes |
| **CC2** (Signal gateway) | **ADDRESSED** — Signal gateway now a clean plugin slot |
| **CC3** (E2E state machines) | **ADDRESSED** — E2E plugin slot created |
| **CC4** (SecOC) | **ADDRESSED** — SecOC traffic gen plugin slot created |
| **CC5** (Callback API) | **Addressable** — gRPC remains the API; plugin internals use frame bus |
| **CC6-CC11** | **Addressable** — All COM features now in plugin domain |
| **CC12** (Buffering) | **Addressable** — PduRouter buffering as plugin internal concern |
| **CC13** (Multicast) | **Addressable** — PduRouter plugin can implement multi-destination |

**Acceptance criteria:**
- Updated gap analysis reflects which gaps are now plugin opportunities
- "Simulation posture" annotations updated

---

## Task 5.7 — Final Integration Test

**Subagent:** e2e-integration

Full-stack test: boot the gateway with plugins, run the simulation, verify everything.

```bash
# Start gateway with PduRouter + CanTp + TCP plugins
BOAT_CAN_INTERFACES=vcan0 \
BOAT_NODE_PLUGINS=./build/debug/src/plugins/pdu_router/pdu_router.so,\
./build/debug/src/plugins/can_tp/can_tp.so?{"iface":"vcan0"},\
./build/debug/src/plugins/tcp/tcp.so?{"mode":"server","listen_port":9999,"iface":"eth0"} \
./build/debug/src/gateway/grpc_gateway/boat_gateway

# Test 1: PDU routing (CAN → PDU → subscriber)
boat pdu route --id 0x300 --transport can --iface vcan0
boat can send 0x300 AABBCCDD
boat pdu subscribe  # should see PduFrame with pdu_id=0x300

# Test 2: CanTp (segmented send)
boat can-tp configure --nsdu-id test --source-addr 0x7E0 --target-addr 0x7E8
boat can-tp send --nsdu-id test --data <255 bytes hex>
# Expect: SF/FF+CF on vcan0, reassembled PDU via PduRouter

# Test 3: FrameService (unified frame send/subscribe)
boat frame send --bus-type can --can-id 0x100 --data 0102
boat frame subscribe --bus-types can,eth
# Expect: stream of CAN and Ethernet frames

# Test 4: TCP (via FrameService)
boat tcp register-plugin /path/to/tcp.so --config '{"mode":"listener","port":9999}'
boat tcp send --dst-ip 127.0.0.1 --dst-port 9999 --data "hello"
# Expect: OK status response
```

**Acceptance criteria:**
- All smoke tests pass
- No regression in existing CI tests
- No crash/leak under valgrind (full heap check)

---

## Phase 5 — File Change Summary

| File | Action | Lines (est.) |
|------|--------|:-----------:|
| `sdk/cpp/include/boat/plugin.h` | Modify (remove v7) | -20 |
| `src/core/plugin/plugin_manager.cpp` | Modify (remove fallback) | -60 |
| `sdk/python/boat/tcp.py` | Delete | -164 |
| `sdk/python/boat/can_node.py` | Modify (deprecation + delegate) | +15 |
| `sdk/python/boat/ethernet_node.py` | Modify | +15 |
| `sdk/python/boat/frame_node.py` | Modify (docs) | +40 |
| `sdk/python/boat/__init__.py` | Modify | +3 |
| `cli/boat_cli/can.py` | Modify (deprecation note) | +2 |
| `cli/boat_cli/eth.py` | Modify | +2 |
| `cli/boat_cli/pdu.py` | Modify | +2 |
| `cli/boat_cli/frame.py` | Modify (help text) | +20 |
| `cli/boat_cli/tcp.py` | Modify (finalize) | +20 |
| `cli/boat_cli/main.py` | Modify | +2 |
| `cli/boat_cli/plugin.py` | Modify (--config) | +10 |
| `proto/boat/v1/plugin.proto` | Modify | +1 |
| `src/gateway/grpc_gateway/plugin_service_impl.cpp` | Modify | +2 |
| `demo/tcp_plugin/tcp_send_client.py` | Rewrite | ~80 |
| `demo/tcp_plugin/tcp_listen_server.py` | Rewrite | ~80 |
| `demo/tcp_plugin/tcp_relay.py` | Rewrite | ~100 |
| `demo/can_responder_node.py` | Modify (import) | +1 |
| `demo/restbus_simulator.py` | Modify | +5 |
| `demo/frame_node_demo.py` | New | ~80 |
| `demo/tcp_frame_demo.py` | New | ~80 |
| `docs/architecture.md` | New | ~300 |
| `AGENTS.md` | Modify | ~50 |
| `backlog/pdu_gap_analysis.md` | Modify | ~30 |
| **Total** | | **~700 (net deletion)** |

**Risk:** Low — Cleanup only. All breaking changes have workarounds. Deprecation warnings give users migration time.

**Build impact:** `boat_gateway` rebuilt (fewer includes). `plugin.h` updated. Python stubs regenerated (plugin.proto change). Demo scripts updated.

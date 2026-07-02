# Phase 3 — Plugin Migration to v8

**Goal:** Migrate all 7 existing plugins to the v8 ABI with unified `on_frame`/`set_frame_publisher`. Rewrite the TCP plugin as a gateway-resident transport (no standalone C API). Update the Python SDK and CLI to use FrameService where applicable.

**Subagents needed:** plugin-sdk, cpp-build-test, hil-testing, e2e-integration, py-sdk-cli

**Dependencies:** Phase 2 complete (unified frame dispatch running in gateway with v7 fallback)

---

## Phase 3a — Simple Plugins (1-6)

### Task 3a.1 — network_sim (Trivial)

**Subagent:** plugin-sdk

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `src/plugins/network_sim/network_sim_plugin.h` | Modify | Update VTable init |
| `src/plugins/network_sim/network_sim_plugin.cpp` | Modify | Add `declared_buses`, zero `on_frame` |

**Work:**
- `declared_buses` returns `""` (no bus I/O)
- `on_frame` = `nullptr` (plugin only uses `on_tick`)
- `set_frame_publisher` = `nullptr`
- All v1-v7 fields unchanged except `on_can_frame`/`on_eth_frame` → `nullptr`

**Acceptance criteria:**
- Plugin loads without errors in gateway
- `on_tick` called on schedule
- Config `{"protocol":"CAN","bus_load_percent":50}` still works

---

### Task 3a.2 — sensor_model (Trivial)

**Subagent:** plugin-sdk

Same pattern as network_sim. No bus I/O, only `on_tick`.

---

### Task 3a.3 — can_responder (Low)

**Subagent:** plugin-sdk + hil-testing

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `src/plugins/can_responder/can_responder_plugin.h` | Modify | VTable init, add `declared_buses` |
| `src/plugins/can_responder/can_responder_plugin.cpp` | Modify | `on_frame` replaces `on_can_frame`, `set_frame_publisher` replaces `set_can_publisher` |

**Work:**
- `declared_buses` returns `"[\"can\"]"`
- `on_frame` handler:
  ```
  if frame.bus_type == CAN:
    if frame.meta.can.can_id == 0x123:
      build response CAN frame
      set_frame_publisher(response_frame)
  ```
- `set_frame_publisher` wired to `set_frame_publisher` callback (replaces `set_can_publisher`)
- Remove: `set_can_publisher` vtable slot → set to `nullptr`
- Remove: `on_can_frame` vtable slot → set to `nullptr`
- Keep: `initialize`, `on_tick`, `shutdown` unchanged

**Acceptance criteria:**
- Plugin loads as v8
- Receiving 0x123 on vcan1 triggers 0x234 response
- Response appears on CAN bus (verified via `boat can subscribe` or FrameService)
- Old v7 path: `responder_on_can_frame` no longer called (verified via test)

---

### Task 3a.4 — vehicle_dynamics (Medium)

**Subagent:** plugin-sdk + hil-testing

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `src/plugins/vehicle_dynamics/vehicle_dynamics_plugin.h` | Modify | VTable init |
| `src/plugins/vehicle_dynamics/vehicle_dynamics_plugin.cpp` | Modify | Rewrite publish to use `set_frame_publisher` |

**Work:**
- `declared_buses` returns `"[\"can\",\"eth\"]"`
- `set_frame_publisher` wired (replaces multiple individual publishers)
- On each tick:
  - Build `Frame{CAN, can_id=0x100, data=speed}` → `publish(frame)`
  - Build `Frame{CAN, can_id=0x101, data=rpm}` → `publish(frame)`
  - Build `Frame{ETH, ethertype=0x0800, data=...}` → `publish(frame)`
- Signal publisher via `set_publisher` (v1) — **unchanged** (signals are orthogonal to frames)
- Bus publisher via `set_bus_publisher` (v5) — **unchanged**
- Remove: `set_can_publisher`, `set_eth_publisher` → `nullptr`

**Acceptance criteria:**
- Speed/rpm CAN frames (0x100, 0x101) appear on bus
- `boat.v1.speed` / `boat.v1.rpm` signals still published via SignalService
- Ethernet frames still published

---

### Task 3a.5 — someip (Low)

**Subagent:** plugin-sdk + hil-testing

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `src/plugins/someip/someip_plugin.h` | Modify | VTable init |
| `src/plugins/someip/someip_plugin.cpp` | Modify | `on_frame` + `set_frame_publisher` |

**Work:**
- `declared_buses` returns `"[\"eth\"]"`
- `on_frame` replaces `on_eth_frame` — filters by `bus_type == ETHERNET`
- `set_frame_publisher` replaces `set_eth_publisher`
- SOME/IP message parsing unchanged (still inspects UDP payload and SD header)
- Remove: `set_eth_publisher`, `on_eth_frame` → `nullptr`

**Acceptance criteria:**
- SOME/IP SD and REQUEST/RESPONSE still work
- Plugin loads via `RegisterPlugin` gRPC with config `{"sd_port":30490}`

---

### Task 3a.6 — can_tp (Medium)

**Subagent:** plugin-sdk + hil-testing

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `src/plugins/can_tp/can_tp_plugin.h` | Modify | VTable init, keep C API |
| `src/plugins/can_tp/can_tp_plugin.cpp` | Modify | `on_frame` + `set_frame_publisher` |

**Work:**
- `declared_buses` returns `"[\"can\"]"`
- `on_frame` replaces `on_can_frame` — filters by `bus_type == CAN`
- `set_frame_publisher` replaces `set_can_publisher` + `set_pdu_publisher`
  - For reassembled PDUs: `BoatFrame{PDU, pdu_id, payload}` via frame publisher
  - For outgoing CAN segments: `BoatFrame{CAN, can_id, data, flags=0x08}` via frame publisher
- ISO 15765-2 logic **unchanged** (segmentation, reassembly, flow control)
- Keep: `can_tp_send()` and `can_tp_configure()` C API for backward compat (CanTpHandle from Python)
- Remove: `set_can_publisher`, `on_can_frame`, `set_pdu_publisher` → `nullptr`

**Acceptance criteria:**
- CanTp segmentation still works (send 255-byte PDU → segmented into SF+FF+CF)
- Reassembled PDU appears as `Frame{BUS_PDU}` on frame bus
- Old C API (`can_tp_send`, `can_tp_configure`) still works via ctypes

---

## Phase 3b — TCP Plugin Rewrite

### Task 3b.1 — Remove standalone C API

**Subagent:** plugin-sdk

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `src/plugins/tcp/tcp_plugin.h` | Modify | Remove `tcp_connect`, `tcp_listen`, `tcp_send`, `tcp_close`, `tcp_abort`, `tcp_set_callbacks` declarations |
| `sdk/cpp/include/boat/tcp.h` | Delete | No longer needed — TCP is now bus-transparent |
| `sdk/python/boat/tcp.py` | Delete (Phase 5) | Keep functional during transition, delete in Phase 5 |

---

### Task 3b.2 — Design TCP plugin config JSON

**Subagent:** plugin-sdk

```
Config:
{
  "iface": "eth0",
  "mode": "server",             // or "client"
  "listen_ip": "0.0.0.0",      // server mode: bind address
  "listen_port": 8080,          // server mode: listen port
  "connect_ip": "10.0.0.1",    // client mode: destination (or driven by frame metadata)
  "connect_port": 9999,         // client mode: destination port
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

**Acceptance criteria:**
- Plugin parses and validates config
- Unknown keys produce warning, not error
- Sensible defaults for all optional fields

---

### Task 3b.3 — Implement frame-driven TCP send path (client mode)

**Subagent:** plugin-sdk + hil-testing

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `src/plugins/tcp/tcp_plugin.cpp` | Major rewrite | `on_frame` → establish connection + send |

**Data flow:**
```
on_frame(frame):
  if frame.bus_type != TCP: return

  // Extract connection info from frame metadata
  dst_ip   = frame.meta.tcp.dst_ip
  dst_port = frame.meta.tcp.dst_port
  src_ip   = frame.meta.tcp.src_ip || config.default_src_ip
  src_port = frame.meta.tcp.src_port || ephemeral

  // Find or create TCP connection
  conn = connection_pool[dst_ip:dst_port]
  if not conn:
    conn = tcp_connect_raw(src_ip, src_port, dst_ip, dst_port)
    connection_pool[dst_ip:dst_port] = conn

  // Send payload as TCP segment
  result = conn.send(frame.payload, frame.payload_len)

  // Publish response frame
  response = BoatFrame{
    TCP, frame.iface,
    {conn_status: result.ok ? OK : NOK, error_code: result.err},
    nullptr, 0
  }
  set_frame_publisher(response)
```

**What stays from old code:**
- Raw socket creation, ARP resolution, TCP state machine
- Segment building (IPv4/IPv6 headers, TCP header, checksums)
- Nagle's algorithm, retransmission with exponential backoff
- Keepalive probes, TIME_WAIT cleanup

**What's new:**
- Connection lifecycle driven by incoming frames, not API calls
- Connection pooling by (dst_ip, dst_port)
- Status reporting via response frames
- No application-level callbacks — all communication via frame bus

**Acceptance criteria:**
- `SendFrame(TCP, dst_ip="10.0.0.1", dst_port=8080, payload="hello")` sends TCP segment
- Response frame with OK or error code arrives back
- Connection reuse: second send to same (ip, port) uses existing connection
- Frame with `conn_id = -2` means "close this connection"

---

### Task 3b.4 — Implement frame-driven TCP receive path (server mode)

**Subagent:** plugin-sdk + hil-testing

**Data flow:**
```
Server mode (config mode = "server"):
  On startup: bind raw socket to listen_port
  Accept loop thread:
    Incoming SYN → complete 3WHS
    Data arrives → build Frame:
      Frame{
        TCP,
        iface = config.iface,
        meta = {src_ip, dst_ip, src_port, dst_port, conn_id},
        payload = received_data
      }
    → publish frame via set_frame_publisher
    Connection close → publish Frame{conn_id, conn_status=CLOSED}
```

**Acceptance criteria:**
- Plugin in server mode accepts connections on `listen_port`
- Received data produces `Frame{TCP, src_ip, src_port, payload}` on frame bus
- Connection close produces status frame
- Multiple concurrent connections handled

---

### Task 3b.5 — Implement VTable for v8 TCP plugin

**Subagent:** plugin-sdk

```cpp
BoatPluginVTable kTcpV8VTable = {
  .initialize           = tp_initialize,
  .on_tick              = tp_on_tick,       // wakes TX thread
  .shutdown             = tp_shutdown,
  .set_publisher        = nullptr,           // no signal publish
  .set_can_publisher    = nullptr,           // removed (v8)
  .on_can_frame         = nullptr,           // removed (v8)
  .set_eth_publisher    = nullptr,           // removed (v8)
  .on_eth_frame         = nullptr,           // removed (v8)
  .set_bus_publisher    = nullptr,
  .set_pdu_publisher    = nullptr,
  // v8 fields:
  .on_frame             = tp_on_frame,       // receives TCP frames from bus
  .set_frame_publisher  = tp_set_frame_publisher,  // publishes TCP frames to bus
  .declared_buses       = tp_declared_buses, // returns "[\"tcp\"]"
};
```

**Acceptance criteria:**
- Plugin loads via `RegisterPlugin` with config JSON
- Plugin loads via `BOAT_NODE_PLUGINS` env var
- ABI version check passes (plugin reports v7, vtable size implies v8)

---

## Phase 3c — Python SDK Updates

### Task 3c.1 — frame_node.py usage in existing nodes

**Subagent:** py-sdk-cli

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `sdk/python/boat/frame_node.py` | Modify | (written in Phase 2) — add TCP convenience methods |
| `sdk/python/boat/can_node.py` | Modify | Internally delegate to FrameNode (keep public API) |
| `sdk/python/boat/ethernet_node.py` | Modify | Same pattern |

**Work for CanNode:**
```python
class CanNode(FrameNode):
    """Backward-compatible CAN node. Uses FrameService internally."""
    def __init__(self, address="localhost:50051", iface_filter=""):
        super().__init__(address, bus_types=[frame_pb2.Frame.CAN])
        self._iface = iface_filter

    def send(self, can_id, data, dlc=None, iface=""):
        frame = make_can_frame(can_id, data, dlc, iface or self._iface)
        return super().send(frame)

    def on_frame(self, frame):
        if frame.bus_type == frame_pb2.Frame.CAN:
            self.on_can_frame(frame.meta.can)  # backward-compat callback
```

Same pattern for `EthernetNode`. Old subclass implementations (`CanResponderNode`) work unchanged.

**Acceptance criteria:**
- `CanNode.send(0x123, b"data")` internally builds a `Frame{CAN}` and uses FrameService
- `CanResponderNode` (from demo) works unchanged
- `EthernetNode.send(...)` same pattern

### Task 3c.2 — TCP node class

**Subagent:** py-sdk-cli

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `sdk/python/boat/tcp_node.py` | **New** | TCP transport via FrameNode |

```python
class TcpNode(FrameNode):
    """TCP communication via FrameService (no ctypes, no .so)."""
    def __init__(self, address="localhost:50051", plugin_path=None):
        super().__init__(address, bus_types=[frame_pb2.Frame.TCP])
        # Optionally auto-register TCP plugin
        if plugin_path:
            self._register_tcp_plugin(plugin_path)

    def connect(self, dst_ip, dst_port, src_ip="0.0.0.0", src_port=0):
        # Create TCP connection via frame
        frame = make_tcp_frame(dst_ip, dst_port, src_ip, src_port, b"")
        return self.send(frame)

    def send_data(self, dst_ip, dst_port, data):
        frame = make_tcp_frame(dst_ip, dst_port, src_port=..., payload=data)
        return self.send(frame)

    def on_frame(self, frame):
        # Handle incoming TCP data + status frames
        if frame.meta.tcp.conn_status == OK:
            self.on_data(frame.meta.tcp.dst_ip, frame.payload)
```

**Acceptance criteria:**
- `TcpNode.send_data("10.0.0.1", 8080, b"hello")` sends TCP frame through gateway
- Incoming TCP data fires `on_data` callback with correct IP/port

---

## Phase 3d — CLI Updates

### Task 3d.1 — TCP CLI commands

**Subagent:** py-sdk-cli

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `cli/boat_cli/tcp.py` | **New** | `boat tcp send` / `boat tcp listen` |

```bash
# Client mode: send TCP data
boat tcp send --dst-ip 10.0.0.1 --dst-port 8080 --data aabbccdd

# Server mode: listen for TCP data
boat tcp listen --plugin /path/to/tcp.so --port 8080
```

**Acceptance criteria:**
- `boat tcp send` streams status responses
- `boat tcp listen` streams incoming data

### Task 3d.2 — Update CLI Plugin commands

**Subagent:** py-sdk-cli

**Changes:**
| File | Action | Description |
|------|--------|-------------|
| `cli/boat_cli/plugin.py` | Modify | Add `--config` flag to RegisterPlugin |

Current `RegisterPlugin` gRPC sends empty `"{}"` config. Add `config_json` field to `RegisterPluginRequest` proto and CLI:

```bash
boat plugin register /path/to/tcp.so --config '{"mode":"server","listen_port":8080,"iface":"eth0"}'
```

**Acceptance criteria:**
- `boat plugin register foo.so --config '{"key":"val"}'` passes config to plugin

---

## Phase 3 — Acceptance Criteria Summary

| Criteria | Test |
|----------|------|
| All 7 plugins compile as v8 | CMake build passes |
| Gateway starts with any combination of v7+v8 plugins | Integration test |
| CAN/Ethernet frame paths unchanged | Existing tests pass |
| TCP plugin accepts frames via FrameService | New integration test |
| TCP plugin sends data over raw socket | Verified with `ip link` / tcpdump |
| TCP plugin reports status via response frames | New integration test |
| `can_responder` v7 still works (fallback) | Existing HIL test |
| `can_responder` v8 works (new path) | New HIL test |
| `CanNode` backward compat | Demo scripts unchanged |
| `FrameNode` new API | New demo script |
| Python `TcpNode` works without ctypes | New demo script |

---

## Phase 3 — File Change Summary

| File | Action | Lines (est.) |
|------|--------|:-----------:|
| `src/plugins/network_sim/*` | Modify | ~10 |
| `src/plugins/sensor_model/*` | Modify | ~10 |
| `src/plugins/can_responder/*` | Modify | ~40 |
| `src/plugins/vehicle_dynamics/*` | Modify | ~60 |
| `src/plugins/someip/*` | Modify | ~30 |
| `src/plugins/can_tp/*` | Modify | ~60 |
| `src/plugins/tcp/*` | Major rewrite | ~900 changed |
| `sdk/cpp/include/boat/tcp.h` | Delete | -66 |
| `sdk/python/boat/tcp.py` | Delete (Phase 5) | -164 |
| `sdk/python/boat/frame_node.py` | Modify | +60 |
| `sdk/python/boat/can_node.py` | Modify | +30 |
| `sdk/python/boat/ethernet_node.py` | Modify | +30 |
| `sdk/python/boat/tcp_node.py` | New | ~120 |
| `cli/boat_cli/tcp.py` | New | ~100 |
| `cli/boat_cli/plugin.py` | Modify | +10 |
| **Total** | | **~1,450** |

**Risk:** High for TCP rewrite (largest code change). Medium for plugin migration. Low for Python SDK.

**Build impact:** All 7 plugin `.so` files rebuilt. `boat_core` unchanged. `boat_gateway` rebuilt (new includes). New Python SDK module (`tcp_node.py`).

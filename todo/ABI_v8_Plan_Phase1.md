# Phase 1 — Design & ABI v8 (no runtime changes)

**Goal:** Design the `BoatFrame` type system, the v8 Plugin ABI additions, and all conversion layers. Zero runtime behavior change. All new types coexist with existing ones.

**Subagents needed:** plugin-sdk, proto-codegen, cpp-build-test, py-sdk-cli

---

## Task 1.1 — Design `BoatFrame` C struct (`sdk/cpp/include/boat/frame.h`)

**Subagent:** plugin-sdk

**Changes:**
| File | Action |
|------|--------|
| `sdk/cpp/include/boat/frame.h` | **New** — complete C ABI frame type |

**Contents:**
- `BoatBusType` enum: `BOAT_BUS_CAN` (0), `BOAT_BUS_CANFD` (1), `BOAT_BUS_ETHERNET` (2), `BOAT_BUS_TCP` (3), `BOAT_BUS_PDU` (4)
- `BoatCanMeta` struct: `can_id` (u32), `dlc` (u8), `flags` (u8) — matches current `BoatCanFrame` metadata
- `BoatEthMeta` struct: `dst_mac[6]`, `src_mac[6]`, `ethertype` (u16), `vlan_id` (u16), `ip_buf[16]` (packed src_ip + dst_ip)
- `BoatTcpMeta` struct: `src_ip[16]`, `dst_ip[16]`, `src_port` (u16), `dst_port` (u16), `conn_id` (i32)
- `BoatPduMeta` struct: `pdu_id` (u32)
- `BoatFrame` struct: `bus_type` (enum), `iface` (const char*), `timestamp_ns` (u64), `payload` (u8*), `payload_len` (size_t), `meta` (tagged union of all metadata structs)
- `BoatFrameFreeFn` type: signature to release frame-owned resources
- Ownership convention: the caller owns the `BoatFrame` payload buffer for the duration of the callback. No heap allocation required for the frame struct itself (stack-allocatable).

**Acceptance criteria:**
- `sizeof(BoatFrame)` ≤ 128 bytes (fits in L1 cache)
- All metadata structs are trivially copyable
- `bus_type` discriminator matches all metadata union members
- Doxygen comments on every field

---

## Task 1.2 — Design C++ internal `core::Frame` class (`src/core/frame.h/.cpp`)

**Subagent:** cpp-build-test

**Changes:**
| File | Action |
|------|--------|
| `src/core/frame.h` | **New** — C++ Frame class |
| `src/core/frame.cpp` | **New** — implementation |
| `src/core/CMakeLists.txt` | Add `frame.cpp` to `boat_core` target |
| `src/tests/unit/test_frame.cpp` | **New** — unit tests |

**Contents:**
- `Frame` class wrapping `std::vector<uint8_t>` payload with RAII
- Nested `CanMeta`, `EthMeta`, `TcpMeta`, `PduMeta` structs (same layout as C ABI)
- `BusType` enum matching `BoatBusType`
- Static factory methods:
  - `Frame::FromCan(iface, can_id, dlc, flags, payload)`
  - `Frame::FromEthernet(iface, dst_mac, src_mac, ethertype, vlan_id, src_ip, dst_ip, payload)`
  - `Frame::FromTcp(iface, src_ip, dst_ip, src_port, dst_port, conn_id, payload)`
  - `Frame::FromPdu(iface, pdu_id, payload)`
- ABI conversion:
  - `void ToAbi(BoatFrame* out) const` — shallow copy metadata, point to payload.data()
  - `static Frame FromAbi(const BoatFrame& abi)` — deep copy payload
- Proto conversion:
  - `void ToProto(Frame* proto) const` — fill proto message
  - `static Frame FromProto(const Frame& proto)` — parse proto message
- Move-constructible, move-assignable, not copyable (payload is large)

**Acceptance criteria:**
- `frame_test` binary passes all tests
- Round-trip: `FromAbi(ToAbi(f)) == f` for all bus types
- Round-trip: `FromProto(ToProto(f)) == f` for all bus types

---

## Task 1.3 — Design `frame.proto` wire format

**Subagent:** proto-codegen

**Changes:**
| File | Action |
|------|--------|
| `proto/boat/v1/frame.proto` | **New** — Frame message + FrameService |
| `sdk/python/boat/stubs/boat/v1/frame_pb2.py` | **Generated** |
| `sdk/python/boat/stubs/boat/v1/frame_pb2_grpc.py` | **Generated** |
| `sdk/python/boat/stubs/generate_stubs.sh` | Add `frame.proto` to proto list |

**Contents:**
- `Frame.BusType` enum: CAN=0, CANFD=1, ETHERNET=2, TCP=3, PDU=4
- `CanMetadata`, `EthMetadata`, `TcpMetadata`, `PduMetadata` messages
- `Frame` message: `oneof metadata { CanMetadata can = 10; EthMetadata eth = 11; TcpMetadata tcp = 12; PduMetadata pdu = 13; }`
- `FrameService`:
  - `rpc SendFrame(SendFrameRequest) returns (SendFrameResponse)`
  - `rpc SubscribeFrames(SubscribeFramesRequest) returns (stream Frame)`
- `SubscribeFramesRequest`: `repeated BusType bus_types` (filter), `string iface_filter`
- PDU metadata intentionally minimal — PduRouter plugin handles the rich routing config

**Acceptance criteria:**
- `protoc` compiles without warnings
- `generate_stubs.sh` produces valid Python stubs
- `import boat.v1.frame_pb2` works in Python
- Proto message size < 1500 bytes for typical CAN/Ethernet frames (MTU-safe)

---

## Task 1.4 — Design Plugin ABI v8 additions (`sdk/cpp/include/boat/plugin.h`)

**Subagent:** plugin-sdk

**Changes:**
| File | Action |
|------|--------|
| `sdk/cpp/include/boat/plugin.h` | Append v8 fields, add callback typedefs, update version comment |

**Contents:**
- New callback typedefs:
  - `BoatFrameReceiveFn`: `void (*)(void* ctx, const BoatFrame* frame)` — host → plugin
  - `BoatFramePublishFn`: `void (*)(void* plugin_ctx, const BoatFrame* frame)` — plugin → host
  - `BoatDeclaredBusesFn`: `const char* (*)(void* ctx)` — returns JSON array string
- New vtable fields (appended at end to preserve ABI compatibility):
  - `BoatFrameReceiveFn on_frame` — replaces `on_can_frame` + `on_eth_frame`
  - `void (*set_frame_publisher)(void* ctx, BoatFramePublishFn, void* publisher_ctx)`
  - `BoatDeclaredBusesFn declared_buses` — optional, returns `"[\"can\",\"eth\"]"` or `"\"\""` (self-filtering)
- Preserve all v1-v7 fields at same offsets (zero vtable growth for v7 plugins)
- Add comment block explaining backward compatibility: "If `on_frame` is NULL, the core falls back to `on_can_frame`/`on_eth_frame`."
- Bump `BOAT_PLUGIN_ABI_VERSION` NOT YET (keep at 7 until cleanup phase)

**Acceptance criteria:**
- `sizeof(BoatPluginVTable)` increases by 3 pointer fields only (+24 bytes on 64-bit)
- Existing v7 plugins compile unchanged (all new fields are NULL from aggregate initialization)
- `boat_plugin_abi_version()` on v7 plugins still returns 7

---

## Task 1.5 — Create Frame ↔ Proto conversion functions

**Subagent:** cpp-build-test

**Changes:**
| File | Action |
|------|--------|
| `src/core/frame.cpp` | Add `ToProto()` / `FromProto()` methods |
| `src/tests/unit/test_frame_proto.cpp` | **New** — conversion tests |

**Contents:**
- `std::string Frame::Serialize() const` — serialize to binary protobuf (for replay store)
- `static Frame Frame::Deserialize(const std::string& data)` — deserialize
- Conversion preserving all metadata fields per bus type
- Edge cases: empty payload, 64-byte CAN FD payload, IPv6 addresses, VLAN tag present/absent
- Error handling: invalid `bus_type` in proto returns nullopt/throws

**Acceptance criteria:**
- Round-trip: `Frame::Deserialize(f.Serialize()) == f`
- All 4 bus types tested
- IPv4 and IPv6 tested for Ethernet and TCP

---

## Task 1.6 — Generate Python stubs and verify

**Subagent:** py-sdk-cli

**Changes:**
| File | Action |
|------|--------|
| `sdk/python/boat/stubs/generate_stubs.sh` | Add `frame.proto` |

**Contents:**
- Run `generate_stubs.sh` to produce `frame_pb2.py` and `frame_pb2_grpc.py`
- Verify: `python3 -c "from boat.v1 import frame_pb2; print('OK')"`
- Verify: `python3 -c "from boat.v1 import frame_pb2_grpc; print('OK')"`
- Add `Frame` and `FrameService` to stub module `__init__.py` if needed

**Acceptance criteria:**
- Stubs generated and importable
- No other stub files changed (pure additive)
- `git diff sdk/python/boat/stubs/` shows only new files + `generate_stubs.sh` change

---

## Task 1.7 — Design review document

**Subagent:** docs-arch

**Changes:**
| File | Action |
|------|--------|
| `todo/ABI_v8_Plan_Phase1.md` | This file — keep updated |

**Contents:**
- Document the `BoatFrame` layout with a diagram showing struct sizes and field offsets
- Document the conversion trilemma: `BoatFrame` (C) ↔ `core::Frame` (C++) ↔ `Frame proto` (wire)
- Document the backward compat strategy: v7 fallback, vtable growth only at the end
- Document which existing types are NOT yet deprecated (CanFrame, EthernetFrame, PduFrame all stay for now)

**Acceptance criteria:**
- Any developer reading this file can understand why each design decision was made

---

## Phase 1 — File Change Summary

| File | Action | Lines (est.) |
|------|--------|:-----------:|
| `sdk/cpp/include/boat/frame.h` | New | ~120 |
| `sdk/cpp/include/boat/plugin.h` | Append | +15 |
| `src/core/frame.h` | New | ~80 |
| `src/core/frame.cpp` | New | ~200 |
| `src/core/CMakeLists.txt` | Modify | +2 |
| `proto/boat/v1/frame.proto` | New | ~50 |
| `sdk/python/boat/stubs/frame_pb2.py` | New (generated) | ~80 |
| `sdk/python/boat/stubs/frame_pb2_grpc.py` | New (generated) | ~40 |
| `sdk/python/boat/stubs/generate_stubs.sh` | Modify | +1 |
| `src/tests/unit/test_frame.cpp` | New | ~150 |
| `src/tests/unit/test_frame_proto.cpp` | New | ~100 |
| **Total** | | **~838** |

**Risk:** Low — purely additive, no existing code path changed.

**Build impact:** `boat_core` grows by `frame.cpp`. Test binary `boat_unit_frame` added to CMake. Proto regeneration produces two new stubs. No runtime changes to the gateway.

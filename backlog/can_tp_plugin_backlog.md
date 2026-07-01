# CanTp Plugin — AUTOSAR / ISO 15765-2 Gap Analysis

Analysis based on the following source documents:

| Document | File |
|----------|------|
| ISO 15765-2:2016 (3rd ed.) | `spec/text/ISO_15765-2_2016(en).txt` |
| AUTOSAR SWS_CAN_TP V2.6.0 | `spec/text/AUTOSAR_SWS_CAN_TP.txt` |
| AUTOSAR CP_RS_CAN R24-11 | `spec/text/AUTOSAR_CP_RS_CAN.txt` |
| ISO 15765-4:2005 (OBD) | `spec/text/ISO15765-4-A0501.txt` |

---

## ✅ What IS Implemented Correctly

| Feature | ISO Ref | Plugin Lines |
|---------|---------|-------------|
| All 4 PCI frame types (SF/FF/CF/FC) | §9.6.1 Table 8 | `:251, :268, :319, :216` |
| 12-bit FF_DL (≤4095) | §9.6.3 | `:270-271` |
| All 3 FC flow statuses (CTS/Wait/Overflow) | §9.6.5 Table 18 | `:226, :232, :236` |
| STmin decoding (ms + µs ranges) | §9.6.5.4 Table 20 | `:25-30` |
| TX STmin pacing (tx_next_send_time) | §9.6.5.4 | `:106-112` |
| BS tracking on TX (block limits) | §9.6.5.3 | `:110, 114-118` |
| Re-FC on RX at block boundaries | §9.6.5.3 | `:344-358` |
| RX overflow protection | §9.6.5.2 | `:276-292` |
| Self-sent frame filtering | — | `:204` |
| Normal 11-bit dual-ID addressing | §10.3.2 | `:34-39, 381-387` |
| Extended addressing (1-byte N_TA) | §10.3.4 | `:84-86, 284, 308-309` |
| Single-ID backward compat | — | `:381-383` |

---

## 🔴 Critical Gaps

### 1. No Timeouts — N_As, N_Bs, N_Cr, N_Ar, N_Br, N_Cs

ISO 15765-2 §9.8 Table 21 defines six mandatory timing parameters. The plugin implements **none** of them.

| Parameter | Description | Typical Value | Plugin |
|-----------|-------------|---------------|--------|
| N_As | Max time sender waits for FC after FF | 1000ms (ISO), **25ms (OBD)** | **MISSING** — TX_WAIT_FC blocks forever |
| N_Bs | Max time sender waits between FC and first CF | 1000ms (ISO), **75ms (OBD)** | **MISSING** |
| N_Cr | Max time receiver waits for next CF | 1000ms (ISO), **150ms (OBD)** | **MISSING** |
| N_Ar | Max time receiver takes to send FC | 1000ms (ISO) | **MISSING** — sent immediately |
| N_Br | Performance: time from FC to first CF | < 0.9×N_Bs | **MISSING** |
| N_Cs | Performance: time from CF to next CF | < 0.9×N_Cr | **MISSING** — only STmin enforced |

**Impact**: A peer that crashes after sending FF leaves the connection stuck in TX_WAIT_FC permanently. The session is blocked (`can_tp_send()` returns -1 for busy on `:424`). No cleanup mechanism.

**OBD note** (`ISO15765-4 §6.4.1`): Significantly tighter timing — N_As=25ms, N_Bs=75ms, N_Cr=150ms.

---

### 2. TX Thread Dangling Pointer Race

`can_tp_plugin.cpp:52-65`: The TX thread collects raw `NsduConnection*` pointers into a `to_send_cf` vector under `tx_mutex`, then releases the lock.

`can_tp_plugin.cpp:393`: `can_tp_configure()` overwrites `plugin->connections[source_addr]` via `operator[]`, which destroys the old `NsduConnection`.

If `can_tp_configure()` runs between the pointer collection and the TX thread's subsequent use (`:73-127`), the TX thread holds a dangling pointer. The connection object may be freed or reused, leading to use-after-free.

**Fix needed**: Reference counting, shared ownership (`std::shared_ptr`), or per-connection locking.

---

### 3. `find_by_target` Returns First Match Only

`can_tp_plugin.cpp:34-39`: Linear scan over `connections` map returns the first connection whose `target_addr` matches. The map is keyed by `source_addr`, so multiple connections can share the same `target_addr` (e.g., two testers talking to one ECU with different source CAN IDs).

If two connections use the same target_addr, incoming frames on that CAN ID are always routed to the first one found. The second connection never receives any frames.

Violates AUTOSAR CanTp096 (multiple simultaneous connections).

---

## 🟡 Important Gaps

### 4. SF Threshold Hardcoded to 7

`can_tp_plugin.cpp:430`:
```cpp
if (len <= 7)  // Single Frame
```

Should be `if (len <= max_payload - offset)` where `offset` is 1 (normal) or 2 (extended). For CAN FD with `can_dlc=64`, a 60-byte PDU fits in a single FD frame but gets unnecessarily segmented into FF+CF.

ISO 15765-2:2016 §9.2 Table 11 defines SF payload up to 7 (TX_DL=8) and up to 62 (TX_DL=64).

---

### 5. FF / CF Payload Miscalculation for Extended Addressing

`can_tp_plugin.cpp:456`:
```cpp
const uint32_t ff_payload = std::min(len, max_payload - 2);
```

For extended addressing, FF has 3 overhead bytes (1 address + 2 PCI) before payload starts. The code subtracts 2, producing frames 1 byte too long.

Similarly, `can_tp_plugin.cpp:94`:
```cpp
chunk = static_cast<uint32_t>(std::min(
    conn->tx_buffer.size() - conn->tx_offset,
    static_cast<size_t>(max_payload - 1)));
```

For extended addressing, CF has 2 overhead bytes (1 address + 1 PCI), so `max_payload - 2` would be correct. Currently subtracts 1.

---

### 6. No Padding Byte Handling

ISO 15765-2 §10.4: Unused CAN data bytes shall be padded (`0xCC` by default, or `0x00` for extended addressing). AUTOSAR CanTp320-325 define configurable padding per N-SDU.

- Outgoing frames (`:440, :458, :102`): DLC is set to actual payload size, not `can_dlc`. Most CAN controllers pad to valid DLC — trailing bytes may contain stale data.
- No RX padding validation: padding errors from non-compliant peers go undetected.
- OBD (`ISO15765-4 §6.4.1`): Requires DLC always 8 (padding mandatory).

**AUTOSAR specifics**:
- CanTp320: Rx padding ON → only accept SF/last CF with length = 8 bytes
- CanTp321: Rx padding ON, SF length < 8 → reject with `CANTP_E_PADDING`
- CanTp322: Rx padding ON, last CF length ≠ 8 → abort with `NTFRSLT_PADDING_E_NOT_OK`
- CanTp323: Rx padding ON → FC frames length 8, unused bytes = `CANTP_PADDING_BYTE`
- CanTp324: Tx padding ON → SF/last CF transmitted with length 8, unused bytes padded
- CanTp116: Regardless of padding mode, only used bytes transferred to upper layer

---

### 7. FF Minimum Length Not Validated

ISO 15765-2 §9.6.3.2 Table 14: FF must carry at least 8 bytes (values 0-7 are invalid, sender should use SF instead).

`can_tp_plugin.cpp:270-276`: Only checks `ff_len > rx_buffer_size`. Accepts `ff_len` values 0-7 without complaint.

---

### 8. CF Sequence Wrap + Loss Desync

`can_tp_plugin.cpp:342`:
```cpp
conn->rx_next_seq = (seq + 1) & 0x0F;
```

If a CF is lost just before the seq=15→0 wrap boundary, the receiver may re-synchronize on incorrect data. Example:
- CF seq=14 sent, lost in transit
- CF seq=15 sent, received — receiver expects 15, gets 15, accepts it
- Buffer now has a gap (missing CF seq=14's data) but receiver continues

ISO 15765-2 handles this via N_Cr timeout — if a CF is not received within N_Cr, the session is aborted. Since timeouts are not implemented, this edge case is undetected.

---

### 9. Connection Overwrite Silently Discards Active Session

`can_tp_plugin.cpp:393`:
```cpp
plugin->connections[conn.source_addr] = conn;
```

- If a connection with the same `source_addr` exists and has an active TX (TX_WAIT_FC, TX_SEND_CF), the old session is silently destroyed. In-flight data is lost.
- No error is returned to the caller.
- AUTOSAR CanTp123: TX channel in CANTP_TX_PROCESSING shall reject new TX requests with E_NOT_OK.

---

### 10. CAN FD DLC Encoding Uses Raw Bytes

`can_tp_plugin.cpp:78, 428`:
```cpp
const uint32_t max_payload = dlc;
```

CAN FD uses DLC-to-byte-length mapping (ISO 15765-2 §6.3 Table 3):
| DLC | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
|-----|---|---|---|---|---|---|---|
| Bytes | 12 | 16 | 20 | 24 | 32 | 48 | 64 |

The plugin treats `dlc` as a raw byte count. A `can_dlc=64` happens to work if the driver accepts raw byte counts, but ISO-compliant DLC values 9-15 produce incorrect payload sizes.

Additionally, no BRS (Bit Rate Switch) flag is set for CAN FD frames.

---

## 🔵 Architectural / Minor Issues

### 11. No Error Reporting / Events

All error conditions are silently handled:
- Overflow (`:278`): Sets `rx_state = RX_IDLE`, sends FC Overflow, but application never notified
- Sequence error (`:324`): Silently resets to RX_IDLE
- Busy (`:424`): Returns -1 with no way to wait/poll

AUTOSAR defines N_Result values (`N_TIMEOUT_A`, `N_WRONG_SN`, `N_INVALID_FS`, `N_UNEXP_PDU`, `N_WFT_OVRN`, `N_BUFFER_OVFLW`, `N_ERROR`) that should be reported to the upper layer. No callback or event mechanism exists.

### 12. TX Thread Busy-Poll

`can_tp_plugin.cpp:56-58`: TX thread uses `wait_for(500µs)` with a predicate that only checks `tx_stop`. It wakes ~2000×/sec even when idle, scanning all connections O(n).

Should use `wait()` (no timeout) and notify on work arrival + stop.

### 13. TX Thread Triple-Lock Per CF

TX thread acquires `tx_mutex` 3× per single CF send:
- `:90-94`: Read tx_seq + chunk
- `:98-101`: Copy from tx_buffer
- `:107-127`: Update state

Between acquisitions 1 and 2, another thread could modify tx_buffer. Combine all three into one locked section.

### 14. Single Mutex for All State

`can_tp_plugin.h:62`: A single `tx_mutex` protects the entire connection map. RX path (`tp_on_can_frame`), TX path (`can_tp_send`), and TX thread all contend on the same lock. Per-connection locking would eliminate contention.

### 15. No 29-Bit / Mixed Addressing

ISO 15765-2 §10.3.5 defines mixed addressing where:
- 29-bit CAN ID carries source + target in extended identifier bits
- First data byte carries N_AE (address extension)
- AUTOSAR SRS_Can_01078 requires all four addressing formats (normal, extended, mixed 11-bit, mixed 29-bit, normal fixed)

Plugin only supports 11-bit normal + 1-byte extended.

### 16. CLI / Python SDK Loads Separate .so

`sdk/python/boat/can_tp.py:101`:
```python
result = self._lib.can_tp_configure(None, ctypes.byref(config))
```

Both `CanTpHandle.configure()` and `send()` pass `None` as the plugin context. This creates a completely separate instance of the CanTp plugin (second copy of the .so) with:
- No CAN publisher wired (`can_publish_fn == nullptr`)
- No connection to the gateway's CAN bus
- No shared state with the gateway-loaded plugin

**Impact**: `boat can-tp send` CLI command loads its own unconnected plugin instance and can never actually transmit frames. The only way to use CanTp is through `BOAT_NODE_PLUGINS` at gateway startup.

---

## Summary

| Priority | Items | Count |
|----------|-------|-------|
| 🔴 Critical | #1 No timeouts, #2 Dangling pointer race, #3 find_by_target single match | **3** |
| 🟡 Important | #4 SF threshold hardcoded, #5 FF payload calc, #6 No padding, #7 FF min length, #8 CF wrap desync, #9 Connection overwrite, #10 CAN FD DLC encoding | **7** |
| 🔵 Minor/Arch | #11 No error reporting, #12 Busy-poll, #13 Triple-lock, #14 Single mutex, #15 No 29-bit/mixed, #16 CLI separate .so | **6** |
| **Total** | | **16** |

The single largest gap is the **complete absence of ISO 15765-2 timing infrastructure** — without N_As/N_Bs/N_Cr timeouts, the plugin cannot detect dead peers and will hang sessions indefinitely. The second most impactful issue is the **TX thread dangling pointer race** under concurrent configure/send activity.

# CanTp Plugin — AUTOSAR / ISO 15765-2 Gap Analysis

Analysis based on the following source documents:

| Document | File |
|----------|------|
| ISO 15765-2:2016 (3rd ed.) | `spec/text/ISO_15765-2_2016(en).txt` |
| AUTOSAR SWS_CAN_TP V2.6.0 | `spec/text/AUTOSAR_SWS_CAN_TP.txt` |
| AUTOSAR CP_RS_CAN R24-11 | `spec/text/AUTOSAR_CP_RS_CAN.txt` |
| ISO 15765-4:2005 (OBD) | `spec/text/ISO15765-4-A0501.txt` |

---

## 🔄 Status Update (2026-08-10)

Re-verified against current `can_tp_plugin.cpp`/`.h` after two commits landed post-analysis: `c35034e` (wire CanTp to a live gRPC service, add multi-instance + node-plugin support) and `a247557` (key CanTp sessions by `nsdu_id`, add remove/subscribe).

- **Resolved: #2 (dangling pointer race), #16 (CLI loads a separate `.so`).**
- **Partially resolved: #1 (timeouts — N_Bs/N_Cr implemented and verified on real PCAN hardware, see below), #6 (padding)** — frames are now sent at fixed `can_dlc` with padding; pad-byte value and RX-side validation still don't match spec.
- **Reframed, not fixed: #3** (still a real bug, but narrower now that the map keys changed), **#10** (byte-length framing was a misread — BRS flag gap is the real remainder), **#14** (now a documented tradeoff, not an oversight).
- **Everything else (#4, #5, #7, #8, #9, #11, #12, #13, #15) — confirmed still open**, code unchanged since the original analysis.

Note: most `:line` citations below predate the `nsdu_id`-keying refactor and no longer point at the right lines — treat them as historical, not current.

**2026-08-10, later the same day** — #1 (N_Bs/N_Cr) implemented on `feat/can-tp-nbs-ncr-timeouts` and verified against real PCAN USB Pro FD hardware (can0/can1 on one physical bus, agn-testcomputer): both watchdogs fire at their configured deadline (confirmed via bus capture that no frame explains the reset any other way), and a happy-path multi-frame transfer over the same dual-instance setup still round-trips byte-exact — no regression. Critical count drops to 0.

**2026-08-10, quick-fix bundle** (`feat/can-tp-hardening-quickfixes`, stacked on the above) — #4, #5, #7, #12, #13 all resolved; see each item below for what actually shipped (#4 and #12 turned out to need more than the "quick" framing suggested). Also found and fixed, via hardware testing rather than review, a bug not in the original 16: **extended-addressing RX was fundamentally broken** — the PCI byte was read from the wrong position for every incoming extended-addressing frame, so it never worked at all (folded into #5's writeup, since it surfaced while verifying that fix). Full non-HIL/non-determinism test suite (133 tests) + Python suite (213 tests) green throughout; real round trips verified for classic, extended-addressing, and both timeout paths.

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

### 1. No Timeouts — N_As, N_Bs, N_Cr, N_Ar, N_Br, N_Cs — 🟡 Partially resolved (2026-08-10)

ISO 15765-2 §9.8 Table 21 defines six mandatory timing parameters. The plugin implemented **none** of them.

**Fixed**: N_Bs and N_Cr are now real watchdogs, checked by the existing TX pacing thread's poll loop (`can_tp_tx_thread_func`). `CanTpConfig` gets `n_bs_ms`/`n_cr_ms` (0 = ISO default 1000ms), threaded through the C ABI, proto, gRPC service, Python SDK, and CLI (`--n-bs-ms`/`--n-cr-ms` on `can-tp configure`). An FC(WT) restarts N_Bs; each accepted CF restarts N_Cr. Verified against real PCAN hardware (can0/can1 on the same physical bus): a withheld FC correctly aborts TX back to `TX_IDLE` at the configured deadline, and a withheld CF correctly aborts RX back to `RX_IDLE` — in both cases confirmed via bus capture that no frame arrived to explain the reset any other way, and a full happy-path multi-frame transfer over the same setup still round-trips byte-exact.

**Deliberately deferred, not implemented**:
| Parameter | Description | Status |
|-----------|-------------|--------|
| N_As | Max time sender waits for FC after FF | **N/A for this transport** — `frame_publish_fn` is synchronous, there is no local transmit-confirmation step to time out on |
| N_Ar | Max time receiver takes to send FC | **N/A** — same reason; FC is sent synchronously in `tp_on_frame` |
| N_Br | Performance: time from FC to first CF | Soft performance target, not a hang-forever bug — deferred |
| N_Cs | Performance: time from CF to next CF | Soft performance target, not a hang-forever bug — deferred |

**Impact of the original gap**: a peer that crashed after sending FF left the connection stuck in `TX_WAIT_FC` permanently. Resolved for the case that actually causes indefinite hangs (N_Bs/N_Cr); the four deferred parameters don't have that failure mode in this codebase.

**OBD note** (`ISO15765-4 §6.4.1`): tighter timing — N_As=25ms, N_Bs=75ms, N_Cr=150ms. OBD callers should pass `--n-bs-ms 75 --n-cr-ms 150` explicitly; there's no built-in OBD profile shortcut (kept the config surface to raw values only, by design).

---

### 2. TX Thread Dangling Pointer Race — ✅ RESOLVED (2026-08-10)

Original finding: the TX thread collects raw `NsduConnection*` pointers under `tx_mutex`, then releases the lock; `can_tp_configure()` overwrote `plugin->connections[source_addr]` via `operator[]`, destroying the old `NsduConnection` and leaving the TX thread with a dangling pointer.

**Fixed by the `nsdu_id`-keying refactor (`a247557`)**, apparently as a side effect rather than a targeted fix:
- Connections are now keyed by `nsdu_id`. Re-configuring an existing `nsdu_id` is an in-place `operator=` on the same map slot, not a destroy+recreate — the object's address is stable.
- `can_tp_remove()` now explicitly refuses to erase a connection unless `tx_state` is `IDLE`/`COMPLETE` (`can_tp_plugin.cpp:502-505`), so the one operation that *does* erase a map entry can't run while the TX thread might still be using it.
- `std::unordered_map` only invalidates references/pointers on erase (not on insert/rehash), which the code now leans on explicitly (see the `tp_on_frame` locking comment).

Residual, smaller issue: the TX thread still reads `conn->tx_state`/`conn->config.*` outside `tx_mutex` in a couple of spots per CF (`can_tp_plugin.cpp:87-97`) — a data race in the strict sense, just not the dangling-pointer/UAF scenario originally described. Not urgent enough to reopen as Critical; folded into #13 (triple-lock) as a locking-hygiene cleanup.

---

### 3. `find_by_target` Returns First Match Only — still open, downgraded to 🟡 Important

`can_tp_plugin.cpp:46-51`: Linear scan over `connections` still returns the first connection whose `target_addr` matches — this function is byte-for-byte unchanged since the original analysis.

**What changed**: the map is now keyed by `nsdu_id` instead of `source_addr` (`a247557`), so this is no longer entangled with the connection-overwrite bug (#9) — configuring two connections that happen to share a `target_addr` no longer clobbers one of them, they just both silently exist with only one reachable on RX. The underlying ambiguity is narrower than before (it now takes two *distinct, deliberately-configured* `nsdu_id`s pointing at the same peer CAN ID, rather than any accidental `source_addr` reuse) but the bug itself is untouched.

If two connections use the same target_addr, incoming frames on that CAN ID are always routed to the first one found (unordered_map iteration order). The second connection never receives any frames.

Violates AUTOSAR CanTp096 (multiple simultaneous connections).

---

## 🟡 Important Gaps

### 4. SF Threshold Hardcoded to 7 — ✅ RESOLVED (2026-08-10)

`can_tp_plugin.cpp:430` (old): `if (len <= 7)` regardless of CAN FD.

Turned out to be more than a threshold tweak: ISO 15765-2:2016 Table 11 defines a *different* SF PCI format for CAN FD (`dlc > 8`) — a 2-byte escape (`0x00` + full-byte `SF_DL`) instead of the classic 1-byte nibble-encoded form, extending `SF_DL` up to `dlc-2` (`dlc-3` extended) instead of always capping at 7/6. Implemented both the TX-side escape encoding (`can_tp_send()`) and the RX-side decode (classic CAN's nibble-0 case, an empty SF, is untouched — the escape interpretation only kicks in for FD connections). Verified the classic-CAN threshold (7/6) is unchanged by the new formula, and the FD threshold now matches Table 11 (62/61 for `dlc=64`).

---

### 5. FF / CF Payload Miscalculation for Extended Addressing — ✅ RESOLVED (2026-08-10)

`can_tp_plugin.cpp:456` (old): `const uint32_t ff_payload = std::min(len, max_payload - 2);` — hardcoded overhead of 2 regardless of addressing mode, silently truncating 1 byte of payload per FF (and per CF, same bug in the TX thread's chunk calc) whenever extended addressing was used. Now computed as `extended_addressing ? 3 : 2` (FF) / `extended_addressing ? 2 : 1` (CF), matching the actual header size.

**Found via hardware testing while verifying this fix**: extended-addressing RX was separately, fundamentally broken — `pci_byte` was read unconditionally from `payload[0]`, but under extended addressing that's the target-address-extension byte, not the PCI byte (at `payload[1]`), so every incoming extended-addressing frame was misclassified and silently dropped by every SF/FF/CF/FC branch, regardless of this payload-size bug. Also fixed as part of the same pass: `pci_byte`'s position now depends on the connection's `extended_addressing` (deferred until `find_by_target()` resolves `conn`, since addressing is per-connection config); FF_DL's low byte was hardcoded to `data[1]` instead of the byte after the (now-correctly-located) PCI byte; and outgoing FC frames hardcoded the extended-addressing byte to `0x00` instead of the peer's address (`conn->target_addr`), unlike every other outgoing frame type. Verified end-to-end on real PCAN hardware: a 30-byte extended-addressing multi-frame transfer round-trips byte-exact between two independent CanTp instances.

---

### 6. No Padding Byte Handling — 🟡 Partially resolved (2026-08-10)

ISO 15765-2 §10.4: Unused CAN data bytes shall be padded (`0xCC` by default, or `0x00` for extended addressing). AUTOSAR CanTp320-325 define configurable padding per N-SDU.

**Fixed**: every emitted SF/FF/CF/FC now goes through a `pad_frame()` helper (`can_tp_plugin.cpp:19-29`) and is sent at the connection's fixed `can_dlc`, not actual payload length — the "DLC set to actual payload size" complaint below no longer applies.

**Still open**:
- Pad byte is hardcoded to `0x55`, not the AUTOSAR default `0xCC` (nor `0x00` for extended addressing), and isn't configurable per N-SDU (CanTp320-325).
- No RX padding validation: padding errors from non-compliant peers still go undetected (CanTp321/322).
- OBD (`ISO15765-4 §6.4.1`): Requires DLC always 8 (padding mandatory) — satisfied by default `can_dlc=8`, but not enforced.

~~Outgoing frames (`:440, :458, :102`): DLC is set to actual payload size, not `can_dlc`. Most CAN controllers pad to valid DLC — trailing bytes may contain stale data.~~ (fixed, see above)

**AUTOSAR specifics**:
- CanTp320: Rx padding ON → only accept SF/last CF with length = 8 bytes
- CanTp321: Rx padding ON, SF length < 8 → reject with `CANTP_E_PADDING`
- CanTp322: Rx padding ON, last CF length ≠ 8 → abort with `NTFRSLT_PADDING_E_NOT_OK`
- CanTp323: Rx padding ON → FC frames length 8, unused bytes = `CANTP_PADDING_BYTE`
- CanTp324: Tx padding ON → SF/last CF transmitted with length 8, unused bytes padded
- CanTp116: Regardless of padding mode, only used bytes transferred to upper layer

---

### 7. FF Minimum Length Not Validated — ✅ RESOLVED (2026-08-10)

ISO 15765-2 §9.6.3.2 Table 14: FF must carry at least 8 bytes (values 0-7 are invalid, sender should use SF instead).

`tp_on_frame()`'s FF handling now rejects `ff_len < 8` (dropped, matching this function's existing silent-drop precedent for other malformed input — no new error-reporting infra needed for this, that's #11's job).

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

### 9. Connection Overwrite Silently Discards Active Session — still open

`can_tp_plugin.cpp:485`:
```cpp
plugin->connections[conn.nsdu_id] = conn;
```

Now keyed by `nsdu_id` rather than `source_addr`, but the behavior is unchanged — and now explicitly *intentional*, per the comment directly above it: "Re-configuring an already-configured nsdu_id overwrites it in place (doubles as 'edit'); this also resets rx/tx state, which is fine even mid-transfer since tp_on_frame's RX path and the TX pacing thread both hold this same lock before touching a connection." That comment addresses the *memory-safety* half (safe to overwrite without crashing, ties into #2's fix) but not the *protocol-compliance* half below:

- If a connection with the same `nsdu_id` exists and has an active TX (TX_WAIT_FC, TX_SEND_CF), the old session is still silently destroyed. In-flight data is lost.
- No error is returned to the caller.
- AUTOSAR CanTp123: TX channel in CANTP_TX_PROCESSING shall reject new TX requests with E_NOT_OK.

---

### 10. CAN FD DLC Encoding — reframed (2026-08-10), BRS flag gap still real

`can_tp_plugin.cpp:90, 542`:
```cpp
const uint32_t max_payload = dlc;
```

**Reframe**: `CanTpConfig::can_dlc` is documented in the public ABI (`sdk/cpp/include/boat/can_tp.h:25`) as "max CAN DLC for this connection (**8 or 64**)" — i.e. it's specified as a byte-length value by design, not the raw ISO 11898-1 4-bit DLC *code* (0-15). Given that, treating it as a byte count directly is correct, not a bug — the original framing (comparing against the DLC-code→byte-length table) doesn't apply to this field as designed. Worth double-checking there's no config path that ever hands this a code in the 9-15 range instead of a byte count, but no evidence of that in the current CLI/SDK.

**Still a real gap**: no BRS (Bit Rate Switch) flag is set for CAN FD frames — every `BoatFrameOwner::Can(...)` call passes `is_fd ? 0x04 : 0` (FDF only; `CANFD_BRS` is `0x01`, never set). CAN FD frames are always sent at the base data rate.

---

## 🔵 Architectural / Minor Issues

### 11. No Error Reporting / Events

All error conditions are silently handled:
- Overflow (`:278`): Sets `rx_state = RX_IDLE`, sends FC Overflow, but application never notified
- Sequence error (`:324`): Silently resets to RX_IDLE
- Busy (`:424`): Returns -1 with no way to wait/poll

AUTOSAR defines N_Result values (`N_TIMEOUT_A`, `N_WRONG_SN`, `N_INVALID_FS`, `N_UNEXP_PDU`, `N_WFT_OVRN`, `N_BUFFER_OVFLW`, `N_ERROR`) that should be reported to the upper layer. No callback or event mechanism exists.

### 12. TX Thread Busy-Poll — ✅ RESOLVED (2026-08-10)

`can_tp_plugin.cpp:56-58` (old): TX thread used `wait_for(500µs)` with a predicate that only checked `tx_stop`, waking ~2000×/sec even when idle.

Replaced with a scan that tracks the earliest upcoming deadline across all connections (CF pacing, N_Bs, N_Cr) and sleeps until exactly that point via `wait_until`, or indefinitely via `wait()` if nothing is pending — woken early by `tx_cv.notify_one()` at every call site that creates or moves a deadline earlier (existing sites plus new ones added at the RX-side deadline mutations, which previously had no reason to wake the TX thread).

**A genuine bug surfaced building this**, caught by hardware testing rather than review: the naive version computed the sleep deadline from a scan pass *before* the send-CF phase updated `tx_next_send_time`/`tx_fc_deadline` for the very connections just serviced, so a still-streaming `BS=0`/`STmin=0` connection could go to sleep indefinitely right after sending only its first CF (confirmed on hardware: a 20-byte transfer stalled after CF1, only resuming when an unrelated `notify_one()` from a second session's `send()` happened to wake the thread). Fixed by skipping the sleep and looping back to rescan immediately whenever an iteration sent anything — only sleeping when a scan finds nothing due, at which point the computed deadline is guaranteed fresh.

### 13. TX Thread Triple-Lock Per CF — ✅ RESOLVED (2026-08-10)

TX thread used to acquire `tx_mutex` 3× per single CF send (read tx_seq/chunk, copy from tx_buffer, update state). The scan pass now captures seq/chunk in the same locked section that decides a CF is due (as part of the `TxWork` entry), so the per-CF send phase only re-locks for the buffer copy and the post-send state update — down from 3 acquisitions to 2, and the data race #2's writeup flagged (reading `tx_state`/`config.*` outside the lock) is gone along with it.

### 14. Single Mutex for All State — reframed (2026-08-10) as an intentional tradeoff

`can_tp_plugin.h:86`: A single `tx_mutex` protects the entire connection map. RX path (`tp_on_frame`), TX path (`can_tp_send`), and TX thread all contend on the same lock. Per-connection locking would eliminate contention.

No longer an unexamined oversight: the header now carries an explicit comment (`can_tp_plugin.h:80-84`) explaining this mutex is deliberately doing double duty as the RX-vs-`Remove()` serialization point (the fix for #2) — "it's effectively a general per-plugin connection-state mutex now, not just a TX-thread lock, so that Remove() erasing a connection can never race a concurrent on_frame() dereferencing the same NsduConnection*." Per-connection locking is still the right long-term fix if contention becomes a real problem, but the current single-mutex design is a considered choice, not neglect.

### 15. No 29-Bit / Mixed Addressing

ISO 15765-2 §10.3.5 defines mixed addressing where:
- 29-bit CAN ID carries source + target in extended identifier bits
- First data byte carries N_AE (address extension)
- AUTOSAR SRS_Can_01078 requires all four addressing formats (normal, extended, mixed 11-bit, mixed 29-bit, normal fixed)

Plugin only supports 11-bit normal + 1-byte extended.

### 16. CLI / Python SDK Loads Separate .so — ✅ RESOLVED (2026-08-10)

Original finding: `CanTpHandle.configure()`/`send()` passed `None` as the plugin context (`sdk/python/boat/can_tp.py:101`, old), spinning up a second, disconnected copy of the plugin `.so` with no CAN publisher wired and no shared state with the gateway-loaded instance — `boat can-tp send` could never actually transmit.

**Fixed by `c35034e`** ("wire CanTp to a live gRPC service, add multi-instance + node-plugin support"): `sdk/python/boat/can_tp.py` no longer touches ctypes/ the raw C ABI at all. `CanTpHandle` is now a thin wrapper around the `CanTpService` gRPC API — `configure()`/`send()`/`remove()`/`subscribe()`/`list_sessions()` all go through `self._client.can_tp.*` RPCs. Server-side, `can_tp_service_impl.cpp` resolves the request to the *live* plugin instance via `plugin_manager.FindService("can_tp:" + iface)` (each gateway-loaded instance registers itself under an iface-scoped service name — see `boat_plugin_service_name()` in `can_tp_plugin.cpp:649-652`). There is no longer any path that creates a second, unconnected plugin instance.

---

## Summary

**As of 2026-08-10, end of day** — after `c35034e`, `a247557`, and the `feat/can-tp-nbs-ncr-timeouts` + `feat/can-tp-hardening-quickfixes` branches (not yet merged to master):

| Priority | Items | Count |
|----------|-------|-------|
| 🔴 Critical | — | **0** |
| 🟡 Important | #3 find_by_target single match (downgraded from Critical), #6 No padding (partial), #8 CF wrap desync, #9 Connection overwrite, #10 CAN FD BRS flag (reframed) | **5** |
| 🔵 Minor/Arch | #11 No error reporting, #14 Single mutex (reframed as intentional), #15 No 29-bit/mixed | **3** |
| ✅ Resolved | #1 N_Bs/N_Cr timeouts (N_As/N_Ar/N_Br/N_Cs deliberately deferred, see #1), #2 Dangling pointer race, #4 SF threshold/FD escape format, #5 FF/CF payload calc (+ the extended-addressing RX bug found alongside it), #7 FF min length, #12 Busy-poll, #13 Triple-lock, #16 CLI separate .so | **8** |
| **Total** | | **16** |

**Original analysis (pre-2026-08-10), for reference:**

| Priority | Items | Count |
|----------|-------|-------|
| 🔴 Critical | #1 No timeouts, #2 Dangling pointer race, #3 find_by_target single match | **3** |
| 🟡 Important | #4 SF threshold hardcoded, #5 FF payload calc, #6 No padding, #7 FF min length, #8 CF wrap desync, #9 Connection overwrite, #10 CAN FD DLC encoding | **7** |
| 🔵 Minor/Arch | #11 No error reporting, #12 Busy-poll, #13 Triple-lock, #14 Single mutex, #15 No 29-bit/mixed, #16 CLI separate .so | **6** |
| **Total** | | **16** |

No Critical items remain, and half the original 16 are now resolved. The most impactful open items are the **`find_by_target` single-match bug** (#3) and the **silent connection-overwrite mid-session** (#9) — both session-integrity issues rather than crashes or hangs — followed by **#11 (no error/event reporting)**, which is the natural next milestone now that timeouts and other silent-drop scenarios exist to report on.

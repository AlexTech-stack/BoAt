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

**2026-08-10, continued on the same branch** — #3, #9, #10, and #6's remainder all resolved; see each item below. All verified on real PCAN hardware, including CAN FD with a data-phase bit rate configured (`dbitrate 2000000 fd on`) specifically to observe the BRS flag on the wire. One pre-existing unit test hardcoded the old pad-byte value (0x55) in its expected output and needed updating for the new default (0xCC) — a real, expected consequence of the fix, not a regression.

**2026-08-10, #15 (mixed/29-bit addressing)** — implemented and verified on real hardware; see its writeup below. Also found and fixed a second bug outside the CanTp plugin entirely: `SocketCanDriver::ReadFrame()` didn't mask `CAN_EFF_FLAG` off received CAN IDs, silently breaking any 29-bit-ID comparison system-wide. Only three items remain open: #6's deliberately-deferred RX padding validation, and #11/#14 (#14 closed as an intentional tradeoff, not a real gap).

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

### 3. `find_by_target` Returns First Match Only — ✅ RESOLVED (2026-08-10)

`can_tp_plugin.cpp:46-51`: Linear scan over `connections` returns the first connection whose `target_addr` matches — this function itself is unchanged, and still would silently misroute if it were ever reached with a genuine duplicate.

**Fixed at the source instead**: `find_by_target()`'s ambiguity is unfixable in general (nothing in a plain ISO-TP frame identifies which of two connections sharing a CAN ID it's for — that's what mixed addressing's N_AE byte is for, see #15), so `can_tp_configure()` now rejects configuring a `target_addr` already claimed by a *different* `nsdu_id` on the same instance (returns -3; a connection re-configuring itself with its own `target_addr` is not a conflict). Turns a silent, undetectable routing failure into an immediate, clear configuration-time error (AUTOSAR CanTp096: multiple simultaneous connections must be distinguishable). Verified on real hardware: a second `nsdu_id` targeting an already-claimed `target_addr` is rejected with `FAILED_PRECONDITION`; re-configuring the original `nsdu_id` with the same `target_addr` still succeeds.

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

### 6. No Padding Byte Handling — 🟡 Mostly resolved (2026-08-10)

ISO 15765-2 §10.4: Unused CAN data bytes shall be padded (`0xCC` by default, or `0x00` for extended addressing). AUTOSAR CanTp320-325 define configurable padding per N-SDU.

**Fixed**: every emitted SF/FF/CF/FC now goes through a `pad_frame()` helper (`can_tp_plugin.cpp:19-29`) and is sent at the connection's fixed `can_dlc`, not actual payload length — the "DLC set to actual payload size" complaint below no longer applies. The pad byte is now `CanTpConfig::pad_byte`, configurable per N-SDU (CanTp320-325), defaulting to the AUTOSAR/ISO value `0xCC` (was hardcoded `0x55`) via the same 0-sentinel convention as `n_bs_ms`/`n_cr_ms` — one documented consequence: literal `0x00` padding isn't independently selectable through this field (0 always resolves to the 0xCC default), since 0x00 is both a legitimate real pad value and the sentinel. Verified on real hardware: default sends `0xCC`-padded frames, `--pad-byte <n>` (any non-zero value) sends that value instead.

**Deliberately deferred**: RX-side padding validation (CanTp321/322: reject SF/last CF shorter than `can_dlc`). Considered and rejected for now — enforcing it means treating any incoming frame shorter than the configured `can_dlc` as a protocol violation, but classic CAN legitimately allows variable-length frames, and this plugin has no "padding mode" config concept to distinguish "peer pads, reject anything short" from "peer doesn't pad, variable length is fine." Building that distinction properly is more scope than this pass; revisit alongside #11 (error reporting) so a violation has somewhere to be reported instead of just silently rejected.

- OBD (`ISO15765-4 §6.4.1`): Requires DLC always 8 (padding mandatory) — satisfied by default `can_dlc=8`, but not enforced.

**AUTOSAR specifics** (RX validation reference, for when #11 makes this worth implementing):
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

### 8. CF Sequence Wrap + Loss Desync — 🔵 Downgraded to Minor, mitigated by #1 (2026-08-10)

`can_tp_plugin.cpp:342` (original line; sequence check logic unchanged):
```cpp
conn->rx_next_seq = (seq + 1) & 0x0F;
```

The specific scenario described here is largely closed now that N_Cr exists (#1): the immediate seq-mismatch check (`if (seq != conn->rx_next_seq) { rx_state = RX_IDLE; return; }`, a few lines above) already catches a lost CF in the general case — the receiver expects seq N, gets seq N+1, and aborts immediately rather than accepting mismatched data. What remains is the exact-wrap edge case (a CF lost at precisely the 15→0 rollover, where the *next* correct-looking seq value coincidentally matches what's expected) — and even that residual case now has N_Cr as a backstop: if the session doesn't cleanly resync, it will time out rather than hang. Not reclassifying as fully resolved since the exact-wrap scenario is still theoretically reachable and untested, but no longer a hang-forever bug — downgraded from Important to Minor/Arch.

---

### 9. Connection Overwrite Silently Discards Active Session — ✅ RESOLVED (2026-08-10)

`can_tp_plugin.cpp:485` (old): `plugin->connections[conn.nsdu_id] = conn;` — unconditional overwrite, no busy check.

**Fixed**: `can_tp_configure()` now checks, before overwriting, whether the existing connection (if any) has an active TX (`tx_state` not `IDLE`/`COMPLETE`) or RX (`rx_state == RX_WAIT_CF`) and refuses with -2 if so — same convention and same "busy" return code as `can_tp_remove()`'s existing guard, extended here to RX for the same data-loss reason. Directly implements AUTOSAR CanTp123 ("TX channel in CANTP_TX_PROCESSING shall reject new TX requests with E_NOT_OK"). Verified on real hardware: re-configuring a session mid-transfer (`TX_WAIT_FC`) is rejected with `FAILED_PRECONDITION`; once the transfer settles (or times out via #1's N_Bs), the same re-configure succeeds.

---

### 10. CAN FD DLC Encoding — ✅ RESOLVED (2026-08-10)

`can_tp_plugin.cpp:90, 542` (original lines):
```cpp
const uint32_t max_payload = dlc;
```

**Reframe (still stands)**: `CanTpConfig::can_dlc` is documented in the public ABI as "max CAN DLC for this connection (**8 or 64**)" — a byte-length value by design, not the raw ISO 11898-1 4-bit DLC *code*. Treating it as a byte count directly is correct, not a bug.

**BRS flag gap — fixed**: `CanTpConfig` gets `brs` (default `false` — not forced on for every CAN FD connection, since not every CAN FD bus has a distinct data-phase bit rate configured to switch to), threaded through every outgoing frame's flags byte via a new `can_flags(is_fd, brs)` helper replacing the six duplicated `is_fd ? 0x04 : 0` call sites. Verified on real hardware with the interfaces explicitly reconfigured for CAN FD + a distinct data-phase bit rate (`dbitrate 2000000 fd on`): `candump -x` shows the `B` (BRS) flag present with `--brs`, absent without it, on otherwise-identical frames.

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

### 15. No 29-Bit / Mixed Addressing — ✅ RESOLVED (2026-08-10)

ISO 15765-2 §10.3.5 defines mixed addressing (29-bit CAN ID + N_AE address-extension byte) and normal fixed addressing (29-bit CAN ID, no address byte). AUTOSAR SRS_Can_01078 requires all four addressing formats (normal, extended, mixed 11-bit, mixed 29-bit, normal fixed).

**Byte layout verified against a reference implementation** ([can-isotp addressing docs](https://can-isotp.readthedocs.io/en/latest/isotp/addressing.html)) before implementing: Normal Fixed is `0x18DA<TA><SA>` (physical) / `0x18DB<TA><SA>` (functional); Mixed 29-bit is `0x18CE<TA><SA>` / `0x18CD<TA><SA>` plus an N_AE payload byte; Mixed 11-bit is wire-identical to Extended (same address-byte mechanism, different AUTOSAR/ISO semantic label).

**Key design realization**: 11-bit vs. 29-bit isn't a separate addressing *mode* in this implementation — it's just a property of the numeric value passed as `source_addr`/`target_addr`, since the driver already auto-detects `CAN_EFF_FLAG` for any ID > 0x7FF, and this plugin has always treated those fields as opaque CAN IDs (never a raw 4-bit DLC-style code). So "Normal Fixed" is just `addressing_mode=NORMAL` with a 29-bit-valued `target_addr`/`source_addr` (caller constructs the `0x18DAxxyy` value themselves), and "Mixed 29-bit" is `addressing_mode=MIXED` the same way. No 29-bit-specific code was needed beyond that.

**The real functional gap, found while designing this**: "extended addressing" as it existed before this fix only *added* a redundant address byte derived from the same CAN ID already used for routing (`target_addr & 0xFF`) — it could never do the one thing extended/mixed addressing exists for: letting multiple logical connections share one physical CAN ID, disambiguated by the address byte. Fixed by:
- `CanTpConfig` gains `addressing_mode` (`CanTpAddressingMode`: NORMAL/EXTENDED/MIXED) and `address_byte` (independently configurable N_TA/N_AE, 0 = derive from `target_addr & 0xFF` for backward compatibility).
- `find_by_target()` now collects *all* `target_addr` matches and, when there's more than one, disambiguates by reading the address byte (always `payload[0]` when present, regardless of 11- vs 29-bit ID — addressing mode only affects the CAN ID field, never the payload layout).
- `can_tp_configure()`'s duplicate-`target_addr` rejection (the fix for #3) is relaxed to allow exactly this case: sharing is fine when every sharer has an address byte and they're all distinct; anything else (a NORMAL sharer, or colliding address bytes) is still rejected as a genuine unresolvable ambiguity.
- `extended_addressing` (the old bool) is kept as a deprecated alias for `addressing_mode=EXTENDED`, so pre-existing configs keep working unchanged.

**A second real bug found via hardware testing, outside the CanTp plugin entirely**: `SocketCanDriver::ReadFrame()` (`src/hil/can/socket_can_driver.cpp`) copied `raw.can_id` straight through without masking off `CAN_EFF_FLAG`/`CAN_RTR_FLAG`/`CAN_ERR_FLAG`, so every received 29-bit-ID frame's `can_id` silently carried `CAN_EFF_FLAG` (0x80000000) while `WriteFrame()` and every downstream comparison (not just CanTp) treat `can_id` as a plain value. A 29-bit frame could never match a plain-valued `target_addr` on RX. Fixed by masking with `CAN_EFF_MASK`/`CAN_SFF_MASK` as appropriate — this was a pre-existing, system-wide bug affecting any 29-bit CAN ID use, not something introduced by this work; it just happened to be the first thing to actually exercise 29-bit IDs end-to-end.

**Verified on real hardware**: two connections sharing one `target_addr` with distinct `address_byte`s each correctly receive only their own data; a duplicate `target_addr` with NORMAL addressing (or colliding `address_byte`s) is rejected with `FAILED_PRECONDITION`; EXTENDED addressing with an `address_byte` independent of `target_addr`'s low byte round-trips correctly (confirmed the wire byte matches the configured value, not the derived one); a `0x18DAxxyy`-style 29-bit "Normal Fixed" transfer round-trips byte-exact.

### 16. CLI / Python SDK Loads Separate .so — ✅ RESOLVED (2026-08-10)

Original finding: `CanTpHandle.configure()`/`send()` passed `None` as the plugin context (`sdk/python/boat/can_tp.py:101`, old), spinning up a second, disconnected copy of the plugin `.so` with no CAN publisher wired and no shared state with the gateway-loaded instance — `boat can-tp send` could never actually transmit.

**Fixed by `c35034e`** ("wire CanTp to a live gRPC service, add multi-instance + node-plugin support"): `sdk/python/boat/can_tp.py` no longer touches ctypes/ the raw C ABI at all. `CanTpHandle` is now a thin wrapper around the `CanTpService` gRPC API — `configure()`/`send()`/`remove()`/`subscribe()`/`list_sessions()` all go through `self._client.can_tp.*` RPCs. Server-side, `can_tp_service_impl.cpp` resolves the request to the *live* plugin instance via `plugin_manager.FindService("can_tp:" + iface)` (each gateway-loaded instance registers itself under an iface-scoped service name — see `boat_plugin_service_name()` in `can_tp_plugin.cpp:649-652`). There is no longer any path that creates a second, unconnected plugin instance.

---

## Summary

**As of 2026-08-10, end of day** — after `c35034e`, `a247557`, and the `feat/can-tp-nbs-ncr-timeouts` + `feat/can-tp-hardening-quickfixes` branches (not yet merged to master):

| Priority | Items | Count |
|----------|-------|-------|
| 🔴 Critical | — | **0** |
| 🟡 Important | #6 No padding (RX validation deliberately deferred, see #6) | **1** |
| 🔵 Minor/Arch | #8 CF wrap desync (downgraded, mitigated by #1), #11 No error reporting, #14 Single mutex (reframed as intentional) | **3** |
| ✅ Resolved | #1 N_Bs/N_Cr timeouts (N_As/N_Ar/N_Br/N_Cs deliberately deferred), #2 Dangling pointer race, #3 find_by_target ambiguity (properly fixed via address-byte disambiguation, see #15), #4 SF threshold/FD escape format, #5 FF/CF payload calc (+ extended-addressing RX bug found alongside it), #7 FF min length, #9 Connection overwrite, #10 CAN FD BRS flag, #12 Busy-poll, #13 Triple-lock, #15 29-bit/mixed addressing (+ a SocketCAN driver bug found alongside it), #16 CLI separate .so | **12** |
| **Total** | | **16** |

**Original analysis (pre-2026-08-10), for reference:**

| Priority | Items | Count |
|----------|-------|-------|
| 🔴 Critical | #1 No timeouts, #2 Dangling pointer race, #3 find_by_target single match | **3** |
| 🟡 Important | #4 SF threshold hardcoded, #5 FF payload calc, #6 No padding, #7 FF min length, #8 CF wrap desync, #9 Connection overwrite, #10 CAN FD DLC encoding | **7** |
| 🔵 Minor/Arch | #11 No error reporting, #12 Busy-poll, #13 Triple-lock, #14 Single mutex, #15 No 29-bit/mixed, #16 CLI separate .so | **6** |
| **Total** | | **16** |

No Critical items remain, and 12 of the original 16 are now resolved. Two genuinely open items remain: **#6's RX padding validation** (deliberately deferred, see its writeup for why) and **#11 (no error/event reporting)** — the last real milestone, now more valuable since there are several silent-drop/timeout scenarios worth surfacing. #8 is downgraded/mitigated rather than fixed outright. #14 is closed as an intentional tradeoff, not a gap.

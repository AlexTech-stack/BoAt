# TCP Plugin Backlog

Remaining gaps, open points, and comments for the TCP plugin after Phase A+B+C hardening.

---

## 🔴 Protocol Gaps

### 1. Congestion Control — RFC 5681 / RFC 6582

AUTOSAR `[SRS_Eth_00099]` requires RFC 5681 (Slow-Start, Congestion Avoidance, Fast Retransmit, Fast Recovery). `[SRS_Eth_00100]` requires RFC 6582 NewReno.

The plugin has zero congestion control — no `cwnd`, `ssthresh`, or any congestion state variables. Nagle was implemented (RFC 896) but that is not congestion control.

**Excluded by design** — not needed in controlled lab/test environments. Left out of Phase C per agreement.

**Revisit if:** The plugin ever needs to operate over real WAN links with variable latency/loss.

---

### 2. RTT Estimation / Adaptive RTO / Karn's Algorithm

AUTOSAR `[SWS_TCPIP_00104]` → RFC 1122 §4.2.2.15. Retransmission timeout currently uses a fixed configurable `retry_ms` (default 1000ms) with exponential backoff. No RTT measurement (SRTT, RTTVAR). No Karn's algorithm.

**Impact:** Fixed RTO of 1s is too aggressive for high-latency links, too conservative for local links. Retransmit timing doesn't adapt to actual network conditions.

**Effort:** Medium. Requires storing per-connection RTT samples (SRTT, RTTVAR), updating on each ACK, computing RTO per RFC 6298.

---

## 🟡 Important Gaps

### 3. Simultaneous Open

AUTOSAR `[SWS_TCPIP_00104]` → RFC 1122 §4.2.2.10. Pure SYN arriving while in SYN_SENT should transition to SYN_RCVD. Currently the duplicate-detection guard at `tcp_plugin.cpp:601-616` catches it and jumps to `tcp_rx_done`.

**Impact:** Low. Simultaneous open is a rare edge case (both sides send SYN at the same time). The connection would fail to establish and need to be retried.

**Effort:** Small. Add a SYN-in-SYN_SENT handler before the duplicate check.

---

### 4. Window Scale Option (RFC 7323)

AUTOSAR `[SWS_TCPIP_00104]` → RFC 1122 §4.2.2.5 (TCP Options). The window field is 16 bits, capping window at 64KB. Window scale extends this. The plugin has no window scale option generation or parsing.

**Impact:** Low for lab use (< 64KB window is sufficient). Medium for real-world use (high BDP links need larger windows).

**Effort:** Medium. Add option kind 3 to `ParseMssOption`-style parser, add send logic in SYN/SYN-ACK segment builders.

---

### 5. Listen Backlog / Connection Limit

AUTOSAR `[SWS_TCPIP_00104]` → RFC 1122 §4.2.2.18. Currently every valid SYN creates a connection unconditionally. No limit on listener backlog or total connections.

**Impact:** Low in controlled env. Medium if someone connects thousands of peers — unbounded `connections` map, unbounded memory.

**Effort:** Small. Add `backlog` and `max_connections` fields to `TcpListener` and `TcpPlugin`. Reject SYN when limit reached.

---

### 6. ICMP Error Processing

RFC 793 §3.9 requires processing ICMP Destination Unreachable, Source Quench, etc. Currently only protocol 6 (TCP) is processed in `HandleIncoming` (line 419 `if (protocol != 6) return;`).

**Impact:** Low. ICMP errors (e.g., "Host Unreachable") go undetected — the connection times out instead of failing immediately.

**Effort:** Medium. Add an ICMP handler that matches ICMP payload TCP headers against connection 4-tuples, or wire into raw socket RX.

---

### 7. SWS Avoidance — Receiver Side

Sender-side SWS avoidance implemented via Nagle. Receiver-side (Clark's algorithm) not done — `rx_window` is static and never reduced based on receive buffer occupancy.

**Impact:** Low. In the current callback model, data is delivered synchronously to `on_data`, so receive buffer occupancy is effectively 0 immediately after delivery. No real SWS risk.

**Effort:** Low if needed. Implement Clark's: advance window only when `rx_occupied < rx_buf_size/2`.

---

### 8. RST in SYN_RCVD → LISTEN (Not CLOSED)

`ATS_TCP_00413` requires that an acceptable RST in SYN_RCVD returns to LISTEN state. Current code transitions to CLOSED (line 935-965). The `TcpListener` remains alive so new connections can still be accepted, but the literal state machine transition is non-compliant.

**Impact:** Low. Functional behavior is the same from the caller's perspective.

**Effort:** Small. After closing the connection, transition to LISTEN instead of CLOSED when the connection was created from a listener.

---

### 9. Initial RTO Default

AUTOSAR OA test spec `TCP_RETRANSMISSION_TO_06` says RTO should default to 3 seconds. Current default is 1000ms.

**Impact:** Low. 1s base is fine for lab networks. Change to 3000ms if spec compliance is needed.

**Effort:** Trivial — change `retry_ms` default in `tcp_plugin.h:143` or document that `"retry_ms": 3000` should be set for compliance.

---

## 🔵 Minor / Enhancement Requests

### 10. SACK (RFC 2018)

Not explicitly required by AUTOSAR docs found. Selective ACK improves loss recovery on WAN links. Not needed for lab use.

**Effort:** Large.

---

### 11. ECN (Explicit Congestion Notification)

Not required by AUTOSAR. Adds IP-level congestion signaling. Requires RFC 3168.

**Effort:** Large.

---

### 12. Path MTU Discovery

Currently DF=1 is hardcoded but ICMP "Frag Needed" is not processed. Path MTU > 1500 causes silent drops.

**Effort:** Medium.

---

## 🛠 Open Infrastructure Points

### 13. Single Global Mutex Contention

The plugin uses a single `std::recursive_mutex` for all connections. High connection counts will cause contention between TX thread, RX thread(s), and API calls.

**Revisit if:** Connection count exceeds ~100 active connections under load.

**Solution:** Per-connection locking, or sharded lock table.

---

### 14. Missing C++ Unit Tests for State Machine

Only `ParseMssOption` has a Catch2 test. The complex state machine logic (ACK validation, FIN transitions, out-of-order handling, keepalive, etc.) has no unit tests.

**Not urgent** — Python integration tests with veth pairs serve as functional tests. Add Catch2 tests if the state machine logic needs to be modified again.

---

### 15. TCP Plugin is a Module (.so)

Built via `add_boat_plugin()` as a MODULE library. Can't be linked into test executables. The `ParseMssOption` function was moved to inline in the header to work around this.

**Not blocking** — tests either include the header or run as Python integration.

---

## 📝 Comments

- **Phase A (protocol correctness)** is the most critical — all 6 items fully implemented.
- **Phase B (robustness)** — 5 of 6 items fixed. B5 (out-of-order) is working but basic (no complex overlap handling).
- **Phase C (flow control)** — all 5 items (C5 excluded) implemented. Keepalive default timers (2h idle, 75s interval, 9 retries) match RFC 1122 recommendations.
- **Nagle** defaults to ON (`"nagle": true`), matching AUTOSAR `[SRS_Eth_00109]` MUST requirement.
- **Congestion control** is the single largest remaining gap, excluded by design.
- **The relay script (`tcp_relay.py`)** no longer needs the workaround queue after the recursive mutex fix (B1).

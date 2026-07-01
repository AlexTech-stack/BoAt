# TCP Plugin vs AUTOSAR / IEEE Spec — Gap Analysis

Generated from analysis of:
- **Plugin source**: `boat-platform/src/plugins/tcp/` (tcp_plugin.h, tcp_plugin.cpp, tcp_segment.h — 943 lines C++)
- **Python SDK**: `boat-platform/sdk/python/boat/tcp.py`
- **Spec sources**: `AUTOSAR_CP_RS_Ethernet.txt`, `AUTOSAR_ATS_TCP.txt`, `9_OA_Automotive_Ethernet_ECU_TestSpecification_v1.txt`

### Legend

| Marking | Meaning |
|---------|---------|
| 🔴 **CRITICAL** | AUTOSAR MUST, plugin missing or broken |
| 🟡 IMPORTANT | AUTOSAR MUST/SHOULD, plugin partial or missing |
| 🔵 MINOR | AUTOSAR MAY or advanced feature |

---

## 1. TCP State Machine (RFC 793)

| Requirement | Spec classification | Plugin status | Gap |
|---|---|---|---|
| All 11 states (CLOSED, LISTEN, SYN_SENT, SYN_RCVD, ESTABLISHED, FIN_WAIT_1, FIN_WAIT_2, CLOSE_WAIT, CLOSING, LAST_ACK, TIME_WAIT) | **MUST** (AUTOSAR_ATS_TCP §3.1, `[SWS_TCPIP_00061]`) | 9 of 11: LISTEN and CLOSING missing from enum | 🔴 LISTEN handled via `TcpListener` struct (orthogonal, not state), CLOSING not implemented |
| FIN_WAIT_1 → FIN_WAIT_2 on ACK of our FIN | **MUST** (RFC 793 §3.5) | **Bug**: never moves to FIN_WAIT_2 | 🔴 FIN stays in `unacked_segment` forever, eventually timeout → ERROR |
| CLOSE_WAIT → LAST_ACK on app close | **MUST** (RFC 793 §3.5) | **Bug**: `tcp_close()` hardcodes FIN_WAIT_1 regardless of state | 🔴 Wrong seq space, peer sees invalid FIN |
| TIME_WAIT → CLOSED after 2×MSL | **MUST** (`ATS_TCP_00399`, `ATS_TCP_00401`) | **No timer**, connection leaks permanently | 🔴 Must implement 2×MSL timer |
| SYN_SENT → CLOSED on RST | **MUST** (RFC 793 §3.4) | RST in SYN_SENT silently ignored | 🔴 Connection refused goes undetected |
| Simultaneous open (SYN in SYN_SENT → SYN_RCVD) | **MUST** (RFC 1122 §4.2.2.10) | Not implemented | 🟡 |
| LISTEN → CLOSED on unacceptable ACK | **MUST** (`ATS_TCP_00430`) | No ACK validation in listener path | 🟡 |

---

## 2. Reliability & Retransmission

| Requirement | Spec classification | Plugin status | Gap |
|---|---|---|---|
| Implement RFC 793 TCP | **MUST** (`[SWS_TCPIP_00061]`) | Partial | 🔴 State machine gaps above |
| RFC 1122 compliance (15 sections) | **MUST** (`[SWS_TCPIP_00104]`) | See rows below | — |
| **ACK validation**: clear retransmit buffer on peer ACK | **MUST** (RFC 793 §3.4) | **Critical bug**: `unacked_segment` *never* cleared by data ACKs | 🔴 Every data segment retransmitted → timeout → ERROR |
| Retransmit: exponential backoff | **MUST** (OA `TCP_RETRANSMISSION_TO_04`) | Yes, exponential backoff implemented | ✅ |
| Karn's algorithm | **MUST** (OA `TCP_RETRANSMISSION_TO_03`) | Not implemented (uses fixed config, no RTT measurement) | 🟡 |
| RTT estimation / adaptive RTO | **SHOULD** (RFC 6298) | Not implemented (fixed `retry_ms`, configurable) | 🔴 `[SRS_Eth_00019]` → RFC 1122 §4.2.2.15 |
| Initial RTO = 3 sec | **SHOULD** (OA `TCP_RETRANSMISSION_TO_06`) | Default is 1000ms (configurable) | 🟡 |
| RTO upper bound = 2×MSL | **SHOULD** (OA `TCP_RETRANSMISSION_TO_08`) | No upper bound | 🟡 |
| Checksum validation on receive | **MUST** (`ATS_TCP_00410`–`00412`) | Not done (checksum calculated but not verified) | 🔴 Corrupted data accepted silently |

---

## 3. Congestion Control

| Requirement | Spec classification | Plugin status | Gap |
|---|---|---|---|
| RFC 5681 (Slow-Start, Congestion Avoidance, Fast Retransmit, Fast Recovery) | **MUST** (`[SRS_Eth_00099]`) | **None** — no cwnd, ssthresh, no congestion state | 🔴 Major gap |
| RFC 6582 NewReno | **MUST** (`[SRS_Eth_00100]`) | None | 🔴 |
| Nagle algorithm (RFC 896) | **MUST** (`[SRS_Eth_00109]`) | Not implemented. Small writes sent immediately on next 100ms poll | 🔴 |

---

## 4. Flow Control

| Requirement | Spec classification | Plugin status | Gap |
|---|---|---|---|
| Window size management (RFC 1122 §4.2.2.3) | **MUST** | Hardcoded 65535 in every segment | 🔴 Cannot advertise smaller window |
| Peer window tracking (RFC 1122 §4.2.2.16) | **MUST** | Peer's window field never read | 🔴 Can overflow peer's receive buffer |
| Zero-window probing (RFC 1122 §4.2.2.17) | **MUST** | Not implemented | 🔴 |
| SWS avoidance | **MUST** (OA `TCP_AVOIDANCE` tests) | Not implemented | 🔴 |

---

## 5. TCP Options

| Requirement | Spec classification | Plugin status | Gap |
|---|---|---|---|
| MSS Option: send | **MUST** (OA `TCP_MSS_OPTIONS_11`) | ✅ Sent in SYN and SYN-ACK | ✅ |
| MSS Option: receive + calculate effective send MSS | **MUST** (OA `TCP_MSS_OPTIONS_06`) | Peer MSS *never parsed* — always uses own MSS | 🔴 |
| Default MSS = 536 if no option received | **MUST** (OA `TCP_MSS_OPTIONS_07`) | Not implemented — always uses 1460 | 🔴 |
| NOP option: able to receive | **MUST** (RFC 1122 §4.2.2.5) | Not parsed (no option parser) | 🟡 |
| End of Options List: able to receive | **MUST** | Not parsed | 🟡 |
| Unimplemented options: ignore | **MUST** | No option parser at all | 🟡 Weak (could cause bugs) |
| Window Scale (RFC 7323) | **MUST** (via RFC 1122 §4.2.2.3) | Not implemented | 🟡 |
| SACK (RFC 2018) | Not explicitly found in AUTOSAR docs | Not implemented | 🔵 |

---

## 6. Error Handling

| Requirement | Spec classification | Plugin status | Gap |
|---|---|---|---|
| RST in ESTABLISHED → CLOSED + EVENT_RST | **MUST** | ✅ | ✅ |
| RST in SYN_RCVD → return to LISTEN | **MUST** (`ATS_TCP_00413`) | → CLOSED, not LISTEN | 🔴 |
| RST in SYN_RCVD: ignore unacceptable | **MUST** (`ATS_TCP_00414`) | Not implemented (no RST validation) | 🔴 |
| RST in SYN_SENT | **MUST** (RFC 793) | Silently ignored | 🔴 |
| Unacceptable ACK → send RST | **MUST** (`ATS_TCP_00415`, `ATS_TCP_00430`–`00433`) | Not implemented | 🔴 |
| Out-of-sequence segment: ACK with correct SEQ/ACK, stay in state | **MUST** (`ATS_TCP_00416`–`00429`) | No out-of-sequence handling — assumes in-order | 🔴 |
| ICMP error processing | **MUST** (RFC 793 §3.9) | No ICMP handler | 🟡 |

---

## 7. API & Architecture

| Requirement | Spec classification | Plugin status | Gap |
|---|---|---|---|
| Socket-based API for upper layers (`[SRS_Eth_00103]`) | **MUST** | Has C ABI (7 functions) + Python SDK + C header | ✅ at API level, ❌ not AUTOSAR SoAd |
| PDU-based communication with lower layer (`[SRS_Eth_00187]`) | **MUST** | Uses `BoatEthPublishFn` / raw AF_PACKET — not PDU-based | 🟡 Different architecture |
| Listen backlog / connection limit | **MUST** (RFC 1122 §4.2.2.18) | Unlimited (no backlog, infinite accepts) | 🟡 |
| TCP keepalive (RFC 1122 §4.2.3.6) | **MUST** | Not implemented | 🟡 |

---

## 8. Thread Safety

| Issue | Severity |
|---|---|
| Callbacks invoked under plugin mutex → deadlock if re-entrant | 🔴 Must be deferred (relay workaround in Python) |
| Mutex unlock/relock around `SendRaw` → connection could be destroyed | 🟡 |
| Single global mutex → no concurrent connection processing | 🟡 |

---

## Summary

| Priority | Count | Key items |
|----------|-------|-----------|
| 🔴 Critical (MUST, broken/missing) | **15** | State machine bugs (5), ACK validation, congestion control, Nagle, window tracking, zero-window probe, MSS from peer, checksum validation, RST handling gaps, out-of-order handling, SWS avoidance |
| 🟡 Important (MUST/SHOULD, partial) | **10** | RTT estimation, adaptive RTO, Window Scale, keepalive, backlog, ICMP, NOP/options parsing, simultaneous open, listen-back-to-listener |
| 🔵 Minor/Advanced | **3** | SACK, ECN, PMTUD |

### Top 3 Most Impactful Fixes

1. **ACK validation** — clear `unacked_segment` on peer ACK. Without this, data transfer fails after `max_retries` segments.
2. **Closing state machine** — fix FIN_WAIT_1→FIN_WAIT_2, CLOSE_WAIT→LAST_ACK, TIME_WAIT→CLOSED (2×MSL timer).
3. **Congestion control** — implement RFC 5681 minimum (slow start, congestion avoidance).

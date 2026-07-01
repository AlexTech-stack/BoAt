# Sprint Plan: TCP Plugin Hardening — Phase A + B

**Branch:** `Feature_tcp_plugin_hardening`  
**Goal:** Fix critical protocol-correctness bugs and robustness gaps for basic, reliable TCP operation  
**Test framework:** Catch2 + Python demo scripts against veth pairs  
**Test command:** `ctest --preset release --output-on-failure`

---

## Phase A — Protocol Correctness

| # | Task | What & Why | Files | Effort |
|---|------|------------|-------|--------|
| A1 | **ACK validation** | Parse peer's ACK field in incoming segments, clear `unacked_segment` for acknowledged bytes. Without this, every data segment is retransmitted until `max_retries` exhaustion → connection death | `tcp_plugin.cpp:591-603` | Medium |
| A2 | **FIN_WAIT_1 → FIN_WAIT_2** | On ACK for our FIN while in FIN_WAIT_1, transition to FIN_WAIT_2. Currently never moves, FIN stays in `unacked_segment` → timeout → ERROR | `tcp_plugin.cpp:587-593` | Small |
| A3 | **CLOSE_WAIT → LAST_ACK** | `tcp_close()` must check current state; from CLOSE_WAIT send FIN → LAST_ACK instead of hardcoded FIN_WAIT_1. Currently wrong seq space, peer sees invalid FIN | `tcp_plugin.cpp:875-911` | Small |
| A4 | **TIME_WAIT 2×MSL timer** | Add monotonic-clock timer that transitions TIME_WAIT → CLOSED after 2×MSL (120s). Connections currently leak permanently in the `connections` map | `tcp_plugin.h:116-118`<br>`tcp_plugin.cpp:630-655` | Medium |
| A5 | **RST in SYN_SENT** | Handle RST during active open → fire `TCP_EVENT_ERROR`. Connection refused currently silently ignored, caller never knows | `tcp_plugin.cpp:550-585` | Small |
| A6 | **MSS parse from peer** | Parse MSS option from SYN/SYN-ACK. Use `min(our_mss, peer_mss)`. Default to 536 if absent per RFC 1122 §4.2.2.6 mandate. Currently always uses own 1460 | `tcp_plugin.cpp` options parser | Medium |

**Dependencies:** A1 → test harness (needed for other tests)  
**Suggested order:** A1 → A2 → A3 → A5 → A6 → A4

---

### Phase A — Detailed Implementation Plan

#### A1 — ACK Validation (Medium)

**Bug:** `tcp_plugin.cpp:591-593` — peer's ACK field is dead code (`(void)ack`). Every data segment stays in `unacked_segment` until `max_retries` exhaustion → connection killed.

**Fix:** Replace lines 591-594 in the ESTABLISHED/FIN_WAIT_1/FIN_WAIT_2 ACK block:

```cpp
// If peer ACKs up to or past our next seq, the outstanding segment is acknowledged
if (!conn.unacked_segment.empty() && ack >= conn.my_seq) {
    conn.unacked_segment.clear();
}
```

`my_seq` points to the next byte we *would* send. When we send N bytes, `my_seq += N`. So ACK ≥ `my_seq` means all bytes (or the FIN flag) were received and the segment can be cleared.

**Test:** Python integration — `tcp_send_client` sends 6+ messages to `tcp_listen_server` over veth. Capture with tcpdump, verify no retransmissions (no duplicate sequence numbers), connection stays alive.

---

#### A2 — FIN_WAIT_1 → FIN_WAIT_2 (Small)

**Bug:** `tcp_plugin.cpp:587-589` — FIN_WAIT_1 falls through same handler as ESTABLISHED but never transitions. With A1, the FIN's `unacked_segment` can be cleared but the state never advances.

**Fix:** Add after line 621 (after the reactive ACK block, before FIN flag check):

```cpp
if (conn.state == btcp::TCP_FIN_WAIT_1 && conn.unacked_segment.empty()) {
    conn.state = btcp::TCP_FIN_WAIT_2;
}
```

**Test:** Python integration — client connects, sends data, calls `tcp.close()`. Verify via tcpdump: server receives FIN → CLOSE_WAIT → server closes → server FIN sent → client ACKs server FIN → server transitions FIN_WAIT_1 → FIN_WAIT_2.

---

#### A3 — CLOSE_WAIT → LAST_ACK (Small)

**Bug:** `tcp_plugin.cpp:900` — `tcp_close()` always sets state to `FIN_WAIT_1`. From CLOSE_WAIT, this sends FIN with wrong seq space.

**Fix 1 — `tcp_close()`:** Replace line 900 with state-aware transition:

```cpp
if (conn.state == btcp::TCP_ESTABLISHED || conn.state == btcp::TCP_SYN_RCVD) {
    conn.state = btcp::TCP_FIN_WAIT_1;
} else if (conn.state == btcp::TCP_CLOSE_WAIT) {
    conn.state = btcp::TCP_LAST_ACK;
} else {
    return -1;
}
```

**Fix 2 — `HandleIncoming`:** Add `TCP_LAST_ACK` to the case label at line 587-589 alongside ESTABLISHED/FIN_WAIT_1/FIN_WAIT_2 for ACK handling. Skip data delivery in LAST_ACK (RFC 793: MUST ignore data segments in LAST_ACK).

**Test:** Python integration — server receives data from client, client sends FIN → server receives CLOSE_WAIT event, server calls `tcp.close()`, traces show LAST_ACK state.

---

#### A4 — TIME_WAIT 2×MSL Timer (Medium)

**Bug:** `tcp_plugin.cpp:651` — connections enter TIME_WAIT and stay there forever, leaking slots in `connections` map.

**Fix 1 (header):** Add `std::chrono::steady_clock::time_point time_wait_until{};` to `TcpConnection`.

**Fix 2 (header):** Add `uint32_t time_wait_ms{120000};` to `TcpPlugin` (2×MSL, configurable via JSON `"time_wait_ms"`).

**Fix 3 (entry):** When entering TIME_WAIT at line 651: `conn.time_wait_until = now + std::chrono::milliseconds(plugin->time_wait_ms);`

**Fix 4 (TX thread cleanup):** After connection iteration loop, erase expired TIME_WAIT connections silently (no callback).

**Fix 5 (config):** Parse `"time_wait_ms"` from JSON in `tp_initialize`.

**Test:** Python integration — client connects → closes normally. Verify via stderr that the connection slot is freed after timeout.

---

#### A5 — RST in SYN_SENT (Small)

**Bug:** `tcp_plugin.cpp:550-585` — SYN_SENT only handles SYN-ACK. RST is silently ignored.

**Fix:** Add RST handler inside SYN_SENT case, before SYN-ACK check:

```cpp
if (flags & 0x04) {  // RST
    if (ack == conn.my_seq) {
        conn.state = btcp::TCP_CLOSED;
        conn.unacked_segment.clear();
        if (conn.on_event)
            conn.on_event(conn.user_ctx, conn.conn_id, btcp::TCP_EVENT_ERROR);
    }
} else if (flags & 0x12) {  // SYN-ACK
```

**Test:** Python integration — `tcp_connect` to non-listening port. Verify `on_event` fires `TCP_EVENT_ERROR` (event=4).

---

#### A6 — MSS Parse from Peer (Medium)

**Bug:** Peer's MSS option never parsed. Always uses own `default_mss` (1460).

**Fix 1 — new helper:** `ParseMssOption(options, opt_len)` — parses TCP options, returns MSS value or 536 default.

**Fix 2 — SYN-ACK (active open):** After matching SYN-ACK, parse options from segment, `conn.mss = min(conn.mss, peer_mss)`.

**Fix 3 — SYN (passive open):** After setting `conn.mss`, parse options from incoming SYN, `conn.mss = min(conn.mss, peer_mss)`.

**Test (C++ unit):** `src/tests/unit/test_tcp_options.cpp` — test `ParseMssOption` with various option sequences.
**Test (Python integration):** Client with small MSS connects to server, verify server respects client's MSS.

---

### CMakeLists.txt — Test Target for A6

```cmake
add_executable(boat_unit_tcp_options unit/test_tcp_options.cpp)
target_include_directories(boat_unit_tcp_options PRIVATE ${CMAKE_SOURCE_DIR}/src/plugins/tcp)
target_link_libraries(boat_unit_tcp_options PRIVATE boat_hil Catch2::Catch2WithMain)
catch_discover_tests(boat_unit_tcp_options)
```

### Implementation Order

```
A1 (ACK validation) → A2 (FIN_WAIT_1→FIN_WAIT_2) → A3 (CLOSE_WAIT→LAST_ACK) → A5 (RST in SYN_SENT)
A6 (MSS parse) — independent, parallel with A1
A4 (TIME_WAIT timer) — independent, last to avoid TX thread merge conflicts
```

### Verification

```bash
cmake --build --preset debug && ctest --preset release -R "tcp" --output-on-failure
# Integration:
sudo ip link add veth0 type veth peer name veth1
sudo ip addr add 120.120.120.1/24 dev veth0 && sudo ip addr add 120.120.120.2/24 dev veth1
sudo ip link set veth0 up && sudo ip link set veth1 up
sudo python3 demo/tcp_plugin/tcp_listen_server.py veth0 120.120.120.1 9999 &
sudo python3 demo/tcp_plugin/tcp_send_client.py veth1 120.120.120.2 0 120.120.120.1 9999 AABBCCDDEEFF
sudo ip link del veth0
```

---

## Phase B — Robustness & Error Handling

| # | Task | What & Why | Files | Effort |
|---|------|------------|-------|--------|
| B1 | **Recursive mutex** | Replace `std::mutex` with `std::recursive_mutex` in `TcpPlugin`. Removes the deadlock that forced the queue-based workaround in `tcp_relay.py`. Callbacks can safely call `tcp.send`/`tcp.close`/`tcp.connect` again, matching the clean `tcp_listen_server.py` pattern | `tcp_plugin.h:111` | Small |
| B2 | **Checksum validation on RX** | Verify TCP checksum on incoming segments at top of `HandleIncoming`, drop silently on mismatch. Currently corrupt data is accepted as valid. AUTOSAR MUST (`ATS_TCP_00410`–`00412`) | `tcp_plugin.cpp:365-430` | Small |
| B3 | **RST validation (RFC 5961)** | Only process RST if `seg.seq == rcv.nxt`; otherwise send challenge ACK and stay in state. Prevents blind reset attacks and spurious connection kills from stale segments | `tcp_plugin.cpp:657-678` | Medium |
| B4 | **Unacceptable ACK → send RST** | Validate incoming ACK against expected ack number per-state. Send RST on unacceptable ACK (e.g., ACK for data not yet sent). AUTOSAR MUST (`ATS_TCP_00415`, `ATS_TCP_00430`–`00433`) | `tcp_plugin.cpp` ESTABLISHED/SYN_RCVD handling | Medium |
| B5 | **Out-of-order segment handling** | Accept segments matching correct 4-tuple, ACK `rcv.nxt`, deliver contiguous data to app, buffer out-of-order segments. Vital for WAN/lossy links. AUTOSAR MUST (`ATS_TCP_00416`–`00429`) | `tcp_plugin.cpp:595-601` + new receive buffer | Large |
| B6 | **Add CLOSING / LISTEN to state enum** | Flesh out enum to all 11 RFC 793 states. Supports test tooling and logs that query current state | `tcp_plugin.h:35-45` | Small |

**Dependencies:** B1 → independent. B2 → independent. B3 → independent (touches RST handlers, benefits from B1). B4 → independent. B5 → most complex, depends on B1 for safe buffer access from callbacks. B6 → independent.

---

### Phase B — Detailed Implementation Plan

#### B1 — Recursive Mutex (Small)

**Bug:** `tcp_plugin.h:113` — `std::mutex mutex;` is non-recursive. `HandleIncoming` holds the mutex while calling `on_data`/`on_event` Python callbacks. Any `tcp.send()`/`tcp.close()`/`tcp.connect()` from within a callback tries to relock the same mutex → **deadlock**. This forced the queue-based workaround in `tcp_relay.py`.

**Fix:** Replace `std::mutex mutex;` with `std::recursive_mutex mutex;` in `TcpPlugin`. Also update the lock_guard types in `tcp_plugin.cpp` (they already use `std::lock_guard` which works with both types — no change needed, but verify).

**Side effect:** After B1, `tcp_relay.py` can be simplified to remove the `work_q` queue and call `tcp.connect()`/`tcp.send()`/`tcp.close()` directly from callbacks — the same clean pattern as `tcp_listen_server.py`.

**Test:** Python integration — modify `tcp_relay.py` to call `tcp.send()` directly from `on_data` callback (remove queue). Verify relay forwards messages without deadlock.

---

#### B2 — Checksum Validation on RX (Small)

**Bug:** Incoming TCP checksum is never verified. Corrupt data is silently accepted and delivered to `on_data`. AUTOSAR MUST per `ATS_TCP_00410`–`00412`.

**Fix:** Add checksum validation after parsing the TCP header (after line 442) and before connection matching. If checksum is invalid, drop the segment silently.

**Logic:**
- After `const uint8_t* tcp_data = ...` (line 442), compute the TCP checksum over the entire TCP segment
- The incoming checksum is at `ip_payload[16] << 8 | ip_payload[17]`
- Zero out the checksum field: `ip_payload[16] = 0; ip_payload[17] = 0;`
- Compute TCP segment length: `tcp_seg_len = data_off + tcp_payload_len`
- Sum all 16-bit words of the TCP segment (header + payload)
- Add pseudo-header: src_ip + dst_ip + protocol (6) + tcp_seg_len
- Compare computed checksum with incoming checksum
- If mismatch → `goto tcp_rx_done;` (drop silently)
- Restore the checksum field from parsed `ack`? No — we already parsed it. We can leave it zeroed; the checksum isn't needed after this point.

**Important:** Must work for both IPv4 and IPv6. For IPv4 pseudo-header: src_ip (4 bytes) + dst_ip (4 bytes) + zero (1 byte) + protocol (1 byte = 6) + tcp_len (2 bytes) = 12 bytes. For IPv6 pseudo-header: src_ip (16 bytes) + dst_ip (16 bytes) + tcp_len (4 bytes) + protocol (4 bytes, zero-padded to 32-bit 6).

The existing `PseudoChecksum` in `tcp_segment.h` handles both via the `ip_len_bytes` parameter (4 for IPv4, 16 for IPv6).

**Test:** C++ unit test — test checksum validation with known-good and known-bad segments. Or Python integration — send intentionally corrupted segment via raw socket, verify `on_data` is never called.

---

#### B3 — RST Validation (RFC 5961) (Medium)

**Bug:** RST segments are processed unconditionally — any RST that matches the 4-tuple kills the connection. A stale/forged RST with wrong sequence number can tear down a healthy connection. RFC 5961 §3: only accept RST if `seq == rcv.nxt`. Otherwise, send a challenge ACK.

**Locations to fix — all RST handlers:**
1. `tcp_plugin.cpp:571-578` — SYN_SENT RST handler (added in A5)
2. `tcp_plugin.cpp:695-699` — ESTABLISHED/FIN_WAIT RST handler
3. `tcp_plugin.cpp:710-715` — SYN_RCVD RST handler

**Logic for each:**
```cpp
if (flags & 0x04) {  // RST
    if (seq == conn.my_ack) {  // RFC 5961: valid RST must match rcv.nxt
        conn.state = btcp::TCP_CLOSED;
        // ... existing teardown logic ...
    } else {
        // Challenge ACK — ACK what we already have
        // Send ACK(seq=my_seq, ack=my_ack) to prompt legitimate response
        std::vector<uint8_t> ack_seg;
        if (af == AF_INET) {
            ack_seg = btcp::BuildIp4TcpSegment(
                conn.src_ip.data(), conn.dst_ip.data(),
                conn.src_port, conn.dst_port,
                conn.my_seq, conn.my_ack,
                nullptr, 0, btcp::TCP_FLAG_ACK, 65535);
        } else {
            ack_seg = btcp::BuildIp6TcpSegment(
                conn.src_ip.data(), conn.dst_ip.data(),
                conn.src_port, conn.dst_port,
                conn.my_seq, conn.my_ack,
                nullptr, 0, btcp::TCP_FLAG_ACK, 65535);
        }
        plugin->mutex.unlock();
        SendRaw(plugin, ack_seg);
        plugin->mutex.lock();
    }
}
```

- In SYN_SENT: `conn.my_ack` is 0 (no data received yet), so only RST with seq=0 is valid (which covers a connection-refused RST that ACKs the SYN — handled by A5's `ack == conn.my_seq` check which is different from `seq == conn.my_ack`). Actually in SYN_SENT, we should check `ack == conn.my_seq` (already done in A5). We can add `seq == ...` as well or keep the A5 logic. The A5 check is already correct for SYN_SENT.

- In SYN_RCVD: `conn.my_ack = their_seq + 1`. A valid RST from the peer would have `seq == conn.my_ack` (the next expected sequence from them).

- In ESTABLISHED: `conn.my_ack` tracks the next seq we expect. Valid data/RST has `seq >= conn.my_ack` in the valid window. For RST specifically, RFC 5961 says it MUST equal rcv.nxt. But RFC 793 originally says `seq` must be within the window `[rcv.nxt, rcv.nxt + rcv.wnd)`. We'll use the stricter RFC 5961 check.

**Test:** Python integration — construct and send a crafted RST with wrong sequence number. Verify connection stays alive (challenge ACK received, no TCP_EVENT_RST fired).

---

#### B4 — Unacceptable ACK → Send RST (Medium)

**Bug:** No validation of incoming ACK field against what we've actually sent. A peer sending spurious ACKs can advance our state incorrectly. AUTOSAR MUST per `ATS_TCP_00415`, `ATS_TCP_00430`–`00433`.

**Locations to fix:**

**1. SYN_RCVD handler (line 702-708):** Currently `if (flags & 0x10)` transitions to ESTABLISHED unconditionally. Add ACK validation:
```cpp
if (flags & 0x10) {
    if (ack == conn.my_seq) {  // Must ACK our SYN+1
        conn.state = btcp::TCP_ESTABLISHED;
        conn.unacked_segment.clear();
        if (conn.on_event) conn.on_event(... TCP_EVENT_CONNECTED);
    } else {
        // Unacceptable ACK → send RST
        // RST should have seq = ack from incoming segment
        std::vector<uint8_t> rst_seg;
        // ... build RST segment with seq=ack ...
        SendRaw(plugin, rst_seg);
    }
}
```

**2. ESTABLISHED/FIN_WAIT handler (line 608+):** Add ACK validation:
```cpp
if (flags & 0x10) {
    if (ack > conn.my_seq) {
        // ACKs data we never sent → unacceptable
        // Send ACK with correct seq/ack
        std::vector<uint8_t> ack_seg;
        // ... build ACK with conn.my_seq / conn.my_ack ...
        SendRaw(plugin, ack_seg);
    } else {
        // ... existing ACK processing ...
    }
}
```

Note: We ACK rather than RST here, per RFC 793 §3.4 "If the ACK is acking something not yet sent, respond with an ACK".

**Test:** Python integration — craft raw TCP segment with spurious ACK > snd.nxt. Verify server sends corrective ACK (does not crash or transition state).

---

#### B5 — Out-of-Order Segment Handling (Large)

**Bug:** `HandleIncoming` assumes in-order delivery. If a segment arrives with `seq > my_ack`, the `my_ack` is advanced incorrectly (`conn.my_ack = seq + tcp_payload_len` at line 641), creating a gap. Out-of-order data is never reordered or buffered.

**Fix — header (`tcp_plugin.h`):** Add receive buffer to `TcpConnection`:
```cpp
// Out-of-order receive buffer: seq_start → {seq_start, data}
std::map<uint32_t, std::vector<uint8_t>> receive_buffer;
// Next expected sequence (duplicate of my_ack, but useful for clarity)
// my_ack already serves as rcv.nxt
```

**Fix — `HandleIncoming` ESTABLISHED/FIN_WAIT data handler (line 635+):** Restructure:
```cpp
if (tcp_payload_len > 0) {
    if (seq == conn.my_ack) {
        // In-order data → deliver to app
        if (conn.on_data)
            conn.on_data(conn.user_ctx, conn.conn_id, tcp_data, tcp_payload_len);
        conn.my_ack = seq + tcp_payload_len;

        // Check receive buffer for now-contiguous segments
        while (!conn.receive_buffer.empty()) {
            auto it = conn.receive_buffer.begin();
            if (it->first == conn.my_ack) {
                if (conn.on_data)
                    conn.on_data(conn.user_ctx, conn.conn_id,
                                 it->second.data(),
                                 static_cast<uint32_t>(it->second.size()));
                conn.my_ack += static_cast<uint32_t>(it->second.size());
                conn.receive_buffer.erase(it);
            } else {
                break;
            }
        }
    } else if (seq > conn.my_ack) {
        // Out-of-order → buffer it
        std::vector<uint8_t> buf(tcp_data, tcp_data + tcp_payload_len);
        conn.receive_buffer[seq] = std::move(buf);
    }
    // else seq < my_ack: duplicate data, already acknowledged

    // Send ACK with current rcv.nxt (my_ack)
    // ... (existing reactive ACK code, use conn.my_ack as ACK field) ...
}
```

**Important changes:**
- The reactive ACK must now always use `conn.my_ack` (rcv.nxt) — already does via `conn.my_ack = seq + tcp_payload_len` then using `conn.my_ack` in the ACK segment
- Duplicate data (seq < my_ack) must still trigger an ACK (to update the peer)
- Buffer must be cleaned on connection close/abort
- No overlap handling needed for initial version (assume clean segments)

**Test:** Python integration — use scapy or raw sockets to send data segments in reverse order (seq 1000 then seq 0). Verify server delivers in correct order (0 then 1000) with correct `my_ack` advancement. Verify via tcpdump that ACKs reflect `rcv.nxt` (not the out-of-order seq).

---

#### B6 — Add CLOSING / LISTEN to State Enum (Small)

**Purpose:** Complete the RFC 793 state machine enum. Currently 9 of 11 states are present in `TcpState`. Missing: `TCP_CLOSING` and `TCP_LISTEN`.

**Fix (`tcp_plugin.h:35-45`):** Add two new states:

```cpp
enum TcpState : uint8_t {
  TCP_CLOSED,
  TCP_LISTEN,     // new
  TCP_SYN_SENT,
  TCP_SYN_RCVD,
  TCP_ESTABLISHED,
  TCP_CLOSE_WAIT,
  TCP_CLOSING,    // new — simultaneous close
  TCP_LAST_ACK,
  TCP_FIN_WAIT_1,
  TCP_FIN_WAIT_2,
  TCP_TIME_WAIT,
};
```

**No runtime changes required:**
- `TCP_LISTEN` — already handled via `TcpListener` struct; adding to enum for logging/tooling
- `TCP_CLOSING` — would be entered during simultaneous close (both sides send FIN before receiving other's FIN). Current code at line 687-692 handles this via FIN_WAIT_1 receiver path but goes directly to TIME_WAIT. For now, just add the enum value; actual CLOSING state transition is future work (Phase C or later).

**Test:** Compilation check — verify the enum compiles and existing state assignments still work. No behavior change expected.

---

### Phase B — Implementation Order

```
B1 (recursive mutex) — independent, enabler for clean callbacks
B2 (checksum validation) — independent, after TCP header parse
B6 (CLOSING/LISTEN enum) — independent, header-only
B3 (RST validation) — touches RST handlers in SYN_SENT, ESTABLISHED, SYN_RCVD
B4 (unacceptable ACK → RST/ACK) — touches ACK handlers in SYN_RCVD, ESTABLISHED
B5 (out-of-order) — most complex, after B1 for safe buffer manipulation
```

### B1 Side Effect — Simplify tcp_relay.py

After B1, update `tcp_relay.py` to remove the `work_q` queue and call `tcp.connect()`/`tcp.send()`/`tcp.close()` directly from callbacks:

```python
# No more work_q — all tcp.* calls happen directly in callbacks
in_to_out: dict[int, int] = {}

def on_data(cid: int, data: bytes) -> None:
    out_cid = in_to_out.get(cid)
    if out_cid is not None:
        tcp.send(out_cid, data)

def on_event(cid: int, event: int) -> None:
    if event == 0:  # TCP_EVENT_CONNECTED
        out_cid = tcp.connect(listen_ip, 0, relay_dst_ip, relay_dst_port)
        in_to_out[cid] = out_cid
    elif event == 1:  # TCP_EVENT_CLOSED
        out_cid = in_to_out.pop(cid, None)
        if out_cid: tcp.close(out_cid)
    elif event == 4:  # TCP_EVENT_ERROR
        out_cid = in_to_out.pop(cid, None)
        if out_cid: tcp.abort(out_cid)
```

## Testing Strategy

### Unit Tests (Catch2)

Each task gets a Catch2 test case. Test binary naming: `boat_unit_tcp_*`

```bash
ctest --preset release --output-on-failure -R "tcp"
```

### Integration Test (Python + veth pair)

```bash
# Setup
sudo ip link add veth0 type veth peer name veth1
sudo ip addr add 120.120.120.1/24 dev veth0
sudo ip addr add 120.120.120.2/24 dev veth1
sudo ip link set veth0 up && sudo ip link set veth1 up

# Run relay on veth0
sudo python3 demo/tcp_plugin/tcp_relay.py veth0 120.120.120.1 9999 120.120.120.3 1234 &

# Send from veth1 → relay should forward to 120.120.120.3:1234
sudo python3 demo/tcp_plugin/tcp_send_client.py veth1 120.120.120.2 0 120.120.120.1 9999 AABBCCDD

# Teardown
sudo ip link del veth0
```

### Regression Check

Existing demos must still work after Phase A+B:
```bash
sudo python3 demo/tcp_plugin/tcp_listen_server.py veth0 120.120.120.1 9999 &
sudo python3 demo/tcp_plugin/tcp_send_client.py veth1 120.120.120.2 0 120.120.120.1 9999
```

After B1 (recursive mutex), `tcp_relay.py` can be simplified to call `tcp.connect`/`tcp.send` directly from callbacks (remove queue).

---

## Phase C — Flow Control & Performance

| # | Task | Effort |
|---|------|--------|
| C1 | Peer window tracking | Medium |
| C2 | Receive window advertisement | Medium |
| C3 | Nagle's algorithm | Medium |
| C4 | TCP keepalive | Medium |
| C5 | ~~RFC 5681 Congestion Control~~ — excluded (not in scope for lab/test environments) | — |
| C6 | Zero-window probing | Medium |

**Dependencies:** C1 → C6 (probing requires window tracking). C2, C3, C4 → independent.

---

### Phase C — Detailed Implementation Plan

#### C1 — Peer Window Tracking (Medium)

**Bug:** Peer's TCP window field (`ip_payload[14-15]`) is never parsed. All outgoing data is sent at full MSS regardless of peer's buffer capacity. Can overflow remote receive buffer.

**Fix 1 — header (`tcp_plugin.h`):** Add field to `TcpConnection`:
```cpp
uint32_t peer_window{65535};  // latest advertised window from peer
```

**Fix 2 — `HandleIncoming`:** Parse window field after TCP header parse (after line 436):
```cpp
uint16_t window = static_cast<uint16_t>((ip_payload[14] << 8) | ip_payload[15]);
```

**Fix 3 — Update peer_window on match:** After matching the connection (in the `from_them` block, same place where `my_ack`/`my_seq` are updated), store:
```cpp
conn.peer_window = window;
```

**Fix 4 — TX thread (line 253):** Cap send chunk to peer's window:
```cpp
uint32_t chunk = std::min<uint32_t>({
    static_cast<uint32_t>(conn.send_buffer.size()),
    static_cast<uint32_t>(conn.mss),
    conn.peer_window
});
```

Also skip sending if peer window == 0 (add guard: `if (conn.peer_window == 0) continue;` before the send_buffer drain block).

**Test:** Python integration — client advertises small window in segment. Verify server sends data chunks ≤ advertised window size. Verify server stops sending when window == 0.

---

#### C2 — Receive Window Advertisement (Medium)

**Bug:** Outgoing window field is hardcoded to 65535 in all 24 Build*Segment calls. The plugin never advertises a real receive window. A fast sender could overwhelm the receiver (data delivered to app is unbuffered).

**Fix 1 — header:** Add configurable receive buffer size to `TcpPlugin`:
```cpp
uint32_t rx_buf_size{65535};  // configurable via JSON "rx_buf_size"
```
Add tracked occupied bytes to `TcpConnection`:
```cpp
uint32_t rx_occupied{0};  // bytes received and not yet consumed by app
```

**Fix 2 — TX thread segment builders:** Replace hardcoded `65535` with `conn.peer_advertised_window`? No — for outgoing ACK/FIN/RST/SYN segments, we want to advertise OUR receive window. Add helper:
```cpp
uint16_t AdvertisedWindow(const btcp::TcpConnection& conn, const btcp::TcpPlugin* plugin) {
    uint64_t avail = plugin->rx_buf_size - conn.rx_occupied;
    return (avail > 65535) ? 65535 : static_cast<uint16_t>(avail);
}
```

**Fix 3 — Update rx_occupied:** In `HandleIncoming`, on data delivery to `on_data`, increment `rx_occupied`. But the app "consumes" data asynchronously — we can't know when it's processed. Instead, use a simpler model:
- `rx_occupied` = total bytes received on this connection (since app consumes immediately in our callback model)
- Window = `rx_buf_size - rx_occupied + delivered_bytes`

Actually, the simplest approach: the window represents how much more data we're willing to accept. Since the app processes data synchronously in the callback, we effectively have infinite buffering. Let's just make the window size configurable and advertise it statically. Add a config parameter `"rx_window": 65535` and use it everywhere instead of the hardcoded 65535.

**Fix 4 — Replace hardcoded 65535 globally:** Use `static_cast<uint16_t>(plugin->rx_window)` instead of `65535` in all Build*Segment calls. This affects ~24 occurrences in tcp_plugin.cpp. Use a helper or a local const in each scope that has access to `plugin`.

**Simplest approach:** Add a static helper or inline function that returns the window. Since most segment builders are called with a fixed pattern, I can do a global replace of `65535)` → `static_cast<uint16_t>(plugin->rx_window))` where `plugin` is accessible.

Actually, there's a simpler refactor: define `uint16_t rx_window = static_cast<uint16_t>(plugin->rx_window);` at the top of `HandleIncoming` and use it everywhere. In the TX thread, pass it as a param or capture via `plugin->rx_window`.

**Simplest implementation:** Just replace the 65535 literal globally. Add `uint16_t rx_window{65535};` to `TcpPlugin`. Parse it from JSON `"rx_window"`. Then in all 24 Build*Segment calls, replace `65535` with `conn.rx_window`... wait, the connection doesn't have this. OK, let me think differently.

**Approach:** Pass the window value through. In the TX thread, define `uint16_t adv_win = static_cast<uint16_t>(plugin->rx_window);` and use it. In HandleIncoming, same. The key is just replacing the magic number.

**Test:** C++ unit/patch test — verify config JSON with `"rx_window": 8192` results in segments with window field = 8192. Python integration — capture with tcpdump, verify window field is configurable value, not 65535.

---

#### C3 — Nagle's Algorithm (Medium)

**Background:** Nagle (RFC 896) says: if there's unacked data, a sender should NOT send another small segment. Instead, buffer it until either the pending data is ACKed, or enough data accumulates to fill MSS. This prevents tinygram congestion on the network.

**Fix 1 — header:** Add config flag to `TcpPlugin`:
```cpp
bool nagle_enabled{true};  // configurable via JSON "nagle"
```

**Fix 2 — TX thread (send_buffer drain):** Modify the send_data logic at line 252:
```cpp
if (!conn.send_buffer.empty()) {
    bool can_send = true;
    if (plugin->nagle_enabled && !conn.unacked_segment.empty()) {
        uint32_t avail = static_cast<uint32_t>(conn.send_buffer.size());
        can_send = (avail >= static_cast<uint32_t>(conn.mss));
    }
    if (can_send) {
        // ... existing send logic (chunk min of send_buffer, mss, peer_window) ...
    }
}
```

When Nagle is active and unacked data is pending:
- If send_buffer has ≥ MSS bytes → send full MSS chunk
- If send_buffer has < MSS bytes → wait (don't send yet)

**Config:** `"nagle": true` (default on, matching AUTOSAR `[SRS_Eth_00109]` MUST).

**Test:** Python integration — send small payloads (< MSS) rapidly, verify they're coalesced into fewer segments. Send single large buffer ≥ MSS, verify immediate send.

---

#### C4 — TCP Keepalive (Medium)

**Background:** RFC 1122 §4.2.3.6 mandates keepalive support. If a connection is idle for a configurable period, send a zero-length ACK probe. If no response after retries, close the connection.

**Fix 1 — header:** Add fields to `TcpConnection`:
```cpp
std::chrono::steady_clock::time_point last_activity;
```
Add config to `TcpPlugin`:
```cpp
uint32_t keepalive_idle_ms{7200000};   // 2 hours default, configurable
uint32_t keepalive_interval_ms{75000}; // 75s between probes
uint32_t keepalive_retry_count{9};     // probes before giving up
int       keepalive_probes_sent{0};     // per-connection, not plugin-level
```

Wait, per-connection fields:
```cpp
std::chrono::steady_clock::time_point last_activity;
int keepalive_probes_sent{0};
```

And plugin-level configs.

**Fix 2 — Update last_activity:** On any data sent or received for a connection, update `conn.last_activity = now;`. Touch this in:
- `HandleIncoming` data/fin/syn handling — after connection match
- TX thread data send — after building segment
- `tcp_connect`/`tcp_listen` — on connection creation

**Fix 3 — TX thread keepalive probe:** Add to the TX thread loop (after retransmit check):
```cpp
if (conn.state == btcp::TCP_ESTABLISHED &&
    conn.unacked_segment.empty() &&
    conn.send_buffer.empty()) {
    auto idle = now - conn.last_activity;
    if (conn.keepalive_probes_sent == 0) {
        if (idle >= std::chrono::milliseconds(plugin->keepalive_idle_ms)) {
            // Send zero-length ACK probe
            BuildAndSendSegment(conn, nullptr, 0, TCP_FLAG_ACK);
            conn.keepalive_probes_sent = 1;
            conn.retransmit_at = now + std::chrono::milliseconds(plugin->keepalive_interval_ms);
        }
    } else {
        if (idle >= std::chrono::milliseconds(plugin->keepalive_interval_ms)) {
            if (conn.keepalive_probes_sent >= static_cast<int>(plugin->keepalive_retry_count)) {
                conn.state = btcp::TCP_CLOSED;
                if (conn.on_event)
                    conn.on_event(conn.user_ctx, conn.conn_id, btcp::TCP_EVENT_ERROR);
            } else {
                BuildAndSendSegment(conn, nullptr, 0, TCP_FLAG_ACK);
                conn.keepalive_probes_sent++;
                conn.retransmit_at = now + std::chrono::milliseconds(plugin->keepalive_interval_ms);
            }
        }
    }
}
```

**Fix 4 — Reset probes on activity:** In `HandleIncoming` and TX thread, when data is sent/received, reset:
```cpp
conn.last_activity = now;
conn.keepalive_probes_sent = 0;
```

**Config:** `"keepalive_idle_ms": 7200000`, `"keepalive_interval_ms": 75000`, `"keepalive_retry_count": 9`.

**Test:** Python integration — establish connection, go idle, verify keepalive probe sent after idle timeout. Verify connection closed after retry exhaustion. Or use small config values (e.g., `"keepalive_idle_ms": 1000`) for quick testing.

---

#### C6 — Zero-Window Probing (Medium)

**Background:** When peer advertises window = 0, we must stop sending. But we need a persist timer to probe with 1-byte segments to detect when the window reopens. RFC 1122 §4.2.2.17 AUTOSAR MUST.

**Dependency:** Requires C1 (peer window tracking).

**Fix 1 — header:** Add persist timer fields to `TcpConnection`:
```cpp
bool persist_active{false};
std::chrono::steady_clock::time_point persist_at;
int persist_count{0};
```

**Fix 2 — Enter persist mode:** In TX thread, when `conn.peer_window == 0` and we have data to send:
- Set `conn.persist_active = true;`
- Set `conn.persist_at = now + persist_timeout;` (start with e.g., 5s, exponential backoff to 60s max)
- Skip sending data

**Fix 3 — Persist probing:** In TX thread, when `persist_active` and time expires:
```cpp
if (conn.persist_active && now >= conn.persist_at) {
    // Send 1-byte probe (zero-length data with seq = my_seq, no new data)
    std::vector<uint8_t> probe(1, 0);  // 1 byte of garbage data as probe
    seg = BuildSegment(conn, probe.data(), 1, TCP_FLAG_ACK);
    conn.unacked_segment = seg;
    // Exponential backoff on persist timer
    uint64_t backoff = std::min<uint64_t>(5000ULL * (1ULL << conn.persist_count), 60000);
    conn.persist_at = now + std::chrono::milliseconds(backoff);
    conn.persist_count++;
    need_send = true;
}
```

**Fix 4 — Exit persist mode:** When peer sends a non-zero window update (`window > 0`), clear:
```cpp
conn.persist_active = false;
conn.persist_count = 0;
```

**Test:** Python integration — client advertises window=0. Verify server stops sending and starts probing. Client sends window update > 0, verify server resumes data transmission.

---

### Phase C — Implementation Order

```
C1 (peer window tracking) → C6 (zero-window probing)
C2 (receive window advertisement) — independent
C3 (Nagle's algorithm) — independent
C4 (TCP keepalive) — independent
```

All can be developed in parallel after C1 is done.

### Verification

```bash
cmake --build --preset debug && ctest --preset release -R "tcp" --output-on-failure
# Integration with keepalive test:
sudo python3 demo/tcp_plugin/tcp_listen_server.py veth0 120.120.120.1 9999 &
sudo python3 demo/tcp_plugin/tcp_send_client.py veth1 120.120.120.2 0 120.120.120.1 9999
# Wait for keepalive idle timeout, verify probes in tcpdump
```


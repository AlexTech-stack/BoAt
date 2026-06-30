# Replay HOWTO

Replay recorded CAN and Ethernet traffic from trace files through the BoAt
gateway onto live buses and network interfaces.

## Quick start

```bash
# Prerequisites: gateway must be running with target interfaces configured
cd boat-platform
cmake --preset debug && cmake --build --preset debug

# Start the gateway (virtual Ethernet interfaces need no prefix)
BOAT_ETH_INTERFACES=veth0 \
  build/debug/src/gateway/grpc_gateway/boat_gateway

# Start the gateway (physical Ethernet NICs need raw: prefix)
BOAT_ETH_INTERFACES=raw:eth0 \
  sudo build/debug/src/gateway/grpc_gateway/boat_gateway
```

### CAN replay
```bash
boat trace replay recording.blf --buses vcan0
```

### Ethernet pcap replay
```bash
boat trace replay capture.pcap --buses eth0 \
  --replay-src-ip 192.168.1.1 \
  --replay-dst-ip 192.168.1.100
```

## Trace file formats

| Format | Description | CAN | Ethernet |
|--------|-------------|:---:|:--------:|
| `.asc` | CAN trace (Vector ASC) | ✓ | |
| `.blf` | CAN trace (Vector BLF) | ✓ | |
| `.pcap` | Classic pcap (DLT\_EN10MB) | | ✓ |

``.pcapng`` is **not** supported — only classic pcap with Ethernet link layer
(DLT\_EN10MB = 1).

Ethernet pcap files automatically switch to server-side mode
(see [Replay modes](#replay-modes)).

## Interface configuration

The `--buses` flag maps trace channels to target interfaces.  The order
determines the mapping:

```bash
# CAN: channel 1 → can0, channel 2 → can1, etc.
boat trace replay recording.blf --buses can0,can1,can2

# Ethernet: first bus is the target interface
boat trace replay capture.pcap --buses eth0 \
  --replay-src-ip 192.168.1.1 --replay-dst-ip 192.168.1.100
```

For CAN traces, channel *N* maps to `buses[N-1]` (1-based).  If fewer buses
are listed than channels, the last bus is used as fallback.

For Ethernet traces, only the first bus is relevant — it selects the target
network interface for frame injection.  The gateway must have the interface
registered via the `BOAT_ETH_INTERFACES` environment variable:

```bash
# Virtual interface (veth) — no prefix needed
BOAT_ETH_INTERFACES=veth0 ...

# Physical NIC (raw AF_PACKET) — requires raw: prefix and CAP_NET_RAW / root
BOAT_ETH_INTERFACES=raw:eth0 ...  # or raw:enx28107b9f2017
```

## Filtering

### CAN channel filter

```bash
# Replay only frames from CAN channel 3 (1-based) onto can0
boat trace replay recording.blf --channel 3 --buses can0
```

### CAN ID filter

```bash
# Replay only frames matching specific CAN IDs (hex)
boat trace replay recording.blf --id 0x040,0x0C0 --buses can0

# Combine with channel filter
boat trace replay recording.blf --channel 4 --id 0x040,0x0C0 --buses can0
```

Both standard 11-bit IDs (e.g. `0x040`) and extended 29-bit IDs
(e.g. `0x1BFC829F`) are supported.

### Ethernet IP addresses

Three mechanisms control IP address rewriting — global rewrite, mapping table,
and post-rewrite filter.

#### Global rewrite (`--replay-src-ip` / `--replay-dst-ip`)

Every packet in the pcap has its source and destination IP replaced with the
given addresses.  Both IPv4 and IPv6 are accepted:

```bash
# IPv4 — all packets rewritten to 192.168.1.1 → 192.168.1.100
boat trace replay capture.pcap --buses eth0 \
  --replay-src-ip 192.168.1.1 --replay-dst-ip 192.168.1.100

# IPv6
boat trace replay capture.pcap --buses eth0 \
  --replay-src-ip 2001:db8::1 --replay-dst-ip 2001:db8::100
```

The CLI accepts any notation supported by Python's `ipaddress` module:
``2001:db8::ff00:42:8329``, ``2001:0db8:0000:0000:0000:ff00:0042:8329``,
``::ffff:192.168.1.1``, etc.

#### Per-IP mapping table (`--ip-map`)

Rewrite specific IP addresses to specific targets, leaving other conversations
unmodified.  This lets you replay a pcap that contains multiple IP
conversations and map each one to different real-world IPs:

```bash
# Rewrite 10.10.10.10 → 192.168.0.100 and 10.10.10.11 → 192.168.0.101
# Other IPs in the pcap are left at their original values
boat trace replay capture.pcap --buses eth0 \
  --ip-map 10.10.10.10=192.168.0.100,10.10.10.11=192.168.0.101
```

When both ``--ip-map`` and ``--replay-src-ip``/``--replay-dst-ip`` are set,
entries in the mapping table take precedence for matching IPs; the global
rewrite acts as a fallback for unmapped addresses:

```bash
# 10.10.10.10 → 192.168.0.100 (via map)
# 10.10.10.11 → 10.0.0.1    (via global fallback)
boat trace replay capture.pcap --buses eth0 \
  --ip-map 10.10.10.10=192.168.0.100 \
  --replay-src-ip 10.0.0.1
```

#### Post-rewrite IP filter (`--ip-filter`)

After IP rewriting (via map or global rewrite), filter out packets whose
resulting source or destination IP is not in the given set.  This lets you
select only the conversations you care about from a busy pcap:

```bash
# Rewrite IPs, then keep only packets involving 192.168.0.100
boat trace replay capture.pcap --buses eth0 \
  --ip-map 10.10.10.10=192.168.0.100,10.10.10.11=192.168.0.101 \
  --ip-filter 192.168.0.100
```

The filter is applied **after** all IP rewriting (map + global), so you filter
on the final, rewritten addresses — not the original capture IPs.

#### Direction-aware IP filters (`--src-ip-filter` / `--dst-ip-filter`)

Like ``--ip-filter`` but matching only the **source** or **destination** IP
specifically.  This lets you replay only one direction of a conversation:

```bash
# Replay only ping requests from 10.0.0.1 → 8.8.8.8, drop responses
boat trace replay capture.pcap --buses eth0 \
  --replay-src-ip 10.0.0.1 --replay-dst-ip 8.8.8.8 \
  --src-ip-filter 10.0.0.1 --dst-ip-filter 8.8.8.8
```

The three post-rewrite filters combine as independent **AND** rules:

- ``--ip-filter``: keep if rewritten **src OR dst** is in the set
- ``--src-ip-filter``: keep only if rewritten **src** is in the set
- ``--dst-ip-filter``: keep only if rewritten **dst** is in the set

```bash
# Keep only traffic to 192.168.0.101 (regardless of source)
boat trace replay capture.pcap --buses eth0 \
  --replay-src-ip 192.168.0.100 --replay-dst-ip 192.168.0.101 \
  --dst-ip-filter 192.168.0.101

# Keep only traffic from 192.168.0.100 (regardless of destination)
boat trace replay capture.pcap --buses eth0 \
  --replay-src-ip 192.168.0.100 --replay-dst-ip 192.168.0.101 \
  --src-ip-filter 192.168.0.100
```

#### EtherType filter (`--ethertype`)

Filter by L2 EtherType **before** any IP processing.  Only packets whose
EtherType is in the given set are replayed.  Accepts hex values or names:

```bash
# Only IPv4 (0x0800)
boat trace replay capture.pcap --buses eth0 --ethertype ipv4

# Only IPv6 (0x86DD)
boat trace replay capture.pcap --buses eth0 --ethertype ipv6

# Multiple: IPv4 + ARP
boat trace replay capture.pcap --buses eth0 --ethertype ipv4,arp
```

Recognised EtherType names:

| Name    | Value    |
|---------|----------|
| ``ip`` / ``ipv4`` | ``0x0800`` |
| ``arp`` | ``0x0806`` |
| ``ipv6`` | ``0x86DD`` |
| ``vlan`` | ``0x8100`` |
| ``boat`` | ``0x88B5`` |

#### Protocol filter (`--protocol`)

Filter by L4 protocol number **before** IP rewriting.  Applied by numeric
value regardless of IP version — ``--protocol udp`` matches both IPv4+UDP
and IPv6+UDP.  Accepts decimal values or names:

```bash
# Only UDP
boat trace replay capture.pcap --buses eth0 --protocol udp

# UDP + ICMP (applies to both IPv4 ICMP and IPv6 ICMPv6)
boat trace replay capture.pcap --buses eth0 --protocol udp,icmp

# Only ICMPv6
boat trace replay capture.pcap --buses eth0 --protocol icmpv6
```

Recognised protocol names:

| Name       | Value |
|------------|-------|
| ``icmp`` / ``icmpv4`` | ``1`` |
| ``igmp`` | ``2`` |
| ``tcp`` | ``6`` |
| ``udp`` | ``17`` |
| ``ipv6`` (encap) | ``41`` |
| ``icmpv6`` | ``58`` |
| ``ospf`` | ``89`` |
| ``sctp`` | ``132`` |

#### Processing order

For each pcap packet:

```
 1. Parse EtherType from the pcap L2 header
 2. EtherType filter:  skip if ethertype not in --ethertype
 3. Parse IP header, extract protocol/next-header number
 4. Protocol filter:   skip if protocol not in --protocol
 5. Apply IP map:      src = ip_map.get(orig_src, replay_src_ip or orig_src)
                        dst = ip_map.get(orig_dst, replay_dst_ip or orig_dst)
 6. IP filter (OR):    skip if neither rewritten src nor dst is in --ip-filter
 7. Src IP filter:     skip if rewritten src not in --src-ip-filter
 8. Dst IP filter:     skip if rewritten dst not in --dst-ip-filter
 9. Rebuild IP header with the final src/dst addresses
10. Recalculate IP and transport checksums
```

### Ethernet MAC addresses

Source MAC is auto-detected from the target interface.  Destination MAC
defaults to broadcast.  Both can be overridden:

```bash
boat trace replay capture.pcap --buses eth0 \
  --replay-src-ip 192.168.1.1 --replay-dst-ip 192.168.1.100 \
  --replay-src-mac 02:de:ad:be:ef:01 \
  --replay-dst-mac 02:de:ad:be:ef:02
```

## Timing

### Speed control

```bash
# Real-time (default)
boat trace replay recording.blf --speed 1.0 --buses vcan0

# Twice as fast
boat trace replay recording.blf --speed 2.0 --buses vcan0

# Half speed
boat trace replay recording.blf --speed 0.5 --buses vcan0

# Maximum speed (as fast as possible)
boat trace replay recording.blf --speed 0 --buses vcan0
```

| `--speed` | Behavior |
|-----------|----------|
| `0` | Max speed — no delay, frames fire at CPU-limited rate |
| `0 < x < 1` | Slower than real-time (e.g. `0.1` = 10x slower) |
| `1.0` | Real-time (default) |
| `2.0` | 2x speed |
| `10` | 10x speed |
| `10000+` | Effectively max speed, indistinguishable from `0` |

In direct mode each frame incurs a gRPC round-trip (~5-8ms) even at max speed.
Use [server-side mode](#server-side-mode) to eliminate per-frame gRPC overhead.

### Loop mode

```bash
# Replay the file in an infinite loop until Ctrl+C
boat trace replay recording.blf --loop --buses vcan0
```

### Tick configuration (server-side)

The gateway's tick interval controls timing resolution in server-side mode.
Configurable via environment variables:

```
BOAT_NODE_TICK_US=100    # 100μs ticks
BOAT_NODE_TICK_MS=1      # 1ms ticks (default)
BOAT_NODE_TICK_US=10000  # 10ms ticks
```

- `BOAT_NODE_TICK_US` takes precedence when both are set
- All intervals use `TimerfdTickTimer` (Linux `timerfd` with
  `CLOCK_MONOTONIC`, absolute-time scheduling, no drift accumulation)
- Minimum practical value is ~100μs (below that, processing overhead per tick
  exceeds the tick interval and deadlines fire immediately)

The tick timer uses **absolute-time scheduling** — each frame is pinned to an
absolute wall-clock deadline (`t_base + tick * tick_duration / multiplier`).
Deadlines in the past fire immediately, so the replay can never fall behind by
more than one tick; there is no accumulated drift even across long traces.

## Replay modes

Two modes are available:

| Mode | Description | CLI flag | Suitable for |
|------|-------------|----------|-------------|
| **Direct** | Reads the trace locally and sends each frame via gRPC | (default) | CAN only, ad-hoc replays |
| **Server-side** | Converts trace to binary, uploads to gateway, plays back on the gateway's tick timer | ``--server-side`` | CAN + Ethernet, high-speed replay |

### Direct mode

```bash
# Replay CAN trace locally via gRPC (default)
boat trace replay recording.blf --buses vcan0
```

Each frame is sent individually via `CanService.SendCanFrame`.  Simple but
each frame incurs a gRPC round-trip (~5-8ms).  Ethernet pcap replay is
**not** available in direct mode.

### Server-side mode

```bash
# CAN: upload + replay in one command
boat trace replay recording.blf --server-side --buses vcan0

# Ethernet: auto-selected (--server-side is implicit for .pcap)
boat trace replay capture.pcap --buses eth0 \
  --replay-src-ip 192.168.1.1 --replay-dst-ip 192.168.1.100
```

The trace is converted to the gateway's internal binary format, uploaded in
a single `ImportTraceData` request, and played back using the gateway's own
tick timer.  There is **no per-frame gRPC overhead** — timing is driven
entirely by the gateway's scheduler.

Server-side mode is **recommended for most use cases**, especially when:
- Replaying Ethernet pcap files (required)
- Replaying at high speed (``--speed 0`` or large multipliers)
- Replaying long traces where timing accuracy matters
- The gateway and client are on different hosts

For Ethernet pcap files, server-side mode is **auto-forced** — the CLI
enables it automatically and ignores the ``--server-side`` flag.

## Verbose output

```bash
# Print every frame as it is sent
boat trace replay recording.blf --verbose --buses vcan0
```

In server-side mode the output shows gateway tick numbers and payload
snippets.  In direct mode it shows CAN IDs, timestamps, and hex data.

## Replay lifecycle

```bash
# Pause / resume / stop an active server-side replay
boat replay pause
boat replay resume
boat replay stop
```

These commands control a running server-side replay.  Direct-mode replays
run in the client process and are interrupted with `Ctrl+C`.

## Replay from event store

Events recorded in the SQLite event store can be replayed:

```bash
# Replay all events from a simulation
boat replay from-events --sim-id <simulation_id>

# With signal and tick range filter
boat replay from-events --sim-id <id> --signal-id speed --tick-min 100 --tick-max 500
```

## Managing uploaded traces

After server-side upload, the trace is stored in the gateway's trace store:

```bash
# List stored traces
boat trace list

# Replay a stored trace via the replay service
boat replay start --trace <trace_id> --speed accelerated --multiplier 5.0
boat replay stream
boat replay stop
```

## Protocol support

### CAN

Standard CAN frames are replayed as-is.  CAN IDs, DLC, and data bytes are
preserved from the trace file.

### CAN FD

CAN FD frames are handled automatically.  The SocketCan driver uses
`struct canfd_frame` internally and correctly preserves FD flags (`FDF`,
`BRS`).  The gateway's `ListBuses` RPC reports FD capability per interface.

```bash
# Configure a physical CAN FD interface
sudo ip link set can0 up type can bitrate 500000 dbitrate 2000000 fd on

# Verify FD support
boat can list-buses
```

### Extended (29-bit) CAN IDs

The SocketCan driver automatically sets the `CAN_EFF_FLAG` bit when a CAN ID
exceeds the 11-bit range (`> 0x7FF`).  This ensures extended frames appear
correctly on the bus with their full 29-bit identifier.

### Ethernet

Replayed IP packets have their L2/L3/L4 headers rewritten to match the
target network:

| Layer | Field | Behavior |
|-------|-------|----------|
| **L2** | Source MAC | Auto-detected from target interface; overridable via ``--replay-src-mac`` |
| **L2** | Destination MAC | Defaults to broadcast; overridable via ``--replay-dst-mac`` |
| **L3** | Source / Dest IP | Rewritten via ``--replay-src-ip``/``--replay-dst-ip`` (global) or ``--ip-map`` (per-address table) |
| **L3** | TTL / Hop Limit | Preserved from the original packet |
| **L4** | UDP ports | Preserved from the original |
| **L4** | ICMP type/code | Preserved from the original |
| **L4** | Checksums | Recalculated after rewriting (IP, UDP, ICMP) |

Supported protocol combinations:

| Protocol | Status |
|----------|--------|
| IPv4 + UDP | Full support |
| IPv4 + ICMP | Full support |
| IPv4 + TCP | **Phase 2** — requires TCP state machine (connection tracking, window-adaptive ACK/SEQ) |
| IPv6 + UDP | Full support (mandatory checksum with IPv6 pseudo-header) |
| IPv6 + ICMPv6 | Full support (mandatory checksum with IPv6 pseudo-header) |

#### Ethernet replay pipeline

1. Python SDK reads the pcap file, strips L2/L3/L4 headers, preserving
   protocol type, ports, TTL/hop-limit, and application payload.
2. Clean L3/L4 headers are built with user-specified addresses.
3. IP and transport checksums are recalculated.
4. The reconstructed IP packet is stored in the gateway's binary trace format.
5. The C++ forwarder reads the trace records, wraps the IP packet in an
   Ethernet L2 frame (using the configured or auto-detected MAC addresses),
   and sends it on the specified interface via ``SendFrame(iface, ...)``.

## Real-world examples

```bash
# CAN: replay channel 4, filter to IDs 0x040 and 0x0C0, at 0.2x speed
boat trace replay tracefile.blf --channel 4 --buses can0 --id 0x040,0x0C0 --speed 0.2
candump can0

# Ethernet: replay a pcap with a global IP override
boat trace replay capture.pcap --buses eth0 \
  --replay-src-ip 192.168.10.50 --replay-dst-ip 10.0.0.1
tcpdump -i eth0

# Ethernet: only replay IPv6 ICMPv6, map specific addresses, filter to one pair
boat trace replay capture.pcap --buses eth0 \
  --ethertype ipv6 --protocol icmpv6 \
  --ip-map 2001:db8::1=fe80::100,2001:db8::2=fe80::200 \
  --ip-filter fe80::100,fe80::200
tcpdump -i eth0
```

## Programmatic usage (Python SDK)

```python
from boat.trace_replay import TraceReplayer

# CAN replay
replayer = TraceReplayer(
    gateway="localhost:50051",
    buses=["vcan0"],
    speed=1.0,
    channel_filter=4,
    id_filter={0x040, 0x0C0},
)

# Direct mode (CAN only)
replayer.replay("tracefile.blf")

# Server-side mode (CAN + Ethernet)
replayer.replay("tracefile.blf", server_side=True)

# Ethernet pcap replay (always server-side)
eth_replayer = TraceReplayer(
    gateway="localhost:50051",
    buses=["eth0"],
    replay_src_ip="192.168.1.1",
    replay_dst_ip="192.168.1.100",
)
eth_replayer.replay("capture.pcap")

# Ethernet with filters and IP mapping
eth_replayer = TraceReplayer(
    gateway="localhost:50051",
    buses=["eth0"],
    ethertype_filter={0x86DD},
    protocol_filter={58},       # ICMPv6
    ip_map={"2001:db8::1": "fe80::100",
            "2001:db8::2": "fe80::200"},
    ip_filter={"fe80::100", "fe80::200"},
)
eth_replayer.replay("capture.pcap")
```

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Frames not appearing on the bus | Check that the target interface is up. For CAN FD: `ip link show can0`. For Ethernet: `ip link show eth0`. |
| Extended CAN IDs appear truncated (e.g. `29F` instead of `1BFC829F`) | SocketCan driver missing `CAN_EFF_FLAG`. Build the latest gateway. |
| gRPC `UNAVAILABLE` | Gateway not running or wrong host/port. Verify: `boat can list-buses`. |
| CAN server-side replay imports but no frames on the bus | The trace file timestamps may be absolute (epoch-based). The Python SDK converts to relative ticks internally since build `43824e6`. Upgrade the SDK: `pip install -e ./sdk/python`. |
| Server-side replay seems to hang (no console output after "Replaying...") | The Python client blocks on `StreamReplay` waiting for events from the gateway's EventBus. Frames are still delivered to the bus — verify with `candump` / `tcpdump`. Use `Ctrl+C` to interrupt. |
| No frames replayed | Check the trace file format. Ensure channel/ID filters are correct. For pcap: only classic pcap (DLT\_EN10MB) is supported, not pcapng. |
| Ethernet pcap replay: no frames on the wire | Verify the target interface is correct (`--buses eth0`) and that ``--replay-src-ip`` / ``--replay-dst-ip`` are set. The interface must be up and registered via ``BOAT_ETH_INTERFACES`` — use ``raw:`` prefix for physical NICs (e.g. ``BOAT_ETH_INTERFACES=raw:eth0``). |

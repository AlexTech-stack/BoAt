# Replay HOWTO

Replay recorded CAN traffic from `.blf` / `.asc` trace files through the BoAt
gateway onto live CAN buses.

## Overview

Two replay modes are available:

| Mode | Description | CLI flag |
|------|-------------|----------|
| **Direct** | Reads a trace file locally and sends each CAN frame one-by-one via `CanService.SendCanFrame` gRPC | (default) |
| **Server-side** | Converts the trace to the gateway's internal binary format, uploads via `ReplayService.ImportTraceData`, then plays back server-side using the gateway's tick timer | `--server-side` |

Server-side mode is **recommended for most use cases** — it avoids per-frame gRPC
overhead and uses the gateway's high-precision tick timer with drift-free
absolute-time scheduling (see [Tick configuration](#tick-configuration) below).
Direct mode is simpler for quick ad-hoc replays but each frame incurs a gRPC
round-trip (~5-8ms) even at max speed.

## Quick start

```bash
# Prerequisites: gateway must be running with CAN interfaces configured
# Build the gateway
cd boat-platform
cmake --preset debug && cmake --build --preset debug

# Start the gateway with a physical or virtual CAN interface
BOAT_CAN_INTERFACES=can0 build/debug/src/gateway/grpc_gateway/boat_gateway
```

### Basic replay

```bash
# Replay a trace file at real-time speed
boat trace replay recording.blf --buses can0
```

All frames from all channels in the trace are replayed. By default, channel N
maps to `buses[N-1]` (1-based). If fewer buses are listed than channels, the
last bus is used as fallback.

### Channel filter

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

### Speed control

```bash
# Real-time (default)
boat trace replay recording.blf --speed 1.0 --buses can0

# Twice as fast
boat trace replay recording.blf --speed 2.0 --buses can0

# Half speed
boat trace replay recording.blf --speed 0.5 --buses can0

# Maximum speed (as fast as possible; in direct mode per-frame gRPC overhead still applies)
boat trace replay recording.blf --speed 0 --buses can0
```

| `--speed` | Behavior |
|-----------|----------|
| `0` | Max speed — no delay, frames fire at CPU-limited rate |
| `0 < x < 1` | Slower than real-time (e.g. `0.1` = 10x slower) |
| `1.0` | Real-time (default) |
| `2.0` | 2x speed |
| `10` | 10x speed |
| `10000+` | Effectively max speed, indistinguishable from `0` |

### Loop

```bash
# Replay the file in an infinite loop
boat trace replay recording.blf --loop --buses can0
```

### Verbose output

```bash
# Print every frame as it is sent
boat trace replay recording.blf --verbose --buses can0
```

## Server-side replay

The server-side mode uploads the trace to the gateway and replays it through the
`ReplayService`, which runs on the gateway's internal tick timer. Because the
entire trace is uploaded in a single request and the gateway drives the timing
internally, there is **no per-frame gRPC overhead** — the gap between frames is
determined solely by the tick timer and the configured speed.

```bash
# Full pipeline: upload + replay in one command
boat trace replay recording.blf --server-side --buses can0
```

Speed is controlled uniformly via `--speed` (see [Speed control](#speed-control)
above). In server-side mode there is no per-frame gRPC overhead, so true max
speed is achievable with `--speed 0` or large multipliers.

### Tick configuration

The tick interval is configurable via environment variables, using the same
pattern as the gateway node tick:

```
BOAT_NODE_TICK_US=100    # 100μs ticks (high-precision timerfd, sub-ms)
BOAT_NODE_TICK_MS=1      # 1ms ticks (default, uses SleepTickTimer)
BOAT_NODE_TICK_US=999    # 999μs ticks, uses timerfd (any value < 1ms)
```

- `BOAT_NODE_TICK_US` takes precedence when both are set
- Values < 1ms use the `TimerfdTickTimer` backend (Linux `timerfd` with
  `CLOCK_MONOTONIC`, absolute-time scheduling, no drift accumulation)
- Values ≥ 1ms use `SleepTickTimer` (`std::this_thread::sleep_until`)
- Minimum practical value is ~100μs (below that, processing overhead per tick
  exceeds the tick interval and deadlines fire immediately)

The tick timer uses **absolute-time scheduling** — each frame is pinned to an
absolute wall-clock deadline (`t_base + tick * tick_duration / multiplier`).
Deadlines in the past fire immediately, so the replay can never fall behind by
more than one tick; there is no accumulated drift even across long traces.

### Managing uploaded traces

After upload, the trace is stored in the gateway's trace store and can be
managed independently:

```bash
# List stored traces
boat trace list

# Replay a stored trace via the replay service
boat replay start --trace <trace_id> --speed accelerated --multiplier 5.0
boat replay stream
boat replay stop
```

## Replay lifecycle commands

```bash
# Pause / resume / stop an active replay
boat replay pause
boat replay resume
boat replay stop
```

## Replay from event store

Events recorded in the SQLite event store can be replayed:

```bash
# Replay all events from a simulation
boat replay from-events --sim-id <simulation_id>

# With signal and tick range filter
boat replay from-events --sim-id <id> --signal-id speed --tick-min 100 --tick-max 500
```

## Real-world example

```bash
# Replay CAN channel 4, filter to IDs 0x040 and 0x0C0, at 0.2x speed
boat trace replay tracefile.blf --channel 4 --buses can0 --id 0x040,0x0C0 --speed 0.2

# Monitor on the bus with candump
candump can0
```

## CAN FD support

CAN FD frames are handled automatically. The SocketCan driver uses `struct
canfd_frame` internally and correctly preserves FD flags (`FDF`, `BRS`). The
gateway's `ListBuses` RPC reports FD capability per interface.

```bash
# Configure a physical CAN FD interface
sudo ip link set can0 up type can bitrate 500000 dbitrate 2000000 fd on

# Verify FD support
boat can list-buses
```

## Extended (29-bit) CAN IDs

The SocketCan driver automatically sets the `CAN_EFF_FLAG` bit when a CAN ID
exceeds the 11-bit range (`> 0x7FF`). This ensures extended frames appear
correctly on the bus with their full 29-bit identifier.

## Programmatic usage (Python SDK)

```python
from boat.trace_replay import TraceReplayer

replayer = TraceReplayer(
    gateway="localhost:50051",
    buses=["can0"],
    speed=1.0,
    channel_filter=4,
    id_filter={0x040, 0x0C0},
)

# Direct mode
replayer.replay("tracefile.blf")

# Server-side mode
replayer.replay_server_side("tracefile.blf")
```

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Frames not appearing on the bus | Check that the CAN interface is up: `ip link show can0`. For FD frames, interface must be configured with `fd on` and `dbitrate`. |
| Extended IDs appear truncated (e.g. `29F` instead of `1BFC829F`) | SocketCan driver missing `CAN_EFF_FLAG`. Build the latest gateway. |
| gRPC `UNAVAILABLE` | Gateway not running or wrong host/port. Verify: `boat can list-buses`. |
| Server-side replay imports but no frames appear on the bus | The trace file timestamps may be absolute (epoch-based). The Python SDK converts timestamps to relative ticks internally since build `43824e6`. Upgrade the SDK: `pip install -e ./sdk/python`. |
| Server-side replay seems to hang (no console output after "Replaying...") | The Python client blocks on `StreamReplay` waiting for events from the gateway's EventBus. CAN frames are still sent to the bus — verify with `candump`. The `EventBus` requires a running tick scheduler to dispatch events, which is not active outside an active simulation. Use `Ctrl+C` to interrupt; the frames will have been delivered. |
| No frames replayed | Check the trace file format (`.blf`/`.asc`). Ensure the channel filter is correct: `python3 -c "import can; print([getattr(m,'channel') for m in can.BLFReader('file.blf')][:5])"`. |

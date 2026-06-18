# AGENTS.md — BoAt Platform

## Repository structure

- **`boat-platform/`** — Main platform (C++20, CMake+Ninja, gRPC)
  - `src/core/` — Simulation engine (scheduler, signal router, determinism, plugin mgr)
  - `src/gateway/grpc_gateway/` — gRPC server → `boat_gateway` binary, listens on `0.0.0.0:50051`
  - `src/hil/` — HIL bridge (CAN/Ethernet drivers, PDU router, bus registries)
    - `can/` — `SocketCanDriver` (raw AF_CAN/SOCK_RAW), `PhysicalCanDriver` (sysfs-probing physical HW)
    - `virtual/` — `VirtualCanDriver` (SocketCan wrapper for vcan*)
    - `pdu/com/` — COM signal library (bit pack/unpack, E2E CRC, Intel/Motorola)
    - `pdu/transmission_engine.h/.cpp` — Cyclic/OnChange/Mixed transmission scheduler
    - `pdu/tick_timer.h/.cpp` — Dual-backend tick timer (sleep_for / timerfd)
  - `src/store/` — SQLite event/trace/config stores
  - `src/ipc/` — Inter-process comm (gRPC, iceoryx2 SHM, UDS)
  - `src/plugins/` — Built-in plugins
    - `vehicle_dynamics/` — Simulated vehicle (speed/RPM, CAN + ETH output)
    - `sensor_model/` — Sensor simulation (LIDAR/CAMERA/RADAR)
    - `network_sim/` — Network bus load simulation
    - `can_responder/` — CAN frame responder (0x123 → 0x234)
    - `can_tp/` — ISO 15765-2 CAN Transport Protocol (segmentation/reassembly)
    - `someip/` — SOME/IP middleware (service discovery stub, request/response)
  - `src/replay/` — Replay engine
  - `proto/boat/v1/` — 15 protobuf definitions defining all gRPC services
  - `sdk/python/` — `boat-py` package (BoAtClient gRPC client, CAN/ETH nodes, trace tools)
  - `sdk/cpp/include/boat/` — C++ SDK headers
    - `plugin.h` — Plugin ABI v7 (BOAT_CAN_FLAG_SELF_SENT, per-interface CAN publish)
    - `can_tp.h` — Standalone CanTp C API (can_tp_send, can_tp_configure)
    - `someip.h` — SOME/IP protocol constants
  - `cli/` — `boat-cli` package (Typer CLI: `boat sim|scenario|can|eth|pdu|can-tp|...`)
  - `config/` — PDU database JSON files
  - `demo/` — Demo node scripts (not web UI)
- **`ui/`** — 7 standalone FastAPI/uvicorn web services requiring a running gateway (launcher:8086, dashboard:8080, commander:8082, recorder:8083, control_panel, debug, system_dashboard)
- **`tools/`** — 2 standalone tools (pdu_editor:8087, trace_analyzer:8088)
- **`traces/`** — Trace output directory (gitignored)

## Build & run

```bash
# Build C++ (presets: debug, release, asan, tsan, coverage)
cmake --preset debug && cmake --build --preset debug

# Gateway binary
build/debug/src/gateway/grpc_gateway/boat_gateway

# Prerequisites (Ubuntu 22.04 ships CMake 3.22 — need 3.24+)
# Rust toolchain required (transitive dep of iceoryx2 for SHM IPC)
# libacl1-dev for sys/acl.h (CMake auto-downloads if missing)
sudo apt install cmake ninja-build g++ libacl1-dev
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Run gateway with virtual CAN
sudo modprobe vcan
sudo ip link add vcan0 type vcan && sudo ip link set vcan0 up
BOAT_CAN_INTERFACES=vcan0 ./build/debug/src/gateway/grpc_gateway/boat_gateway

# Run gateway with physical CAN (e.g. PEAK PCAN-USB Pro FD)
sudo ip link set can0 up type can bitrate 500000
sudo ip link set can1 up type can bitrate 500000
BOAT_CAN_INTERFACES=can0,can1,vcan0 ./build/debug/src/gateway/grpc_gateway/boat_gateway

# Enable CAN FD (optional, requires FD-capable hardware)
sudo ip link set can0 up type can bitrate 500000 dbitrate 2000000 fd on
```

## CAN Hardware Integration

The gateway distinguishes between virtual (`vcan*`) and physical CAN interfaces at startup:
- `vcan*` → `VirtualCanDriver` (wraps SocketCAN)
- all others → `PhysicalCanDriver` (reads sysfs for driver metadata, e.g. `peak_usb`)

The `ListBuses` gRPC response now returns per-interface metadata (driver name, state, FD support, bitrate).

### CLI CAN commands

```bash
# List registered interfaces with metadata (requires gateway)
boat can list-buses
boat --json can list-buses

# Detect available CAN hardware on the host (no gateway required)
boat can detect
boat --json can detect
```

The `boat can detect` command scans `/sys/class/net/` for CAN interfaces and identifies:
- Physical hardware (PEAK PCAN-USB Pro FD via USB ID `0c72:0011`, other USB devices)
- Virtual CAN interfaces
- Driver name, FD capability, link state

## Test

```bash
# C++ (Catch2)
ctest --preset release --output-on-failure
ctest --test-dir build/debug -R TestName --timeout 30 --output-on-failure
ctest --test-dir build/debug -N  # list tests

# Python SDK + CLI
pip install -e ./sdk/python[dev] && pip install -e ./cli
pytest sdk/python/tests cli/tests -v
```

Test binary naming: `boat_unit_*` (unit), `boat_integration_*`, `boat_hil_*`, `boat_determinism_seed`.

## Python SDK / CLI

```bash
# Editable installs
pip install -e ./boat-platform/sdk/python
pip install -e ./boat-platform/cli

# Regenerate gRPC stubs after proto changes
bash boat-platform/sdk/python/boat/stubs/generate_stubs.sh

# CLI
boat --help
boat sim init|start|pause|step|stop
boat can send|listen
boat scenario create|validate|get|list

# SDK (programmatic)
from boat.client import BoAtClient
client = BoAtClient("localhost:50051")
```

## UI services

```bash
bash start_ui.sh   # launches all 10 services in background
bash stop_ui.sh    # kills them all
```

Each service is a standalone `python3 ui/<name>.py` FastAPI/uvicorn app with embedded HTML. SDK path is resolved via `sys.path.insert(0, ...)` relative to the script location.

## Quirks & gotchas

- Gateway binary path: `build/{preset}/src/gateway/grpc_gateway/boat_gateway`
- `boat` CLI entry point (boat_cli/main.py): Typer app with subcommands. Uses `BoAtClient(address)` from `boat-py`.
- `python3 -m boat` dispatches: subcommands `can|pdu|eth|db` → `boat/cmd.py` (one-shot), anything else → `boat/cli.py` (interactive REPL).
- Proto stubs in `sdk/python/boat/stubs/boat/v1/` must be regenerated when proto files change (`generate_stubs.sh`).
- iceoryx2 requires `cargo` (Rust) at build time only; the resulting shared-memory IPC is used at runtime for large payloads (>4KB).
- HIL tests need `BOAT_HIL_ENABLED=1` and a real or virtual CAN interface (`vcan0`).
- Determinism test runs simulation twice with same seed and expects bit-exact output.
- Coverage report: `gcovr --root . --exclude build/ --xml coverage.xml`.
- Release packaging: `cpack -G "TGZ;DEB;RPM"`.
- Docker images pushed to `ghcr.io/boat-platform/boat-platform:*`.

## PDU Features

### I-PDU Groups

Groups enable/disable sets of PDUs at runtime. PDUs in a disabled group are silently dropped.

```bash
# Create a group with two PDUs, disabled
boat pdu group --id 1 --name "Safety" --pdu 0x100 --pdu 0x200 --disabled

# Create a group with two PDUs, enabled (--enabled/--disabled toggle)
boat pdu group --id 2 --name "Chassis" --pdu 0x300 --pdu 0x400 --enabled

# Enable/disable at runtime
boat pdu enable-group --id 1
boat pdu disable-group --id 1

# List groups
boat pdu list-groups

# Programmatic (Python)
node = PduNode()
node.configure_group(group_id=1, name="Safety", pdu_ids=[0x100, 0x200], enabled=False)
node.enable_group(1)
node.disable_group(1)
groups = node.list_groups()
```

### Transmission Schedules

Routes can specify automatic sending behavior (Cyclic, OnChange, Mixed with n-times fast repetitions).

```bash
# Cyclic: send every 100ms
boat pdu route --id 0x100 --transport can --iface vcan0 --send-type cyclic --cycle-ms 100

# OnChange: send only when payload changes, with 3 fast repetitions at 10ms intervals
boat pdu route --id 0x200 --transport can --iface vcan0 --send-type onchange --fast-ms 10 --reps 3

# Mixed: cyclic background at 200ms + OnChange triggers with 2 fast reps at 20ms
boat pdu route --id 0x300 --transport can --iface vcan0 --send-type mixed --cycle-ms 200 --fast-ms 20 --reps 2

# Additional optional parameters for routes:
#   --can-id N         CAN frame ID override (default: same as pdu_id)
#   --ethertype 0x0800 EtherType (default 0x88B5 sim-only; set 0x0800 for IPv4)
#   --src-ip A.B.C.D   Source IP (enables IP/UDP/IpduM transport)
#   --dst-ip A.B.C.D   Destination IP (required for IP/UDP transport)
#   --src-port N       UDP source port
#   --dst-port N       UDP destination port
#   --ttl N            IPv4 TTL / IPv6 Hop Limit (default 64)
#   --vlan N           802.1Q VLAN ID

# The gateway's OnTick() drives the transmission engine (10ms default tick interval,
# set via BOAT_NODE_TICK_MS).  The tick interval is the minimum cycle time — e.g.
# a 10ms tick supports cycle_ms >= 10ms.  Setting BOAT_NODE_TICK_MS=1 gives 1ms
# precision.  For sub-ms precision use BOAT_NODE_TICK_US (e.g. BOAT_NODE_TICK_US=100
# for 100μs ticks, uses high-precision timerfd backend).  Lower intervals increase
# CPU load — 100μs tick on a typical x86 adds ~1-2% CPU per 10 scheduled PDUs.
#
# Timer backends (auto-selected by TickTimer factory):
#   SleepTickTimer     — std::this_thread::sleep_for, used for intervals >= 1ms
#   TimerfdTickTimer   — Linux timerfd (CLOCK_MONOTONIC), used for intervals < 1ms,
#                        absolute-time scheduling, no drift accumulation

# ── STOP sending ──────────────────────────────────────────────────────────

# Option A: Reconfigure with --send-type none to keep the route but stop auto-sends
boat pdu route --id 0x100 --transport can --iface vcan0 --send-type none

# Option B: Remove the route and schedule entirely
boat pdu remove-route --id 0x100

# Option C: Disable the PDU's group (keeps config, silences the PDU)
boat pdu group --id 1 --pdu 0x100
boat pdu disable-group --id 1
```

### COM Signal Library (C++)

Bit-level signal packing with Intel/Motorola support, physical-to-raw conversion, AUTOSAR E2E CRC.

```cpp
#include "pdu/com/com_signal.h"
using namespace boat::hil::com;

MessageDef msg;
msg.length_bytes = 8;
SignalDef sig;
sig.name = "Speed";
sig.bit_length = 16;
sig.start_pos = 0;
sig.is_motorola = false;  // Intel
sig.factor = 0.5;
sig.offset = 0.0;

auto packed = PackSignals(msg, {{"Speed", 100.0}});
// unpacked["Speed"] == 100.0
auto unpacked = UnpackSignals(msg, packed.data(), packed.size());

// E2E CRC
uint8_t crc8 = E2eCrc8(data, len);
uint16_t crc16 = E2eCrc16(data, len);
uint32_t crc32 = E2eCrc32(data, len);
```

### CanTp — CAN Transport Protocol (Plugin)

ISO 15765-2 segmentation/reassembly for PDUs larger than 8 bytes. Operates as a `BOAT_NODE_PLUGINS` node plugin with its own C API.

Each connection represents a session between `source_addr` (this node) and
`target_addr` (peer node).  Both IDs must be configured so that the plugin
can correctly associate frames and distinguish ISO-TP traffic from regular
signal frames on the bus.

```bash
# Build plugin
cmake --build --preset debug

# Run gateway with CanTp plugin
BOAT_NODE_PLUGINS=./build/debug/src/plugins/can_tp/can_tp.so \
  BOAT_CAN_INTERFACES=vcan0 \
  ./build/debug/src/gateway/grpc_gateway/boat_gateway

# Configure a session (dual-ID, tester→ECU)
boat can-tp configure --nsdu-id diag --source-addr 0x7E0 --target-addr 0x7E8 --bs 0 --stmin 0

# Send large PDU via CanTp CLI (--dlc 8 for classic CAN, --dlc 64 for CAN-FD)
boat can-tp send --nsdu-id diag --source-addr 0x7E0 --target-addr 0x7E8 --dlc 8 --data 0123456789ABCDEF...
```

Programmatic (C API):
```c
#include <boat/can_tp.h>

CanTpConfig cfg;
cfg.nsdu_id = 0x7E0;
cfg.source_addr = 0x7E0;
cfg.target_addr = 0x7E8;
cfg.rx_buffer_size = 4095;
cfg.block_size = 0;      // unlimited CF per FC
cfg.st_min = 0;          // no min separation
cfg.can_dlc = 8;
cfg.extended_addressing = false;
can_tp_configure(plugin_ctx, &cfg);

uint8_t data[] = {0x01, 0x02, ..., 0xFF};  // 255 bytes
can_tp_send(plugin_ctx, 0x7E0, data, 255);  // segmented into SF or FF+CF
```

### SOME/IP Plugin

Service-oriented middleware over Ethernet UDP. Listens on configured ports, responds to SOME/IP requests, supports Service Discovery.

```bash
BOAT_NODE_PLUGINS=./build/debug/src/plugins/someip/someip.so \
  BOAT_ETH_INTERFACES=veth0 \
  ./build/debug/src/gateway/grpc_gateway/boat_gateway
```

Config: `{"sd_port": 30490}`. Registers offered services; responds to REQUEST messages with RESPONSE echoes.

## AUTOSAR specification reference

AUTOSAR specs are available locally via `spec/` (symlinked, gitignored — populate on each machine):

```bash
# Content expected under spec/:
spec/
├── GUIDE.md          # Search workflow
├── latest/           # 266 PDFs
├── text/             # Flat UTF-8 text (97 MB)
└── search.db         # SQLite FTS5 index (115 MB)
```

Search workflow (see `spec/GUIDE.md` for details):
```bash
# Find which document covers your topic
python3 -c "
import sqlite3
conn = sqlite3.connect('spec/search.db')
cur = conn.execute(
    \"SELECT rank, filename FROM docs WHERE docs MATCH ? ORDER BY rank LIMIT 5\",
    ('\"remote frame\" CAN',)
)
for rank, fname in cur:
    print(f'  [{rank:.1f}] {fname}')
"

# Read the relevant section
grep -n -B 2 -A 10 -i "remote frame" spec/text/AUTOSAR_SWS_CAN_Driver.txt
```

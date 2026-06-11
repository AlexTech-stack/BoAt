# AGENTS.md — BoAt Platform

## Repository structure

- **`boat-platform/`** — Main platform (C++20, CMake+Ninja, gRPC)
  - `src/core/` — Simulation engine (scheduler, signal router, determinism, plugin mgr)
  - `src/gateway/grpc_gateway/` — gRPC server → `boat_gateway` binary, listens on `0.0.0.0:50051`
  - `src/hil/` — HIL bridge (CAN/Ethernet drivers, PDU router, bus registries)
  - `src/store/` — SQLite event/trace/config stores
  - `src/ipc/` — Inter-process comm (gRPC, iceoryx2 SHM, UDS)
  - `src/plugins/` — Built-in plugins (vehicle_dynamics, sensor_model, network_sim, can_responder)
  - `src/replay/` — Replay engine
  - `proto/boat/v1/` — 15 protobuf definitions defining all gRPC services
  - `sdk/python/` — `boat-py` package (BoAtClient gRPC client, CAN/ETH nodes, trace tools)
  - `sdk/cpp/` — C++ SDK headers
  - `cli/` — `boat-cli` package (Typer CLI: `boat sim|scenario|can|eth|pdu|...`)
  - `config/` — PDU database JSON files
  - `demo/` — Demo scripts mirroring UI services
- **`ui/`** — 7 standalone FastAPI/uvicorn web services (launcher:8086, dashboard:8080, commander:8082, recorder:8083, control_panel, pdu_editor, trace_analyzer)
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
```

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
bash start_ui.sh   # launches all 7 services in background
bash stop_ui.sh    # kills them all
```

Each service is a standalone `python3 ui/<name>.py` FastAPI/uvicorn app with embedded HTML. SDK path is resolved via `sys.path.insert(0, ...)` relative to the script location.

## Quirks & gotchas

- Gateway binary path: README says `build/{preset}/src/gateway/grpc_gateway/boat_gateway`, but CI checks `build/release/gateway/grpc_gateway/boat_gateway` (without `src/`). Verify actual binary location if CI fails.
- `boat` CLI entry point (boat_cli/main.py): Typer app with subcommands. Uses `BoAtClient(address)` from `boat-py`.
- `python3 -m boat` dispatches: subcommands `can|pdu|eth|db` → `boat/cmd.py` (one-shot), anything else → `boat/cli.py` (interactive REPL).
- Proto stubs in `sdk/python/boat/stubs/boat/v1/` must be regenerated when proto files change (`generate_stubs.sh`).
- iceoryx2 requires `cargo` (Rust) at build time only; the resulting shared-memory IPC is used at runtime for large payloads (>4KB).
- HIL tests need `BOAT_HIL_ENABLED=1` and a real or virtual CAN interface (`vcan0`).
- Determinism test runs simulation twice with same seed and expects bit-exact output.
- Coverage report: `gcovr --root . --exclude build/ --xml coverage.xml`.
- Release packaging: `cpack -G "TGZ;DEB;RPM"`.
- Docker images pushed to `ghcr.io/cetitec/boat-platform:*`.

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

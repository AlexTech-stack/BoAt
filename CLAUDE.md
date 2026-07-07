# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

BoAt is a deterministic automotive simulation and testing platform (SIL/HIL/CI). At its core is a **tick-based simulation gateway** (`boat_gateway`, C++20) that bridges virtual and physical CAN/Ethernet networks, exposes a **gRPC API** (15 protobuf services), and is driven by a **Python SDK (`boat-py`) and CLI (`boat-cli`)**. Custom node logic is loaded as **C-ABI `.so` plugins**.

> **Status: Work In Progress.** APIs, config, and behavior change without notice.

The bulk of the code lives under `boat-platform/`. There is a detailed, feature-oriented companion doc at **`AGENTS.md`** (build recipes, PDU/CAN-TP/SOME/IP usage, CLI examples) — consult it for command-by-command feature reference. This file focuses on the big-picture architecture and the commands you need most.

## Build & test

Everything C++ is CMake + Ninja, driven by presets in `boat-platform/CMakePresets.json`. **Run these from `boat-platform/`.**

```bash
# Configure + build (presets: debug, release, asan, tsan, coverage)
cmake --preset debug && cmake --build --preset debug

# Gateway binary lands at:
build/debug/src/gateway/grpc_gateway/boat_gateway

# C++ tests (Catch2 via ctest)
ctest --preset release --output-on-failure
ctest --test-dir build/debug -R TestName --timeout 30 --output-on-failure   # single test
ctest --test-dir build/debug -N                                             # list tests
```

Test-binary naming convention: `boat_unit_*`, `boat_integration_*`, `boat_hil_*`, `boat_determinism_seed`.

Python SDK + CLI (editable installs, then pytest):

```bash
pip install -e ./boat-platform/sdk/python[dev] && pip install -e ./boat-platform/cli
pytest boat-platform/sdk/python/tests boat-platform/cli/tests -v
```

Toolchain notes: needs CMake **3.24+** (Ubuntu 22.04's 3.22 is too old), Ninja, g++/C++20, `libacl1-dev`, and a **Rust toolchain** (`cargo`) — Rust is a build-time-only transitive dep of iceoryx2 (used at runtime for large-payload shared-memory IPC).

## Running the gateway

The gateway inspects interfaces at startup: `vcan*` → `VirtualCanDriver`, all others → `PhysicalCanDriver` (reads sysfs for driver metadata).

```bash
sudo modprobe vcan
sudo ip link add vcan0 type vcan && sudo ip link set vcan0 up
BOAT_CAN_INTERFACES=vcan0 ./build/debug/src/gateway/grpc_gateway/boat_gateway
# gRPC listens on 0.0.0.0:50051
```

Key env vars: `BOAT_CAN_INTERFACES` / `BOAT_ETH_INTERFACES` (comma-separated), `BOAT_NODE_PLUGINS` (comma-separated `.so` paths for the always-on node manager), `BOAT_NODE_TICK_MS` / `BOAT_NODE_TICK_US` (tick period; the tick is the minimum cycle time), `BOAT_HIL_ENABLED=1` (required for HIL tests).

## Architecture (the parts that span files)

**Simulation core (`src/core/`)** — A deterministic tick scheduler drives a signal router, determinism guard, and plugin manager. Determinism is the central invariant: the `boat_determinism_seed` test runs the same seed twice and asserts **bit-identical** output. Preserve this when touching scheduling, ordering, or RNG.

**Dual PluginManager** — Two independent `PluginManager` instances run concurrently with the same ABI but different lifetimes:
- a **simulation-scoped** manager driven by the tick scheduler during sim runs, and
- an **always-on node** manager driven by its own independent tick thread, for persistent plugins (CAN-TP, SOME/IP, TCP).

Plugins loaded via `BOAT_NODE_PLUGINS` go to the always-on manager. This distinction matters constantly.

**Plugin ABI (`sdk/cpp/include/boat/plugin.h`)** — C ABI, currently **version 7** (`BOAT_PLUGIN_ABI_VERSION`). Hooks: `on_tick`, `on_can_frame`, `on_eth_frame`. `BOAT_CAN_FLAG_SELF_SENT` (0x08) tags internally-dispatched frames to prevent self-loop — respect it in frame handlers. Built-in plugins in `src/plugins/`: `vehicle_dynamics`, `sensor_model`, `network_sim`, `can_responder`, `can_tp` (ISO 15765-2), `someip`, `tcp`. Standalone plugin C APIs also live in `sdk/cpp/include/boat/` (e.g. `can_tp.h`, `someip.h`).

**HIL bridge (`src/hil/`)** — CAN/Ethernet drivers plus the AUTOSAR-inspired **PDU router**. `pdu/com/` holds the COM signal library (bit pack/unpack, Intel/Motorola byte order, E2E CRC8/16/32); `pdu/transmission_engine.*` schedules Cyclic/OnChange/Mixed sends; `pdu/tick_timer.*` is a dual-backend timer (uses Linux `timerfd`, absolute-time, no drift). The gateway's `OnTick()` drives the transmission engine.

**gRPC surface (`proto/boat/v1/`)** — 15 `.proto` files, one per service (simulation, signal, scenario, replay, fault, metrics, trace, can, ethernet, pdu, plugin, debug, control, common, **bus** — the last is the always-on `BusService`). **After editing any `.proto`, regenerate Python stubs** with `bash boat-platform/sdk/python/boat/stubs/generate_stubs.sh` — the generated stubs under `sdk/python/boat/stubs/boat/v1/` are committed and must stay in sync.

**Storage (`src/store/`)** — SQLite-backed event/trace/config stores; the replay engine (`src/replay/`) reconstructs any prior run from the event store. Fault injection is seeded and deterministic.

**IPC (`src/ipc/`)** — gRPC, iceoryx2 shared memory (large payloads >4KB), and UDS.

## Python CLI / SDK

```bash
boat --help
boat sim init|start|pause|step|stop
boat can send|listen|list-buses|detect     # `detect` scans /sys/class/net, needs no gateway
boat scenario create|validate|get|list
boat pdu route|group|enable-group|...       # PDU routing/groups (see AGENTS.md)
boat can-tp configure|send                  # ISO-TP
```

Dispatch quirk: `python3 -m boat` sends `can|pdu|eth|db` subcommands to `boat/cmd.py` (one-shot) and everything else to `boat/cli.py` (interactive REPL). Programmatic use: `from boat.client import BoAtClient`.

## Web UI & tools (require a running gateway)

`ui/` holds standalone FastAPI/uvicorn services (each is `python3 ui/<name>.py` with embedded HTML: launcher, dashboard, commander, control_panel, recorder, debug, system_dashboard). `tools/` holds `pdu_editor.py` and `trace_analyzer.py`. Launch/stop via `start_ui.sh`/`stop_ui.sh` and `start_tools.sh`/`stop_tools.sh`. Each resolves the SDK via `sys.path.insert(0, ...)` relative to the script.

## Conventions & gotchas

- **Determinism is a hard invariant** — don't introduce unseeded randomness, wall-clock-dependent ordering, or nondeterministic iteration in core/scheduling code.
- **Proto → regenerate stubs** (see above); committed stubs drift silently otherwise.
- **`vcan*` vs physical** interface handling is decided at gateway startup — new driver behavior usually belongs in `VirtualCanDriver` vs `PhysicalCanDriver`.
- HIL tests need `BOAT_HIL_ENABLED=1` and a real/virtual CAN interface (`vcan0`).
- Coverage: `gcovr --root . --exclude build/ --xml coverage.xml`. Packaging: `cpack -G "TGZ;DEB;RPM"`. Docker: `ghcr.io/boat-platform/boat-platform:*`.
- LLM cost-control guidance for the AI codegen path: `boat-platform/docs/ai/llm-cost-control.md`.

## AUTOSAR spec reference

Specs are provided locally under `spec/` (symlinked, **gitignored** — populate per machine): `spec/latest/` (PDFs), `spec/text/` (flat UTF-8), `spec/search.db` (SQLite FTS5 index), `spec/GUIDE.md` (search workflow). Query `search.db` (FTS5 `docs MATCH`) to find the right document, then `grep` the corresponding `spec/text/*.txt`. See `AGENTS.md` for the exact snippet. Open gap analyses live in `backlog/` (`pdu_gap_analysis.md`, `can_tp_plugin_backlog.md`, `tcp_plugin_backlog.md`).

## Specialized subagents

`.opencode/agents/*.md` define domain-scoped subagents (cpp-build-test, hil-testing, plugin-sdk, pdu-database, proto-codegen, py-sdk-cli, storage-layer, trace-analysis, web-ui, devops-ci, spec-reference, ai-codegen, boat-test-engineer, docs-arch, e2e-integration). They're a useful map of how the codebase is partitioned by responsibility.

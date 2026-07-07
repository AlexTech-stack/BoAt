# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **This branch (`Replay_ABIv8_adaption`) is the ABI v8 refactor.** It differs substantially from `master` (which is v7). The single most important thing to internalize is the **v8 architecture** described below — much of the platform was re-shaped around a **unified frame type**, **everything-is-a-plugin** dispatch, and a **plugin-based replay pipeline**. If something you read elsewhere assumes `BoatCanFrame`/`BoatEthFrame`, a core-resident PduRouter, or `boat can`/`boat eth` as primary commands, it is pre-v8 and no longer correct here.

## What this is

BoAt is a deterministic automotive simulation and testing platform (SIL/HIL/CI). Its core is a **tick-based simulation gateway** (`boat_gateway`, C++20) that bridges virtual and physical CAN/Ethernet networks, exposes a **gRPC API**, and is driven by a **Python SDK (`boat-py`) and CLI (`boat-cli`)**. Node/transport logic is loaded as **C-ABI `.so` plugins**.

The bulk of the code lives under `boat-platform/`. `AGENTS.md` is the detailed feature reference (already updated for v8), and `boat-platform/docs/architecture/system-architecture.md` covers the v8 architecture in depth. This file focuses on the big picture and the commands you need most.

> **Status: Work In Progress.** APIs, config, and behavior change without notice.

## The v8 architecture (what changed, and why it matters everywhere)

Read these before editing frame/plugin/replay code — they are cross-cutting invariants, not local details.

1. **Unified frame type.** There is one `BoatFrame` (ABI, `sdk/cpp/include/boat/frame.h`) and one internal `core::Frame` (`src/core/`) covering **all** bus types: `can`, `canfd`, `eth`, `tcp`, `pdu`. The old separate `BoatCanFrame` / `BoatEthFrame` types and their typedefs are **deleted**. Conversions cross the ABI boundary via `core::Frame::ToAbi()` / `ProtoToCoreFrame()`. The wire/trace representation is the `boat.v1.Frame` protobuf.

2. **Plugin ABI v8** (`sdk/cpp/include/boat/plugin.h`, `BOAT_PLUGIN_ABI_VERSION = 8`). v7 fallbacks are gone; a v7 plugin is **rejected at load with a clear error**. The vtable has 9 fields: `initialize`, `on_tick`, `shutdown`, `set_publisher`, `set_bus_publisher`, `set_pdu_publisher`, `on_frame`, `set_frame_publisher`, `declared_buses`. New plugins should implement `on_frame` (receive) + `set_frame_publisher` (send) + `declared_buses` (which bus types it handles).

3. **Core owns transport; plugins own conversations.** The gateway is a thin dispatcher. The **single `FrameSink`** (`src/gateway/grpc_gateway/frame_sink.{h,cpp}`) is the *only* path a frame reaches a bus — it routes by `bus_type` to `CanBusRegistry` / `EthernetBusRegistry`. Inbound frames go through `PluginManager::DispatchFrame()`, which calls `on_frame` **only on plugins whose `declared_buses` include that bus type** (pre-filtered at load — no O(N) fan-out). Plugins publish outbound frames back through a `frame_publish_fn` (wired to the `FrameSink`). Two `PluginManager` instances still run concurrently (a simulation-scoped one driven by the tick scheduler, and an always-on node manager driven by its own tick thread). The rule: **stateless transport (CAN/Ethernet frames on/off the wire) is core; stateful conversations (TCP, ISO-TP, PDU routing, SOME/IP) are plugins.**

4. **PduRouter is now a plugin.** It moved out of core into `src/plugins/pdu_router/` (`pdu_router.so`) and is loaded like any other plugin — it is no longer auto-loaded into the gateway core. PDU routing, transmission engine, groups, and deadline monitoring live there. gRPC PDU calls are **delegated** to the plugin.

5. **`FrameService` gRPC + `boat frame` CLI.** A unified `FrameService` provides send/subscribe for **all** bus types. `boat frame send` / `boat frame subscribe` are the primary CLI verbs; `boat can` / `boat eth` are **deprecated** (kept as thin wrappers). Proto count is now 16 services under `proto/boat/v1/`.

6. **Plugin config is data-driven.** Plugins take JSON config appended to their path as a query string: `plugin.so?{"iface":"vcan0"}`. TCP's old dedicated C API was removed — TCP is now a config-driven, gateway-resident v8 plugin (`tcp.so?{"mode":"server","listen_port":8080,...}`).

7. **Replay pipeline.** Replay does not write to buses directly. `ReplayController` (`src/replay/`) parses trace records into `core::Frame` and transmits each through the single `FrameSink` (`replay_controller.SetEventForwarder`). The registry's RX dispatch then delivers replayed frames to plugins' `on_frame`, so plugins still observe replayed traffic. There is **no FrameForwarder plugin** — that indirection (and the `can_io` direct-SocketCAN alternative) was removed; the core `FrameSink` is the one path to the wire.

8. **`FrameService.SendFrame` for non-wire buses.** Every producer — plugins, replay, and gRPC `FrameService.SendFrame` — transmits through the one `FrameSink`. TCP and PDU are not wire buses: a **TCP** send returns `UNIMPLEMENTED` (TCP is driven through the TCP plugin's connection API, not raw frame send); a **PDU** send is dispatched to the `pdu_router` plugin via `PluginManager::DispatchFrame`.

9. **Determinism remains the hard invariant.** The `boat_determinism_seed` test runs the same seed twice and asserts **bit-identical** output. Don't introduce unseeded randomness or nondeterministic ordering in core/scheduling/replay code.

**Loopback prevention:** the registry send path is the **single site** that tags locally-sent frames — `BOAT_CAN_FLAG_SELF_SENT` (0x08) / `BOAT_ETH_FLAG_SELF_SENT` (0x01) — so plugins can tell their own echoes from wire RX in `on_frame`. Keep this tagging in the registry (not scattered across plugins) when touching frame flow.

## Build & test

C++ is CMake + Ninja via presets in `boat-platform/CMakePresets.json`. **Run from `boat-platform/`.**

```bash
# Configure + build (presets: debug, release, asan, tsan, coverage)
cmake --preset debug && cmake --build --preset debug

# Gateway binary:
build/debug/src/gateway/grpc_gateway/boat_gateway

# C++ tests (Catch2 via ctest)
ctest --preset release --output-on-failure
ctest --test-dir build/debug -R TestName --timeout 30 --output-on-failure   # single test
ctest --test-dir build/debug -N                                             # list tests
```

Test-binary naming: `boat_unit_*`, `boat_integration_*`, `boat_hil_*`, `boat_determinism_seed`. v8 added `test_frame.cpp` (unified-frame unit tests) — a good reference for the frame model.

Python SDK + CLI:

```bash
pip install -e ./boat-platform/sdk/python[dev] && pip install -e ./boat-platform/cli
pytest boat-platform/sdk/python/tests boat-platform/cli/tests -v
```

Toolchain: CMake **3.24+** (Ubuntu 22.04's 3.22 is too old), Ninja, g++/C++20, `libacl1-dev`, and a **Rust toolchain** (`cargo`) — build-time-only transitive dep of iceoryx2 (runtime SHM IPC for payloads >4KB).

## Running the gateway

Interfaces are inspected at startup: `vcan*` → `VirtualCanDriver`, others → `PhysicalCanDriver` (sysfs metadata). Plugins are passed via `BOAT_NODE_PLUGINS` (comma-separated `.so` paths, each optionally with `?{json}` config).

```bash
sudo modprobe vcan
sudo ip link add vcan0 type vcan && sudo ip link set vcan0 up

# Bare gateway (core FrameSink handles CAN/Eth) — gRPC on 0.0.0.0:50051
BOAT_CAN_INTERFACES=vcan0 ./build/debug/src/gateway/grpc_gateway/boat_gateway

# With PDU routing + transport plugins (note per-plugin JSON config)
BOAT_CAN_INTERFACES=vcan0 \
  BOAT_NODE_PLUGINS=./build/debug/src/plugins/pdu_router/pdu_router.so,./build/debug/src/plugins/can_tp/can_tp.so?{\"iface\":\"vcan0\"} \
  ./build/debug/src/gateway/grpc_gateway/boat_gateway
```

Key env vars: `BOAT_CAN_INTERFACES` / `BOAT_ETH_INTERFACES`, `BOAT_NODE_PLUGINS`, `BOAT_NODE_TICK_MS` / `BOAT_NODE_TICK_US` (tick = minimum cycle time), `BOAT_HIL_ENABLED=1` (HIL tests).

## Python CLI / SDK (v8 surface)

```bash
boat sim init|start|pause|step|stop
boat frame send --bus-type can --can-id 0x123 --iface vcan0 --data AABBCCDD   # unified send
boat frame subscribe --bus-types can                                          # unified subscribe
boat can send|listen|list-buses|detect     # can/eth are deprecated wrappers; `detect` needs no gateway
boat pdu route|group|enable-group|...       # delegated to the pdu_router plugin
boat can-tp configure|send                  # ISO-TP
boat replay import <trace> --trace-id ...   # convert+upload .asc/.blf/.pcap → boat.v1.Frame records
boat replay stream --trace <id> --speed accelerated --multiplier 2.0 --buses vcan0
```

Programmatic: `from boat.client import BoAtClient` / `from boat.frame_node import FrameNode` (e.g. `node.send_can("vcan0", 0x123, b"...")`). Dispatch quirk: `python3 -m boat` routes `can|pdu|eth|db` to `boat/cmd.py` (one-shot), everything else to `boat/cli.py` (REPL).

**After editing any `.proto`, regenerate Python stubs**: `bash boat-platform/sdk/python/boat/stubs/generate_stubs.sh`. The generated stubs under `sdk/python/boat/stubs/boat/v1/` are committed and must stay in sync.

## Web UI & tools (require a running gateway)

`ui/` holds standalone `python3 ui/<name>.py` FastAPI services (launcher, dashboard, commander, control_panel, recorder, debug, system_dashboard); `tools/` holds `pdu_editor.py`, `trace_analyzer.py`, plus `dbc2boatjson.py`. Launch via `start_ui.sh`/`start_tools.sh`, stop via `stop_ui.sh`/`stop_tools.sh`. Each resolves the SDK via `sys.path.insert(0, ...)` relative to the script.

## Other conventions & gotchas

- **`vcan*` vs physical** driver selection is decided at gateway startup — new driver behavior usually belongs in `VirtualCanDriver` vs `PhysicalCanDriver`.
- `add_boat_plugin()` (`cmake/BoAtPlugin.cmake`) is the macro for registering a new plugin target; `BoAtProto.cmake` wraps protobuf generation.
- Coverage: `gcovr --root . --exclude build/ --xml coverage.xml`. Packaging: `cpack -G "TGZ;DEB;RPM"`. Docker: `ghcr.io/boat-platform/boat-platform:*`.
- System-test structure/conventions: `test/Structure.md`. LLM cost-control guidance: `boat-platform/docs/ai/llm-cost-control.md`.

## AUTOSAR spec reference

Specs live under `spec/` (symlinked, **gitignored** — populate per machine): `spec/latest/` (PDFs), `spec/text/` (flat UTF-8), `spec/search.db` (SQLite FTS5), `spec/GUIDE.md`. Query `search.db` (FTS5 `docs MATCH`) to find the right document, then `grep` the matching `spec/text/*.txt`. Open gap analyses are in `backlog/`.

## Specialized subagents

`.opencode/agents/*.md` define domain-scoped subagents (cpp-build-test, hil-testing, plugin-sdk, pdu-database, proto-codegen, py-sdk-cli, storage-layer, trace-analysis, web-ui, devops-ci, spec-reference, ai-codegen, boat-test-engineer, docs-arch, e2e-integration) — a useful map of how the codebase is partitioned by responsibility.

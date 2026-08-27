# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Sibling file: `AGENTS.md`.** The two are split by *audience*, not by depth: Claude Code
> loads this file, every other agent/model (opencode — see `.opencode/`, and anything else
> following the AGENTS.md convention) loads `AGENTS.md`. They describe the same repository
> and must never disagree. **So: any change you make here to a fact about the codebase has
> to land in `AGENTS.md` too** — otherwise the other tools keep acting on the stale version
> and nobody notices, because neither file's readers see the other. Neither is authoritative
> over the other; the source code is authoritative over both.
>
> `AGENTS.md` is the longer file and goes deeper on several areas (see "Where AGENTS.md goes
> deeper" at the end of this file). It is *not* auto-loaded for you — read it directly when
> you need that detail.

## What this is

BoAt is a deterministic automotive simulation and testing platform (SIL/HIL/CI). Its core is a **tick-based simulation gateway** (`boat_gateway`, C++20) that bridges virtual and physical CAN/Ethernet networks, exposes a **gRPC API**, and is driven by a **Python SDK (`boat-py`) and CLI (`boat-cli`)**. Node/transport logic is loaded as **C-ABI `.so` plugins**.

The bulk of the code lives under `boat-platform/`. `admin_gui/` (PySide6 desktop client) and `ui/` + `tools/` (FastAPI web services) sit at the repo root. `boat-platform/docs/architecture/system-architecture.md` covers the architecture in depth.

> **Status: Work In Progress.** APIs, config, and behavior change without notice.

## License

The project is **Apache-2.0** (`LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md` at the repo root).

- **New source files get a two-line SPDX header** matching the surrounding files — the
  comment prefix follows the language (`//`, `#`, `--`):
  ```
  // Copyright 2026 Alexander Günther
  // SPDX-License-Identifier: Apache-2.0
  ```
  It goes *after* any `#!` shebang. Do not paste the full Apache boilerplate; the short
  form is what the rest of the tree uses.
- **Generated files are deliberately header-less** — the protoc stubs under
  `sdk/python/boat/stubs/boat/v1/` and `tools/wireshark/boat_pdu_db.lua`. Their generators
  would overwrite a header on the next run. Don't "fix" them.
- **Nothing third-party is vendored into this tree.** C++ deps come in via CMake
  `FetchContent` at build time; Python deps via pip. If you add a dependency, add it to
  `THIRD_PARTY_NOTICES.md` too.
- `tools/dbc/` fetches comma.ai's opendbc DBCs **on demand** (`tools/dbc/fetch_opendbc.sh`,
  download-only) into a gitignored directory. Converting one to BoAt's PDU-database JSON is
  a separate manual step — see `tools/dbc/README.md`. Don't commit fetched or derived files.

## Architecture (ABI v8) — cross-cutting invariants

These are on `master`. Read them before editing frame/plugin/replay code — they are
cross-cutting invariants, not local details. If something you read elsewhere assumes
`BoatCanFrame`/`BoatEthFrame`, a core-resident PduRouter, or `boat can`/`boat eth` as
CLI commands, it is pre-v8 and no longer correct.

1. **Unified frame type.** There is one `BoatFrame` (ABI, `sdk/cpp/include/boat/frame.h`) and one internal `core::Frame` (`src/core/`) covering **all** bus types: `can`, `canfd`, `eth`, `tcp`, `pdu`. The old separate `BoatCanFrame` / `BoatEthFrame` types and their typedefs are **deleted**. Conversions cross the ABI boundary via `core::Frame::ToAbi()` / `ProtoToCoreFrame()`. The wire/trace representation is the `boat.v1.Frame` protobuf.

2. **Plugin ABI v8** (`sdk/cpp/include/boat/plugin.h`, `BOAT_PLUGIN_ABI_VERSION = 8`). v7 fallbacks are gone; a v7 plugin is **rejected at load with a clear error**. The vtable has 9 fields: `initialize`, `on_tick`, `shutdown`, `set_publisher`, `set_bus_publisher`, `set_pdu_publisher`, `on_frame`, `set_frame_publisher`, `declared_buses`. New plugins should implement `on_frame` (receive) + `set_frame_publisher` (send) + `declared_buses` (which bus types it handles).

3. **Core owns transport; plugins own conversations.** The gateway is a thin dispatcher. The **single `FrameSink`** (`src/gateway/grpc_gateway/frame_sink.{h,cpp}`) is the *only* path a frame reaches a bus — it routes by `bus_type` to `CanBusRegistry` / `EthernetBusRegistry`. Inbound frames go through `PluginManager::DispatchFrame()`, which calls `on_frame` **only on plugins whose `declared_buses` include that bus type** (pre-filtered at load — no O(N) fan-out). Plugins publish outbound frames back through a `frame_publish_fn` (wired to the `FrameSink`). Two `PluginManager` instances run concurrently (a simulation-scoped one driven by the tick scheduler, and an always-on node manager driven by its own tick thread). The rule: **stateless transport (CAN/Ethernet frames on/off the wire) is core; stateful conversations (TCP, ISO-TP, PDU routing, SOME/IP) are plugins.**

4. **PduRouter is a plugin.** It lives in `src/plugins/pdu_router/` (`pdu_router.so`) and is loaded like any other plugin — it is *not* auto-loaded into the gateway core. PDU routing, transmission engine, groups, and deadline monitoring live there. gRPC PDU calls are **delegated** to the plugin. The five built-in plugins are `pdu_router`, `can_tp`, `someip`, `tcp`, `probe`.

5. **`FrameService` gRPC + `boat frame` CLI.** A unified `FrameService` provides send/subscribe for **all** bus types. `boat frame send` / `boat frame subscribe` / `boat frame list-ifaces` are the CLI verbs. The old `boat can` / `boat eth` Typer commands are **removed outright** — not deprecated wrappers, gone (there is no `can.py`/`eth.py` in `boat_cli/`). There is no CLI hardware-detection command either; use `ip -d link show type can`. `proto/boat/v1/` holds **18 `.proto` files declaring 16 gRPC services**.

6. **Plugin config is data-driven.** Plugins take JSON config appended to their path as a query string: `plugin.so?{"iface":"vcan0"}`. `BOAT_NODE_PLUGINS` is split brace-aware, so commas inside a `{...}` config don't split the entry. TCP's old dedicated C API was removed — TCP is now a config-driven, gateway-resident v8 plugin (`tcp.so?{"mode":"server","listen_port":8080,...}`).

7. **Replay pipeline.** Replay does not write to buses directly. `ReplayController` (`src/replay/`) parses trace records into `core::Frame` and transmits each through the single `FrameSink` (`replay_controller.SetEventForwarder`). The registry's RX dispatch then delivers replayed frames to plugins' `on_frame`, so plugins still observe replayed traffic. There is **no FrameForwarder plugin** — that indirection (and the `can_io` direct-SocketCAN alternative) was removed; the core `FrameSink` is the one path to the wire. Interface/MAC targeting is a **replay-time** decision, not baked in at import, so one imported trace replays against different hardware without re-importing.

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

Test-binary naming: `boat_unit_*`, `boat_integration_*`, `boat_hil_*`, `boat_determinism_seed`. `src/tests/unit/test_frame.cpp` (`boat_unit_frame`) is a good reference for the unified frame model.

**Three different things are called "test" here** — be precise about which you mean:
`ctest`/`pytest` (tests of the codebase itself); `test/*.md` (the **manual**, hand-verified
release sign-off record — never update those verdicts programmatically); and
`boat test run <manifest.json>` (an **automated** CI-style HIL suite runner: an
`EnvironmentConfig` + a `ManifestConfig` drive `TestSuiteRunner`, which spins up or connects
to a gateway, runs each test file as a subprocess, and writes JSON/JUnit/HTML reports —
`boat_cli/test.py`, `config/tests/{env,manifest}_*.json`).

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

Key env vars (`src/gateway/grpc_gateway/main.cpp` is the authoritative list):

- `BOAT_CAN_INTERFACES` / `BOAT_ETH_INTERFACES` — interfaces to open
- `BOAT_NODE_PLUGINS` — plugins to load
- `BOAT_NODE_TICK_MS` / `BOAT_NODE_TICK_US` — tick = minimum cycle time (compiled-in default 1ms; `_US` overrides `_MS` when both are set)
- `BOAT_GRPC_PORT` — default 50051; set to run more than one gateway on a host. The gateway **refuses to start** if the port is taken rather than silently sharing it via gRPC's `SO_REUSEPORT`.
- `BOAT_TLS_CERT` + `BOAT_TLS_KEY` — opt-in TLS, PEM paths, **must be set together**; `BOAT_TLS_CLIENT_CA` additionally requires client certs (mTLS). Note the Python `BoAtClient` has no TLS support yet — it builds insecure channels only, so a TLS-enabled gateway is not reachable from the SDK/CLI as-is.
- `BOAT_HIL_ENABLED=1` — HIL tests

## Python CLI / SDK

Subcommands (`cli/boat_cli/main.py`): `ai`, `sim`, `scenario`, `replay`, `plugin`, `can-tp`, `frame`, `pdu`, `db`, `test`, `trace`.

```bash
boat sim init|start|pause|step|stop
boat frame send --bus-type can --can-id 0x123 --iface vcan0 --data AABBCCDD   # unified send
boat frame subscribe --bus-types can                                          # unified subscribe
boat frame list-ifaces                      # list CAN + Ethernet interfaces the gateway sees
boat pdu route|group|enable-group|...       # delegated to the pdu_router plugin
boat can-tp configure|send                  # ISO-TP
boat plugin list                            # both PluginManagers, with a `scope` column
boat test run <manifest.json>               # automated HIL suite runner
boat replay import <trace> --trace-id ...   # convert+upload .asc/.blf/.pcap → boat.v1.Frame records
boat replay stream --trace <id> --speed accelerated --multiplier 2.0 --buses vcan0
boat trace replay <trace.asc> --buses vcan0 # direct, CAN-only, client-paced (no import)
```

Programmatic: `from boat.client import BoAtClient` / `from boat.frame_node import FrameNode` (e.g. `node.send_can("vcan0", 0x123, b"...")`). Every `*Node` class and `BoAtClient` resolve their gateway address the same way: explicit `address=` > `BOAT_HOST` env var > `localhost:50051` — which is what keeps node scripts portable across gateways. The `boat` CLI's `--host` flag follows the same order.

Dispatch quirk: `python3 -m boat` routes `can|pdu|eth|db` to `boat/cmd.py` (one-shot, PDU-database-driven `can send`/`eth send` only — unrelated to the `boat` console script above, which has no `can`/`eth` subcommand), everything else to `boat/cli.py` (REPL).

**After editing any `.proto`, regenerate Python stubs**: `bash boat-platform/sdk/python/boat/stubs/generate_stubs.sh`. The generated stubs under `sdk/python/boat/stubs/boat/v1/` are committed and must stay in sync.

## Node scripts, web UI & desktop client

- **`boat-platform/nodes/`** — general-purpose node scripts (`cyclic_can_sender.py`, `can_request_responder.py`, `pdu_cyclic_publisher.py`, `can_tp_trigger_sender.py`). Each takes `--address` (default `None`, so `BOAT_HOST` decides) plus its own flags via a module-level `build_parser()`, which the tooling introspects to build UI fields. `boat-platform/demo/` holds scenario-specific demo nodes instead.
- **`ui/`** — 8 standalone `python3 ui/<name>.py` FastAPI services: launcher (8086), dashboard (8080), commander (8082), control_panel (8081), recorder (8083), debug (8084), system_dashboard (8081), launcher_agent (8090). `start_ui.sh` launches only **5** of them (launcher, dashboard, commander, control_panel, recorder); `stop_ui.sh` kills them.
- **`tools/`** — `pdu_editor.py` (8087), `trace_analyzer.py` (8088), `trace_editor.py` (8089), `eth_trace_analyzer.py` (8090) are services launched by `start_tools.sh`/`stop_tools.sh`; `dbc2boatjson.py` and `test_vw_mlb_replay.py` are plain scripts, not services.
- **`admin_gui/`** — PySide6 desktop client (at the **repo root**, not under `boat-platform/`) for one or more launcher agents; pages: Gateway, Nodes, Test Runs, Interfaces, Settings. `python3 admin_gui/main.py`; headless verification via `QT_QPA_PLATFORM=offscreen`.

Each web service resolves the SDK via `sys.path.insert(0, ...)` relative to its own script. **Port collisions to know about:** `control_panel` and `system_dashboard` both default to 8081, and `launcher_agent` and `eth_trace_analyzer` both default to 8090 — each has a `BOAT_*_PORT` env override.

## Other conventions & gotchas

- **`vcan*` vs physical** driver selection is decided at gateway startup — new driver behavior usually belongs in `VirtualCanDriver` vs `PhysicalCanDriver`.
- `add_boat_plugin()` (`cmake/BoAtPlugin.cmake`) is the macro for registering a new plugin target; it also copies an optional `<name>.schema.json` config sidecar next to the `.so`, which `admin_gui` reads to build per-key config fields. `BoAtProto.cmake` wraps protobuf generation.
- Coverage: `gcovr --root . --exclude build/ --xml coverage.xml`. Packaging: `cpack -G "TGZ;DEB;RPM"`. Docker: `ghcr.io/boat-platform/boat-platform:*`.
- System-test structure/conventions: `test/Structure.md`. Per-feature manual runbooks: `boat-platform/docs/testing/`. LLM cost-control guidance: `boat-platform/docs/ai/llm-cost-control.md`.
- Open issues and incident write-ups live in `backlog/*.md` — worth grepping before assuming a rough edge is unknown.

## AUTOSAR spec reference

Specs live under `spec/` (symlinked, **gitignored** — populate per machine): `spec/latest/` (PDFs), `spec/text/` (flat UTF-8), `spec/search.db` (SQLite FTS5), `spec/GUIDE.md`. Query `search.db` (FTS5 `docs MATCH`) to find the right document, then `grep` the matching `spec/text/*.txt`. Open gap analyses are in `backlog/`.

## Specialized subagents

`.opencode/agents/*.md` define 15 domain-scoped subagents (cpp-build-test, hil-testing, plugin-sdk, pdu-database, proto-codegen, py-sdk-cli, storage-layer, trace-analysis, web-ui, devops-ci, spec-reference, ai-codegen, boat-test-engineer, docs-arch, e2e-integration) — a useful map of how the codebase is partitioned by responsibility.

## Where AGENTS.md goes deeper

`AGENTS.md` is ~6× this file. Nothing in it contradicts what's above — it just carries
operational detail this file summarizes in a line or two. Read the relevant section there
(it is not auto-loaded) before doing real work in these areas:

| Topic | AGENTS.md section | Why you'd want it |
|---|---|---|
| Admin GUI internals | `## Admin GUI (PySide6 client)` (~208 lines) | Per-page behavior, dialog wiring, the dark-theme conventions, session save/load format, and several real hardware bugs found + fixed |
| CanTp / ISO-TP | `### CanTp — CAN Transport Protocol` (~134 lines) | N_Bs/N_Cr watchdog semantics, addressing modes, CAN FD padding, multi-instance `--iface` rules, and the PDU-bus echo-loop hazard |
| Launcher Agent REST API | `## Launcher Agent` (~91 lines) | Full endpoint list, `external:<pid>` discovery of unmanaged gateways, node registry, `PYTHONUNBUFFERED` gotcha |
| PDU groups & schedules | `### I-PDU Groups`, `### Transmission Schedules` (~71 lines) | Every `boat pdu route` flag, the three send types, and the three ways to stop a cyclic send |
| COM signal library | `### COM Signal Library (C++)` (~27 lines) | `PackSignals`/`UnpackSignals` usage, Intel vs Motorola, E2E CRC helpers |
| Probe plugin | `### Probe Plugin` (~33 lines) | Config keys and what each conformance check proves — also the canonical minimal v8 plugin example |
| Replay internals | `## Replay System` + subsections | Trace record layout, the full import/stream flag surface, the sink dataflow diagram |
| Test-runner detail | `## Test` | The three-meanings-of-"test" distinction in full, plus `backlog/test_runner_backlog.md` context |

If you change a fact in one of those sections, change it here too — and vice versa.

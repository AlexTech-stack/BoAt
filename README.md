# BoAt

> **⚠ Work in Progress** — This project is under active development. APIs, configuration, and behavior may change without notice. Contributions and feedback welcome!

A deterministic automotive simulation and testing platform for Software-in-the-Loop, Hardware-in-the-Loop, and CI/CD validation pipelines.

---

## What is BoAt?

BoAt is a tick-based simulation gateway that bridges virtual and physical CAN/Ethernet networks. It provides a deterministic simulation engine, a plugin SDK for custom node logic, a gRPC API surface, and a Python CLI/SDK.

## Key capabilities

- **Deterministic core** — Tick-based scheduler with a seeded determinism engine. Replay reproduces frame content and ordering bit-identically across runs; tick-level timing does not yet reproduce under load. See [What "deterministic" means here](#what-deterministic-means-here).
- **CAN & Ethernet HIL** — Supports both virtual (`vcan*`) and physical CAN interfaces (PEAK PCAN, Kvaser, gs_usb) via SocketCAN, plus virtual Ethernet over UDP multicast.
- **Plugin SDK** — C ABI **v8** plugin interface built around a single unified `BoatFrame` type (CAN/CAN-FD/Ethernet/PDU/TCP). Plugins implement `on_tick`, `on_frame`, `set_frame_publisher`, and `declared_buses`. The core owns the stateless transport substrate: the single `FrameSink` is the only path a frame reaches a bus, and `PluginManager::DispatchFrame()` delivers inbound frames to plugins' `on_frame`, filtered to their declared bus types. `BOAT_CAN_FLAG_SELF_SENT` (0x08) / `BOAT_ETH_FLAG_SELF_SENT` (0x01) tag locally-sent frames to prevent self-loop. Load `.so` plugins at runtime with JSON config (`plugin.so?{...}`). Plugins own stateful conversations only — built-in set: PduRouter, CAN-TP (ISO 15765-2), TCP, SOME/IP.
- **Dual PluginManager architecture** — Two independent `PluginManager` instances run concurrently: a simulation-scoped manager (driven by the tick scheduler during simulation runs) and an always-on node manager (driven by its own independent tick thread for persistent plugins like CAN-TP). Both managers use the same ABI but serve different lifetimes.
- **gRPC API** — 16 protobuf services: Simulation, Signal, Scenario, Replay, Fault, Metrics, Trace, the unified **Frame** service (send/subscribe for all bus types), CAN, Ethernet, PDU, Plugin, Debug, and the always-on BusService.
- **Python SDK + CLI** — `boat-py` package with `BoAtClient`, `FrameNode`, `PduNode` classes. `boat-cli` with commands for sim, scenario, frame (unified send/subscribe), PDU, CAN-TP, replay, trace, and plugin management (the old `boat can`/`boat eth` commands were removed outright in ABI v8 — use `boat frame`).
- **PDU routing** — AUTOSAR-inspired PDU router with I-PDU groups, cyclic/onChange/mixed transmission schedules, and COM signal packing (Intel/Motorola, E2E CRC).
- **Event store & replay** — SQLite-backed event store. The replay controller reconstructs any prior simulation run — see [What "deterministic" means here](#what-deterministic-means-here) for which parts reproduce exactly.
- **Fault injection** — Seeded deterministic fault injector for reproducing fault scenarios (signal errors, CAN dropouts, timing faults).
- **Web dashboards** — 10 standalone FastAPI services providing live CAN frame traces, signal monitoring, PDU editing, trace analysis, and system dashboards.

## What "deterministic" means here

BoAt is a testing platform, so it matters exactly which properties reproduce and which
don't. The table below is verified end-to-end by `boat_determinism_replay`
(`boat-platform/src/tests/determinism/`), which replays one trace twice and diffs what
actually reached the wire:

| Property | Reproducible? |
|---|---|
| Seeded PRNG and fault-injection streams | **Yes** — by construction (`DeterminismEngine`) |
| Replayed frame content and ordering on the wire | **Yes** — asserted bit-identical, including under CPU load |
| Which tick a replayed frame is observed in | **Not yet** — identical on an idle host, diverges under contention |

The gap: replay, the simulation tick scheduler, and the always-on node tick thread are
three independent time domains. Replay and the node tick thread both schedule against
absolute deadlines, which holds them in lockstep on an idle machine but not on a loaded
one. In practice this means a test asserting on **frame content and ordering** reproduces
reliably, while a test asserting on **tick numbers** may not on a busy CI runner. Coupling
those clocks is open work.

## Quick start

See [boat-platform/README.md](boat-platform/README.md) for build prerequisites, build & run instructions.

```bash
# One-line summary
cd boat-platform && cmake --preset debug && cmake --build --preset debug
BOAT_CAN_INTERFACES=vcan0 ./build/debug/src/gateway/grpc_gateway/boat_gateway
```

## Learn more

- **[📘 The BoAt Guidebook](guidebook.html)** — start here: what BoAt is, setup, every UI and tool, a large how-to collection, and FAQs (open the file in a browser)
- [Project overview](boat-platform/docs/project.html)
- [Architecture](boat-platform/docs/architecture/system-architecture.md)
- [API specification](boat-platform/docs/api/api-specification.md)
- [Project plan](boat-platform/project-plan.md)
- [AGENTS.md](AGENTS.md) — Build, run, and development reference

## License

BoAt is open source under the [Apache License 2.0](LICENSE).

```
Copyright 2026 Alexander Günther

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

Contributions are accepted under the same license (Apache-2.0 §5). Third-party
components used at build or run time keep their own licenses — see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [NOTICE](NOTICE).

# BoAt

> **⚠ Work in Progress** — This project is under active development. APIs, configuration, and behavior may change without notice. Contributions and feedback welcome!

A deterministic automotive simulation and testing platform for Software-in-the-Loop, Hardware-in-the-Loop, and CI/CD validation pipelines.

**BoAt** is a name, not an acronym. The alternating capitals don't mean anything either.

---

## What is BoAt?

BoAt is a tick-based simulation gateway that bridges virtual and physical CAN/Ethernet networks. It provides a deterministic simulation engine, a plugin SDK for custom node logic, a gRPC API surface, and a Python CLI/SDK.

## Key capabilities

- **Deterministic core** — Tick-based scheduler with a seeded determinism engine. Replay reproduces frame content, ordering, *and* tick attribution bit-identically across runs, including under CPU load. See [What "deterministic" means here](#what-deterministic-means-here).
- **CAN & Ethernet HIL** — Supports both virtual (`vcan*`) and physical CAN interfaces (PEAK PCAN, Kvaser, gs_usb) via SocketCAN, plus virtual Ethernet over UDP multicast.
- **Plugin SDK** — C ABI **v9** plugin interface built around a single unified `BoatFrame` type (CAN/CAN-FD/Ethernet/PDU/TCP). Plugins implement `on_tick`, `on_frame`, `set_frame_publisher`, and `declared_buses`. The core owns the stateless transport substrate: the single `FrameSink` is the only path a frame reaches a bus, and `PluginManager::DispatchFrame()` delivers inbound frames to plugins' `on_frame`, filtered to their declared bus types. `BOAT_CAN_FLAG_SELF_SENT` (0x08) / `BOAT_ETH_FLAG_SELF_SENT` (0x01) tag locally-sent frames to prevent self-loop. Load `.so` plugins at runtime with JSON config (`plugin.so?{...}`). Plugins own stateful conversations only — built-in set: PduRouter, CAN-TP (ISO 15765-2), TCP, Probe, and SOME/IP (request/response; service discovery is still a stub).
- **Dual PluginManager architecture** — Two `PluginManager` instances with different lifetimes: an always-on node manager for persistent plugins like CAN-TP, and a simulation-scoped manager loaded per scenario. Both use the same ABI, and both are ticked by one `TickAuthority` rather than by clocks of their own.
- **gRPC API** — 14 protobuf services across 16 `.proto` files (64 RPCs): Simulation, Signal, Scenario, Replay, Fault, Metrics, Trace, the unified **Frame** service (send/subscribe/list-interfaces for all bus types), CAN-TP, PDU, Plugin, NodePlugin, Debug, and the always-on BusService. The per-bus `CanService` and `EthernetService` were deleted — `FrameService` replaces both.
- **Python SDK + CLI** — `boat-py` package with `BoAtClient`, `FrameNode`, `PduNode` classes. `boat-cli` with commands for sim, scenario, frame (unified send/subscribe), PDU, CAN-TP, replay, trace, and plugin management (the old `boat can`/`boat eth` commands were removed outright in the unified-frame migration — use `boat frame`).
- **PDU routing** — AUTOSAR-inspired PDU router with I-PDU groups, cyclic/onChange/mixed transmission schedules, and COM signal packing (Intel/Motorola, E2E CRC).
- **Event store & replay** — SQLite-backed event store. The replay controller reconstructs any prior simulation run — see [What "deterministic" means here](#what-deterministic-means-here) for which parts reproduce exactly.
- **Fault injection** — Seeded deterministic fault injector for reproducing fault scenarios (signal errors, CAN dropouts, timing faults).
- **Web dashboards** — 12 standalone FastAPI services providing live CAN frame traces, signal monitoring, PDU editing, trace analysis, and system dashboards: 8 under `ui/` (launcher, dashboard, commander, control_panel, recorder, debug, system_dashboard, launcher_agent) and 4 under `tools/` (pdu_editor, trace_analyzer, trace_editor, eth_trace_analyzer).

## What "deterministic" means here

BoAt is a testing platform, so it matters exactly which properties reproduce and which
don't. The table below is verified end-to-end by `boat_determinism_replay`
(`boat-platform/src/tests/determinism/`), which replays one trace twice and diffs what
actually reached the wire:

| Property | Reproducible? |
|---|---|
| Seeded PRNG and fault-injection streams | **Yes** — by construction (`DeterminismEngine`) |
| Replayed frame content and ordering on the wire | **Yes** — asserted bit-identical, including under CPU load |
| Which tick a replayed frame is observed in | **Yes** — asserted identical, including under CPU load |

Tick attribution used to be the gap: replay and the node tick thread were separate time
domains, both scheduling against absolute wall-clock deadlines, which held them in lockstep
on an idle machine but not on a loaded one. They are now ordered phases of a single
`TickAuthority` (`node_plugins` → `sim_plugins` → `replay`), and a replay record's due time
is computed from elapsed **ticks** rather than elapsed wall time. A loaded host therefore
makes the authority tick later in real terms without ever changing which tick a record comes
due on — which is what lets the test assert this with a plain `REQUIRE` rather than a
tolerance. Verified 10/10 identical under 2× CPU oversubscription, the load that previously
failed 0/6.

So a test may assert on tick numbers, not just on frame content and ordering. The one thing
that is *not* promised is wall-clock timing: a tick happens later on a busy host, it just
never contains different frames.

## Quick start

See [boat-platform/README.md](boat-platform/README.md) for build prerequisites, build & run instructions.

```bash
# 1. Build (run from boat-platform/)
cd boat-platform && cmake --preset debug && cmake --build --preset debug

# 2. A virtual CAN bus to talk to
sudo modprobe vcan
sudo ip link add vcan0 type vcan && sudo ip link set vcan0 up

# 3. Start the gateway — gRPC on 0.0.0.0:50051
BOAT_CAN_INTERFACES=vcan0 ./build/debug/src/gateway/grpc_gateway/boat_gateway
```

Then, in a second terminal:

```bash
pip install -e ./boat-platform/sdk/python[dev] && pip install -e ./boat-platform/cli

boat frame list-ifaces                                                       # what the gateway sees
boat frame send --bus-type can --iface vcan0 --can-id 0x123 --data AABBCCDD   # put a frame on the bus
boat frame subscribe --bus-types can                                         # watch traffic (Ctrl-C to stop)
```

And the simulation lifecycle, using the scenario that ships with the repo:

```bash
cd boat-platform
boat scenario create --file config/scenarios/example.json   # -> scenario_id "example"
boat sim create --scenario example                          # -> a simulation_id
boat sim start <simulation_id>
boat sim pause <simulation_id> && boat sim step --ticks 10 <simulation_id>
boat sim stop  <simulation_id>
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

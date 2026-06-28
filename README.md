# BoAt

> **⚠ Work in Progress** — This project is under active development. APIs, configuration, and behavior may change without notice. Contributions and feedback welcome!

A deterministic automotive simulation and testing platform for Software-in-the-Loop, Hardware-in-the-Loop, and CI/CD validation pipelines.

---

## What is BoAt?

BoAt is a tick-based simulation gateway that bridges virtual and physical CAN/Ethernet networks. It provides a deterministic simulation engine, a plugin SDK for custom node logic, a gRPC API surface, and a Python CLI/SDK.

## Key capabilities

- **Deterministic core** — Tick-based scheduler with seeded determinism guarantees bit-identical replay across runs and environments.
- **CAN & Ethernet HIL** — Supports both virtual (`vcan*`) and physical CAN interfaces (PEAK PCAN, Kvaser, gs_usb) via SocketCAN, plus virtual Ethernet over UDP multicast.
- **Plugin SDK** — C ABI v7 plugin interface with `on_tick`, `on_can_frame`, `on_eth_frame` hooks. `BOAT_CAN_FLAG_SELF_SENT` (0x08) tags internally-dispatched frames to prevent self-loop. Load `.so` plugins at runtime. Built-in plugins: vehicle dynamics, sensor simulation, CAN transport protocol (ISO 15765-2), SOME/IP middleware.
- **Dual PluginManager architecture** — Two independent `PluginManager` instances run concurrently: a simulation-scoped manager (driven by the tick scheduler during simulation runs) and an always-on node manager (driven by its own independent tick thread for persistent plugins like CAN-TP). Both managers use the same ABI but serve different lifetimes.
- **gRPC API** — 15 protobuf services: Simulation, Signal, Scenario, Replay, Fault, Metrics, Trace, CAN, Ethernet, PDU, Plugin, Debug, and the always-on BusService.
- **Python SDK + CLI** — `boat-py` package with `BoAtClient`, `CanNode`, `PduNode` classes. `boat-cli` with commands for sim, scenario, CAN, PDU, CAN-TP, replay, trace, and plugin management.
- **PDU routing** — AUTOSAR-inspired PDU router with I-PDU groups, cyclic/onChange/mixed transmission schedules, and COM signal packing (Intel/Motorola, E2E CRC).
- **Event store & replay** — SQLite-backed event store. Deterministic replay controller reconstructs any prior simulation run.
- **Fault injection** — Seeded deterministic fault injector for reproducing fault scenarios (signal errors, CAN dropouts, timing faults).
- **Web dashboards** — 10 standalone FastAPI services providing live CAN frame traces, signal monitoring, PDU editing, trace analysis, and system dashboards.

## Quick start

See [boat-platform/README.md](boat-platform/README.md) for build prerequisites, build & run instructions.

```bash
# One-line summary
cd boat-platform && cmake --preset debug && cmake --build --preset debug
BOAT_CAN_INTERFACES=vcan0 ./build/debug/src/gateway/grpc_gateway/boat_gateway
```

## Learn more

- [Project overview](boat-platform/docs/project.html)
- [Architecture](boat-platform/docs/architecture/system-architecture.md)
- [API specification](boat-platform/docs/api/api-specification.md)
- [Project plan](boat-platform/project-plan.md)
- [AGENTS.md](AGENTS.md) — Build, run, and development reference

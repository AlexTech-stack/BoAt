# Project Plan — BoAt Platform

## Vision

Build an open-source, production-grade automotive simulation and testing platform for deterministic, high-throughput validation across software-in-the-loop, hardware-in-the-loop, and CI/CD pipelines.

## Stakeholders

OEM engineers, Tier-1 suppliers, open-source community contributors, CI/CD automation consumers.

## Milestones

| Period | Milestone | Primary Outcomes |
|---|---|---|
| Months 1-3 | M0 Scaffold | Repository structure, baseline docs, architecture decisions |
| Months 4-6 | M1 Core Sim Engine | Tick scheduler, signal router, deterministic execution kernel |
| Months 7-9 | M2 API Gateway | gRPC services, control plane, streaming interfaces |
| Months 10-12 | M3 Plugin SDK | Stable C ABI, C++ SDK, Python bindings, sample plugins |
| Months 13-15 | M4 Observability | Event/trace persistence, metrics, dashboard integration |
| Months 16-18 | M5 HIL + M6 GA | Hardware integration, stabilization, public release |

## Epics

| Epic | Description | Priority | Milestone |
|---|---|---|---|
| E1: Simulation Core | Tick scheduler, signal router, state machine | P0 | M1 |
| E2: Plugin SDK | C++ SDK, Python bindings, example plugins | P0 | M3 |
| E3: API Gateway | gRPC server, all service implementations | P0 | M2 |
| E4: CLI Tool | boat sim, boat scenario, boat replay, boat plugin commands | P1 | M6 |
| E5: Observability | Event store, trace store, metrics, live streaming | P1 | M4 |
| E6: Replay Engine | Deterministic replay, seek, speed control | P1 | M4 |
| E7: HIL Bridge | HAL, SocketCAN, virtual stubs | P2 | M5 |
| E8: Web Dashboard | Live signal viewer, scenario manager, trace browser | P2 | M6 |
| E9: AI Features | Scenario generation, anomaly detection | P3 | M6 |
| E10: Distributed Sim | Multi-node coordination, HLA bridge | P3 | M6 |

## Functional Requirements

1. Simulation lifecycle: init, run, pause, step, reset, stop
2. Deterministic tick-based execution across repeated runs
3. Plugin loading and unloading at runtime
4. Event recording and deterministic replay
5. Signal injection for scenario manipulation and testing
6. Fault injection for resilience and failure-mode validation
7. Hardware-in-the-loop bridge for external interfaces
8. Multi-scenario batch execution for regression and CI
9. Real-time monitoring dashboard for state, signals, and metrics

## Non-Functional Requirements

- Soft real-time latency: ≤1 ms jitter in simulated mode
- Throughput: ≥1,000,000 events per second
- Zero-copy IPC for payloads larger than 4 KB
- Deterministic replay must be bit-exact
- Plugin isolation with crash containment strategy
- Primary OS target: Linux (Ubuntu 22.04 LTS and 24.04 LTS)
- Portability target: macOS and Windows via abstraction layer

## Constraints

- Core language: C++20
- Bindings language: Python 3.11+
- Build system: CMake 3.24+
- API stack: gRPC 1.60+, Protocol Buffers v3
- Storage: SQLite embedded + optional TimescaleDB for distributed scale
- IPC: Eclipse iceoryx2 for zero-copy shared-memory communication

## UX Concepts

**Web Dashboard:** Signal timeline view, YAML scenario editor with live validation, plugin registry (card-based), simulation control bar (play/pause/step/reset/stop), fault injection panel (timeline drag-and-drop), trace browser (MF4/CSV export).

**CLI:** Predictable command groups (`boat sim`, `boat scenario`, `boat replay`, `boat plugin`), consistent output modes (human-readable tables + JSON), explicit error messages with actionable next steps.

**Accessibility:** Keyboard-first navigation, high-contrast indicators, error states with clear remediation guidance.

## User Flows

**Engineer runs a scenario:** `boat scenario create --file scenario.yaml` → `boat sim start --scenario <id>` → `boat sim watch <id>` → `boat sim stop <id>`

**CI pipeline validates SUT:** GitHub Action triggers → `boat sim run --scenario regression.yaml --assert assertions.yaml` → exit 0/1

**Engineer replays a trace:** `boat replay start --trace <id>` → `boat replay seek --tick 5000` → `boat replay stream`

**Plugin developer integrates:** Implement `boat_plugin_create()` → build `.so` → `boat plugin register --path ./myplugin.so` → `boat plugin list`

## Risk Register

| ID | Risk | Probability | Impact | Mitigation |
|---|---|---|---|---|
| R01 | iceoryx2 API instability | Low | High | Pinned to GIT_TAG v0.4.1 in CMake FetchContent; ShmPublisher/ShmSubscriber wrapper layer in src/ipc/shm/ |
| R02 | Plugin ABI breakage | Medium | High | BOAT_PLUGIN_ABI_VERSION runtime check at dlopen (plugin_manager.cpp:53); mismatched .so rejected before load |
| R03 | Determinism broken by FP differences in plugins | Low | High | Core engine avoids FP entirely (execution_pipeline.md:67-69); plugin devs must use fixed-point or deterministic polynomials; CI determinism job catches regressions |
| R04 | gRPC streaming backpressure under high event rate | Medium | Medium | No flow control implemented; a fast producer can overwhelm slow clients, causing OOM or stream disconnects |
| R05 | SQLite write throughput at high event rates | Medium | High | PRAGMA journal_mode=WAL + synchronous=NORMAL in place; no async batch writer; TimescaleDB fallback not yet implemented |
| R06 | Plugin crash takes down entire gateway | Medium | High | Plugins run in-process with the gateway via dlopen; no process isolation, watchdog, or state snapshot mechanism exists |
| R07 | CAN frame loss under high bus load | Medium | High | SocketCAN socket opened without SO_RCVBUF — uses kernel-default receive buffer; no netlink dropped-frame monitoring |
| R08 | Python gRPC stub drift from proto changes | Medium | High | C++ stubs auto-generated via BoAtProto.cmake in CMake build; Python stubs require manual generate_stubs.sh — no CI check enforces sync |
| R09 | LLM API cost overrun | Low | Low | `boat ai` CLI defaults to local inference (Ollama); cloud backends are opt-in; feature is P3 priority |

## Governance

- Work management via GitHub Issues and Milestones
- RFC process required for breaking changes
- Semantic versioning for all release artifacts and APIs

## Definition of Done

- All acceptance criteria met
- CI pipeline green
- Documentation updated
- Changes peer-reviewed and approved

## Team Roles

Project Manager, Lead Developer, C++/Python Developer, Backend Developer, AI Engineer, DevOps Engineer, Test Manager, Test Engineer, Requirement Engineer, UX/UI Designer.

## Glossary

- **BoAt:** The simulation and testing platform
- **Scenario:** Declarative simulation setup including plugins, signals, timing, and faults
- **Tick:** Discrete deterministic simulation time step
- **Plugin:** Dynamically loaded module implementing simulation behavior
- **Signal:** Typed data stream exchanged between components/plugins
- **Trace:** Persisted event timeline for analysis and replay
- **HIL:** Hardware-in-the-loop — bridging simulation with physical hardware
- **SUT:** System Under Test — the target being validated

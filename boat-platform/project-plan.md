# Project Plan — BoAt Platform

## What this document is

The plan of record: what BoAt is for, what has been built, what has not, and what is
committed next. It is linked from the README's "Learn more", so it is read by people
deciding whether to use or contribute to the project — which means every claim in it has to
be one the tree can back up.

**Status is recorded, not projected.** Where a requirement is unmet, this document says so
and links the issue tracking it. Where a risk has been closed, it says that too. An earlier
revision of this file carried an 18-month schedule, performance numbers nothing had ever measured,
a portability target nothing was working toward, and a risk register whose "mitigation"
column described the unmitigated state; all four are corrected below.

Open work lives in [GitHub Issues](https://github.com/AlexTech-stack/BoAt/issues), grouped by
the milestones in [Roadmap](#roadmap). Longer-form gap analyses and incident write-ups live in
[`backlog/`](../backlog/). The source code is authoritative over all of them.

*Last reconciled against the tree: 2026-10-08, at `v0.1.1`.*

## Vision

An open-source, production-grade automotive simulation and testing platform for
deterministic, high-throughput validation across software-in-the-loop,
hardware-in-the-loop, and CI/CD pipelines.

Determinism is the differentiating claim rather than one feature among many: a replayed
trace puts bit-identical frame content on the wire, in identical order, attributed to
identical ticks, across runs. That is what makes a failure reproducible and therefore
diagnosable, and it is the constraint every design decision is checked against.

## Stakeholders

OEM engineers, Tier-1 suppliers, open-source community contributors, CI/CD automation
consumers.

## Where the project is

Built and in use:

- **`boat_gateway`** — tick-based C++20 simulation gateway, bridging virtual and physical
  CAN/CAN-FD and Ethernet, with a single `TickAuthority` running ordered phases
  (`node_plugins` → `sim_plugins` → `replay`).
- **14 gRPC services** across 16 `.proto` files, with opt-in TLS and mTLS.
- **Plugin ABI v9** — C ABI, 10-field vtable, version checked at `dlopen` with no fallback.
  Five in-tree plugins: `pdu_router`, `can_tp`, `someip`, `tcp`, `probe`.
- **Python SDK (`boat-py`) and CLI (`boat-cli`)** — 12 CLI command groups; node classes
  resolving their gateway the same way everywhere (`address=` > `BOAT_HOST` >
  `localhost:50051`).
- **Replay pipeline** — `.asc`/`.blf`/`.pcap` import, tick-paced streaming, PCAPNG export,
  trace analysis and scoring.
- **Observability** — SQLite event and trace stores, `MetricsService`, an effective-config
  document that is byte-identical across identically configured runs so it can be committed
  next to the trace it produced.
- **Web and desktop surfaces** — 8 FastAPI services in `ui/`, 4 more in `tools/`, and a
  PySide6 `admin_gui` client driving one or more launcher agents.
- **CI** — nine jobs (eleven runs with the matrix): build and test on Ubuntu 22.04 and
  24.04, pytest on 3.11 and 3.13, proto-stub sync, ASan, TSan, coverage, a determinism check
  under CPU oversubscription, Docker build and push, and a vcan HIL smoke test.
- **Releases** — `v0.1.0` and `v0.1.1` tagged; TGZ, DEB and RPM packages built, verified by
  name, version and content, and attached to the release.

Test baseline at `v0.1.1`: **164 C++ cases** (100% passing in debug and release; 7 HIL cases
skipped without `BOAT_HIL_ENABLED=1`) and **544 Python cases** (536 passing, 8 skipped).

Known not to be green: the **TSan job fails 6–7 of 164** — a UDS/IPC concurrency cluster
([#10](https://github.com/AlexTech-stack/BoAt/issues/10)). The failing set is not stable
between runs, so any count is a lower bound. Every other job passes. This is the one thing
standing between the project and a green pipeline, and it is the entry condition for the next
milestone.

## Roadmap

Milestones are versions, and they carry **no dates**. The previous revision of this plan
scheduled six milestones across 18 calendar months; the work it placed in months 16–18 was
complete by month 6, which made every date in the table misleading in both directions at
once. Dates on a project with one principal contributor and no external commitments were
decoration. Ordering and entry conditions are the parts that were doing real work, so those
are what remain.

| Milestone | Means | Entry condition for |
|---|---|---|
| [**v0.2.0** — Green and honest](https://github.com/AlexTech-stack/BoAt/milestone/1) | Every CI job green on `master`; consumer-reported bugs closed; no published claim that is not backed by a test | inviting outside contributors |
| [**v0.3.0** — Measured](https://github.com/AlexTech-stack/BoAt/milestone/2) | The unmeasured non-functional requirements are either verified by a committed benchmark or retired | making any performance claim |
| [**v0.4.0** — Robust under failure](https://github.com/AlexTech-stack/BoAt/milestone/3) | Plugin crash containment, SocketCAN overflow visibility, gRPC backpressure, bus health reporting | third-party plugins and unattended benches |
| [**v1.0.0** — Stable surfaces](https://github.com/AlexTech-stack/BoAt/milestone/4) | A stability commitment for the gRPC API and the C plugin ABI, with a deprecation policy | out-of-tree consumers depending on either |

Historical milestones M0–M6 (scaffold, core engine, API gateway, plugin SDK, observability,
HIL, GA) are all delivered, with the exceptions recorded under [Epics](#epics) and
[Non-functional requirements](#non-functional-requirements).

## Epics

| Epic | Priority | Status |
|---|---|---|
| **E1: Simulation Core** — tick scheduler, signal router, state machine | P0 | **Delivered.** `src/core/`: `tick_scheduler`, `sim_clock`, `signal_router`, `signal_bus`, `sim_state_machine`, `determinism_engine`, `fault_injector`, `scenario_loader` |
| **E2: Plugin SDK** — C++ SDK, Python bindings, example plugins | P0 | **Delivered, except Python bindings.** The C ABI, the C++ SDK and five in-tree plugins exist. There is no Python plugin-authoring path, and the Python SDK that does exist is a gRPC *client* SDK rather than a binding. Whether to build one, or amend the epic, is [#26](https://github.com/AlexTech-stack/BoAt/issues/26) |
| **E3: API Gateway** — gRPC server, service implementations | P0 | **Delivered**, plus TLS/mTLS, which was not in scope |
| **E4: CLI Tool** | P1 | **Delivered, beyond scope.** The epic named four command groups; twelve shipped |
| **E5: Observability** — event store, trace store, metrics, live streaming | P1 | **Partial.** Stores and `MetricsService` are implemented; nothing consumes the metrics, so they are plumbed but invisible ([#28](https://github.com/AlexTech-stack/BoAt/issues/28)) |
| **E6: Replay Engine** — deterministic replay, seek, speed control | P1 | **Delivered, beyond scope.** Tick attribution is asserted bit-identical, which the epic did not require |
| **E7: HIL Bridge** — HAL, SocketCAN, virtual stubs | P2 | **Delivered.** Bus health and RX-overflow visibility are open ([#20](https://github.com/AlexTech-stack/BoAt/issues/20), [#22](https://github.com/AlexTech-stack/BoAt/issues/22)) |
| **E8: Web Dashboard** | P2 | **Partial.** Signal viewing, scenario management, trace browsing and simulation control exist. No fault-injection panel ([#27](https://github.com/AlexTech-stack/BoAt/issues/27)), no metrics panel ([#28](https://github.com/AlexTech-stack/BoAt/issues/28)) |
| **E9: AI Features** — scenario generation, anomaly detection | P3 | **Partial.** `boat ai` generates scenarios, bus setups, CLI invocations and plugin skeletons against a local LLM. Anomaly detection was not built; the timing-anomaly analysis in the SDK's trace analysers is statistical, not what this epic described |
| **E10: Distributed Sim** — multi-node coordination, HLA bridge | P3 | **Not started.** Nothing exists. Keeping, scoping or dropping it is [#30](https://github.com/AlexTech-stack/BoAt/issues/30) |

## Functional Requirements

| | Requirement | Status |
|---|---|---|
| 1 | Simulation lifecycle: init, run, pause, step, reset, stop | Implemented. Two defects: state is gateway-global rather than per-simulation, and a long-lived gateway can stop accepting simulations ([#31](https://github.com/AlexTech-stack/BoAt/issues/31)); reset does not rewind the tick ([#32](https://github.com/AlexTech-stack/BoAt/issues/32)) |
| 2 | Deterministic tick-based execution across repeated runs | Implemented and asserted. `boat_determinism_seed` pins the PRNG; `boat_determinism_replay` drives a trace through the real pipeline twice and compares what reached the wire — content, ordering and tick attribution, all hard-asserted, verified 10/10 under 2× CPU oversubscription |
| 3 | Plugin loading and unloading at runtime | Implemented, with lifetime pinning (`busy_count_`) so `Unload` cannot race a tick or a frame dispatch |
| 4 | Event recording and deterministic replay | Implemented |
| 5 | Signal injection for scenario manipulation and testing | Implemented |
| 6 | Fault injection for resilience and failure-mode validation | **Engine only.** Five fault types, seeded for reproducibility, wired into the signal path, reachable over gRPC — and no CLI, SDK or UI surface, so unreachable in practice ([#27](https://github.com/AlexTech-stack/BoAt/issues/27)) |
| 7 | Hardware-in-the-loop bridge for external interfaces | Implemented |
| 8 | Multi-scenario batch execution for regression and CI | Implemented, differently from the original wording: `boat test run <manifest.json>` runs a manifest of test files as subprocesses against a spawned or existing gateway, emitting JSON, JUnit and HTML reports |
| 9 | Real-time monitoring dashboard for state, signals, and metrics | State and signals: implemented. Metrics: [#28](https://github.com/AlexTech-stack/BoAt/issues/28) |

## Non-Functional Requirements

| Requirement | Status |
|---|---|
| Deterministic replay must be bit-exact | **Met, and exceeded.** See FR2 |
| Zero-copy IPC for payloads larger than 4 KB | **Met.** iceoryx2 shared memory above the 4 KB threshold, UDS below it, with the boundary tested from both sides |
| Primary OS target: Linux (Ubuntu 22.04 LTS and 24.04 LTS) | **Met.** Both in CI |
| Soft real-time latency: ≤1 ms jitter in simulated mode | **Not measured.** No benchmark exists anywhere in the repository ([#18](https://github.com/AlexTech-stack/BoAt/issues/18)) |
| Throughput: ≥1,000,000 events per second | **Not measured.** Same issue |
| Plugin isolation with crash containment strategy | **Not implemented.** No signal handler, no watchdog, no supervisor. All plugins run in-process via `dlopen`, so one `abort()` takes down the gateway, every other plugin, the gRPC server and all simulation state ([#19](https://github.com/AlexTech-stack/BoAt/issues/19)) |
| Portability target: macOS and Windows via abstraction layer | **Not started, and in tension with the architecture.** SocketCAN is a Linux kernel subsystem with no equivalent on either target, so a port would have no CAN transport. The client side — SDK, CLI, web UIs — is already portable, being gRPC only. The requirement needs restating rather than scheduling ([#42](https://github.com/AlexTech-stack/BoAt/issues/42)) |

The two unmeasured numbers were removed from the public-facing documentation during the
release-readiness audit, so they are not being advertised without evidence. They remain here
as requirements, which is the honest place for them — but a requirement nothing can check is
not doing any work, and v0.3.0 exists to resolve them one way or the other. If the real
numbers come out lower, this table gets amended to match them.

## Constraints

| | Target | Actual |
|---|---|---|
| Core language | C++20 | C++20 |
| Bindings language | Python 3.11+ | 3.11 and 3.13 in CI |
| Build system | CMake 3.24+ | 3.24 minimum; configures under CMake 4.x |
| API stack | gRPC 1.60+, Protocol Buffers v3 | gRPC 1.75.0, protobuf 31.1 |
| Storage | SQLite embedded | SQLite with WAL and `synchronous=NORMAL`. Writes are synchronous on the caller's thread ([#23](https://github.com/AlexTech-stack/BoAt/issues/23)) |
| IPC | Eclipse iceoryx2 | v0.4.1, pinned. Pre-1.0 upstream with no API stability promise (R10) |

Dependencies are fetched at build time via CMake `FetchContent`; nothing third-party is
vendored into the tree. A Rust toolchain is required as a build-time-only transitive
dependency of iceoryx2.

**TimescaleDB** appeared in an earlier revision of this section as optional storage for
distributed scale. It was never implemented and is not planned; it is removed here rather
than left as an aspiration, because it had already propagated into a class diagram as a type
that does not exist ([#14](https://github.com/AlexTech-stack/BoAt/issues/14)).

## UX Concepts

**CLI.** Predictable command groups, consistent output modes (human-readable tables and
JSON), explicit errors with actionable next steps. Delivered, across twelve groups.

**Web dashboard.** Signal timeline view, **JSON** scenario editing with validation, plugin
registry, simulation control bar (play/pause/step/reset/stop), trace browser. Delivered.

Two items from the original concept are not built, and one is corrected:

- **Fault injection panel** (timeline drag-and-drop) — not built
  ([#27](https://github.com/AlexTech-stack/BoAt/issues/27)).
- **Metrics display** — not built ([#28](https://github.com/AlexTech-stack/BoAt/issues/28)).
- **Trace export** is **PCAPNG**, not MF4/CSV. `boat replay export` writes CAN/CAN-FD and
  Ethernet frames to separate PCAPNG interfaces in one file on one timeline, which opens in
  Wireshark. The `MF4` and `CSV` values in `TraceRecord::Format` are declared and
  unimplemented ([#29](https://github.com/AlexTech-stack/BoAt/issues/29)); MF4 has a real
  argument behind it for this audience and would need its own scope.

**Accessibility.** Keyboard-first navigation, high-contrast indicators, error states with
remediation guidance. Partially delivered; not audited.

## User Flows

Each of these has been run end to end against a live gateway on `vcan0`.

**Engineer runs a scenario**

```
boat scenario create --file config/scenarios/example.json
boat sim create --scenario <scenario-id>
boat sim start <sim-id>
boat sim watch <sim-id>
boat sim stop <sim-id>
```

The CLI takes scenarios as **JSON**, not YAML, and `sim create` is a separate step from
`sim start`. `config/scenarios/example.json` is a runnable minimal example — it ships
precisely because this flow is the project's front door and previously referenced a file
that did not exist.

**CI pipeline validates a SUT**

```
boat test run config/tests/manifest_can_loopback.json
```

Exit code 0 or 1, with JSON, JUnit and HTML reports. Relative paths inside a manifest or
environment config resolve against that file's own directory, so this works from anywhere.
An earlier revision of this plan showed `boat sim run --scenario regression.yaml --assert
assertions.yaml`; no such command or flag has ever existed.

**Engineer replays a trace**

```
boat replay import <trace.asc> --trace-id <id>
boat replay start --trace <id>
boat replay seek --replay-id <replay-id> --tick 5000
boat replay stream --trace <id> --speed accelerated --multiplier 2.0 --buses vcan0
```

`boat trace replay <trace.asc> --buses vcan0` is the direct, CAN-only, client-paced
alternative with no import step.

**Plugin developer integrates**

```
# implement boat_plugin_create() against ABI v9, build as a .so
boat plugin register --path ./myplugin.so
boat plugin list
```

A plugin reporting an ABI version other than 9 is rejected at load with a clear error. There
are no fallbacks, so the versioning rule matters to plugin authors — and it is not yet
written down ([#24](https://github.com/AlexTech-stack/BoAt/issues/24)).

## Risk Register

The previous revision of this table had a "Mitigation" column that described, in detail, the
*unmitigated* state of each risk. It was an accurate risk analysis mislabelled as a
mitigation plan, and it let three risks that had actually been closed sit there reading as
live. The column is now **Status**, and it says what is true.

| ID | Risk | Status |
|---|---|---|
| R01 | Plugin crash takes down the entire gateway | **Open, unmitigated.** All plugins run in-process via `dlopen`. No process isolation, no watchdog, no state recovery, no crash attribution. Rises in severity the moment out-of-tree plugins exist → [#19](https://github.com/AlexTech-stack/BoAt/issues/19) |
| R02 | Tick ordering divergence between dual PluginManagers | **Closed.** Both managers are now ordered phases of one `TickAuthority` (`node_plugins` → `sim_plugins` → `replay`). The two managers still exist, but the split is about plugin *lifetime*, not timing. This is what made hard tick-attribution assertions possible |
| R03 | CAN frame loss under high bus load | **Open, unmitigated.** Kernel-default `SO_RCVBUF`, no overflow detection, no dropped-frame counters, no backpressure. Frames are dropped silently → [#20](https://github.com/AlexTech-stack/BoAt/issues/20) |
| R04 | Python gRPC stub drift from proto changes | **Closed.** The `proto-stubs-in-sync` CI job regenerates the stubs and fails if the committed output differs |
| R05 | Single-process gateway as a SPOF | **Open, accepted for now.** The same architectural fact as R01, seen from the availability side. No health check, no crash recovery, no hot restart. A supervisor is one of the options under [#19](https://github.com/AlexTech-stack/BoAt/issues/19) |
| R06 | gRPC streaming backpressure under high event rate | **Open, unmitigated.** No flow control, no per-client buffer limits. A fast producer can grow server memory without bound or drop the stream with no diagnostic → [#21](https://github.com/AlexTech-stack/BoAt/issues/21) |
| R07 | SQLite write throughput at high event rates | **Open, partially addressed.** WAL and `synchronous=NORMAL` are set; every insert is still synchronous on the caller's thread, which can delay a tick → [#23](https://github.com/AlexTech-stack/BoAt/issues/23) |
| R08 | No TLS on the gRPC port | **Closed as a default-posture risk, documented as a deployment choice.** Opt-in TLS and mTLS exist on both sides through one `make_channel()` helper, with misconfiguration raising rather than falling back to plaintext. The default is still unauthenticated plaintext, which `SECURITY.md` states plainly |
| R09 | Determinism broken by floating point in plugins | **Open, unmitigated.** The core avoids FP; nothing stops a plugin using it. No `-ffloat-store` or `-frounding-math` enforced. ABI v9's host-supplied time source removes the larger version of this hazard — a plugin reading its own clock — but not FP itself |
| R10 | iceoryx2 API instability on upgrade | **Open, accepted.** Pinned to v0.4.1. The `ShmPublisher`/`ShmSubscriber` wrappers limit the blast radius. Upstream is pre-1.0 with no C++ API stability promise |
| R11 | Plugin ABI breakage | **Open, by design.** ABI v9 is checked at `dlopen` (`plugin_manager.cpp:113`) and a mismatch is rejected outright. This prevents silent breakage at the cost of a hard error with no fallback. What is missing is the *policy* — how ABI changes are proposed and announced → [#15](https://github.com/AlexTech-stack/BoAt/issues/15), [#24](https://github.com/AlexTech-stack/BoAt/issues/24) |
| R12 | vcan-only CI misses hardware-specific failures | **Open, accepted.** CI runs on `vcan0`. Bus-off recovery, error frames, arbitration timing and cable faults are never exercised. HIL tests need a self-hosted runner with physical hardware. [#22](https://github.com/AlexTech-stack/BoAt/issues/22) would at least make the controller state visible when hardware *is* attached |
| R13 | Concurrency defects found only under sanitizers and only under load | **Open.** TSan fails 6–7 of 164, concentrated in UDS/IPC, and the failing set varies between runs on identical code. Four diagnoses so far: three found and fixed a real defect and were each followed by more reports, and the fourth — that the unguarded `client_threads_` vector was the place to start — was undercut by a *single-client* test joining the failures. The signal is that the remaining work is a synchronisation design rather than a fifth point fix → [#10](https://github.com/AlexTech-stack/BoAt/issues/10) |
| R14 | Untested surfaces carry undetected defects | **Open, demonstrated.** `admin_gui/`, `tools/`, `nodes/` and `demo/` have no tests. `tools/dbc2boatjson.py` has a consumer-reported correctness bug that produces silently wrong signal ranges — an untested converter, with a bug, found by someone else → [#11](https://github.com/AlexTech-stack/BoAt/issues/11), [#17](https://github.com/AlexTech-stack/BoAt/issues/17) |

R13 and R14 are new. Both describe failure modes the project has already experienced rather
than anticipated ones, which is the main thing an honest register should be adding over time.

## Governance

- **Work management via [GitHub Issues](https://github.com/AlexTech-stack/BoAt/issues) and
  [Milestones](https://github.com/AlexTech-stack/BoAt/milestones).** Issues are labelled by
  area (`area:*`), kind (`bug`, `enhancement`, `autosar-gap`, `concurrency`, `performance`,
  `test-coverage`, `governance`, `documentation`) and priority (`P0`–`P3`).
  `good first issue` marks the ones that are self-contained and well specified.
- **Longer-form analyses stay in [`backlog/`](../backlog/)** — gap analyses, incident
  write-ups and design notes too large for an issue body. Issues link to them; they are the
  evidence, not the queue.
- **Semantic versioning** for release artifacts and APIs. `CHANGELOG.md` follows Keep a
  Changelog, with breaking changes marked.
- **RFC process for breaking changes** — required by this plan and
  **not yet written** ([#15](https://github.com/AlexTech-stack/BoAt/issues/15)). The gap
  matters most for the plugin ABI, which breaks hard rather than degrading.
- **The dual-file rule.** `CLAUDE.md` and `AGENTS.md` describe the same repository for
  different audiences and must never disagree; a fact changed in one has to land in the
  other. The PR template reminds contributors of this.

## Definition of Done

- All acceptance criteria met
- CI pipeline green — **currently not true on `master`**
  ([#10](https://github.com/AlexTech-stack/BoAt/issues/10)); this is the v0.2.0 entry
  condition and the one item that makes the definition presently aspirational
- Documentation updated, including both agent files where a shared fact changed
- Changes peer-reviewed and approved

## Roles the work covers

Project management, C++ and Python development, backend, AI, DevOps, test management and
engineering, requirements, UX/UI.

This is a list of **disciplines the work has required**, not a staffed team. The project has
one principal contributor. An earlier revision listed these as "Team Roles", which read as a
ten-person organisation and would have given a prospective contributor a false impression of
how much capacity is behind the backlog.

## Glossary

- **BoAt** — the simulation and testing platform. A name, not an acronym.
- **Scenario** — declarative simulation setup: plugins, signals, timing, faults. JSON.
- **Tick** — the discrete deterministic simulation time step; also the minimum cycle time.
- **Tick authority** — the single owner of tick advancement, running ordered phases. Nothing
  else in the system owns a clock; that is what makes tick attribution reproducible.
- **Plugin** — a dynamically loaded `.so` implementing the C ABI (currently v9) and running
  in-process, on the tick.
- **Node** — a script or program driving the gateway from *outside*, over gRPC. Not on the
  tick, and so cannot affect determinism.
- **Signal** — a typed data stream exchanged between components and plugins.
- **Frame** — the unified bus message type, covering `can`, `canfd`, `eth`, `tcp` and `pdu`.
- **Trace** — a persisted event timeline for analysis and replay.
- **HIL** — hardware-in-the-loop: bridging simulation with physical hardware.
- **SUT** — system under test: the target being validated.

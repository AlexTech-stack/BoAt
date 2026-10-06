# Changelog

All notable changes to BoAt are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims at
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) — with the pre-1.0 caveat that a
**minor** bump may contain a breaking change. Breaking changes are marked **BREAKING** and carry
a `!` in their commit subject.

The plugin ABI is versioned separately from the project; see
[CONTRIBUTING.md](CONTRIBUTING.md#versioning-and-stability).

## [Unreleased]

Nothing yet.

## [0.1.0] — 2026-10-06

First tagged release. The project has been developed since 2026-03-30 without a changelog, so
this entry describes the state at the tag rather than replaying ~320 commits; `git log` is the
record for anything before it.

### Added

- **Tick-based simulation gateway** (`boat_gateway`, C++20): bridges virtual and physical
  CAN / CAN-FD / Ethernet networks, with `vcan*` interfaces driven by `VirtualCanDriver` and
  physical ones by `PhysicalCanDriver` (PEAK PCAN, Kvaser, gs_usb via SocketCAN), plus virtual
  Ethernet over UDP multicast and physical Ethernet over `AF_PACKET`.
- **gRPC API**: 14 services across 16 `.proto` files, 64 RPCs — Simulation, Signal, Scenario,
  Replay, Fault, Metrics, Trace, Frame, CanTp, Pdu, Plugin, NodePlugin, Debug, Bus.
- **`FrameService`**: one send / subscribe / stream / list-interfaces surface for every bus type
  (`can`, `canfd`, `eth`, `tcp`, `pdu`).
- **Plugin SDK**, C ABI **v9**, built on a single unified `BoatFrame`. A plugin reporting a
  different ABI version is rejected at load with a clear error — there are no compatibility
  shims. Five built-in plugins: `pdu_router`, `can_tp` (ISO 15765-2), `tcp`, `someip`, `probe`.
- **`TickAuthority`**: one clock running ordered phases (`node_plugins` → `sim_plugins` →
  `replay`) every tick, replacing three independent time domains.
- **Replay pipeline**: imports `.asc` / `.blf` / `.pcap` / `.pcapng` to an internal
  length-delimited `boat.v1.Frame` format, and replays through the single `FrameSink`. Records
  come due on elapsed *ticks* scaled by a speed multiplier, never on a wall clock. Interface and
  MAC targeting is a replay-time decision, so one imported trace replays against different
  hardware without re-importing.
- **PDU routing** in the `pdu_router` plugin: I-PDU groups, cyclic / on-change / mixed
  transmission schedules, IpduM containers, deadline monitoring, and COM signal packing
  (Intel/Motorola, AUTOSAR E2E CRC).
- **Python SDK (`boat-py`) and CLI (`boat-cli`)**: `BoAtClient`, `FrameNode`, `PduNode`,
  `BusNode`, `PduMessageNode`; `boat` subcommands for `sim`, `scenario`, `replay`, `plugin`,
  `can-tp`, `frame`, `pdu`, `db`, `test`, `trace`, `config`, `ai`. Every node class and the CLI
  resolve the gateway address the same way: explicit argument > `BOAT_HOST` > `localhost:50051`.
- **TLS and mTLS**, opt-in on both sides. Server: `BOAT_TLS_CERT` + `BOAT_TLS_KEY`, with
  `BOAT_TLS_CLIENT_CA` additionally requiring client certificates. Client: `BOAT_TLS_CA`,
  `BOAT_TLS=1`, `BOAT_TLS_CLIENT_CERT` + `BOAT_TLS_CLIENT_KEY`, `BOAT_TLS_SERVER_NAME`, all read
  by `TlsConfig.from_env()`. Misconfiguration raises `TlsConfigError` rather than silently
  falling back to plaintext.
- **Effective configuration**: env resolution happens once into `EffectiveConfig`, recording the
  values used, which variable decided the tick, whether each interface opened, each plugin's
  verbatim config and load result, and a warnings list. Written to `BOAT_CONFIG_DUMP` at startup
  and served by `DebugService.GetEffectiveConfig` / `boat config show`. It carries no timestamp
  or hostname, so two identically configured runs emit byte-identical JSON and can be diffed.
- **HIL suite runner** (`boat test run <manifest>`): an `EnvironmentConfig` plus a
  `ManifestConfig` drive `TestSuiteRunner`, which starts or attaches to a gateway, runs each test
  file as a subprocess with a timeout, and writes JSON / JUnit / HTML / optional Allure reports.
- **Trace analysis and reverse engineering**: `boat trace score` rates a trace's information
  value for RE, with AUTOSAR E2E Data-ID lists, SUM8/XOR8 checksum detection and multiplexor
  detection.
- **12 FastAPI services** — 8 under `ui/` (launcher, dashboard, commander, control_panel,
  recorder, debug, system_dashboard, launcher_agent) and 4 under `tools/` (pdu_editor,
  trace_analyzer, trace_editor, eth_trace_analyzer) — plus `admin_gui/`, a PySide6 desktop
  client for one or more launcher agents.
- **Packaging**: `cpack -G "TGZ;DEB;RPM"`, and multi-arch Docker images.
- `config/scenarios/example.json`, a runnable minimal scenario, so the documented
  `scenario create` → `sim create` → `start` flow can actually be followed.

### Changed

- **BREAKING** — Plugin ABI **v9** hands each plugin the host clock via `set_time_source`
  (`BoatNowNsFn`, monotonic nanoseconds, real or virtual under `BOAT_TIME_SOURCE=virtual`). A
  plugin that needs time must read it there instead of calling `steady_clock::now()`. CAN-TP, TCP
  and the PDU router were moved onto it.
- **BREAKING** — One `BoatFrame` replaces the separate `BoatCanFrame` / `BoatEthFrame` types,
  covering all bus types with a `bus_type` discriminator and per-bus metadata.
- **BREAKING** — The `PduRouter` is a runtime plugin (`pdu_router.so`), not gateway-core logic.
  PDU gRPC calls are delegated to it.
- Replay no longer owns a thread or a clock; it is an ordered phase of the `TickAuthority`.
- The simulation scheduler was folded into the `TickAuthority`, so the two `PluginManager`
  instances now differ only in **lifetime**, not timing.
- Core owns stateless transport, plugins own stateful conversations. The single `FrameSink` is
  the only path a frame reaches a bus, and the registry send path is the single site that tags
  locally-sent frames (`BOAT_CAN_FLAG_SELF_SENT` / `BOAT_ETH_FLAG_SELF_SENT`).

### Removed

- **BREAKING** — `CanService` and `EthernetService`, and their protos, C++ implementations,
  gateway registrations and generated stubs. `FrameService` carries all six of their RPCs;
  `FrameService.ListInterfaces` replaced `CanService.ListBuses` + `EthernetService.ListInterfaces`.
- **BREAKING** — The `CanNode` and `EthernetNode` SDK classes. Use `FrameNode`, which is composed
  rather than subclassed.
- **BREAKING** — The `boat can` and `boat eth` CLI commands, removed outright rather than left as
  deprecated wrappers. Use `boat frame`. (Unrelated and still present: `python3 -m boat can send`,
  a separate one-shot PDU-database-driven dispatcher.)
- **BREAKING** — `FrameNode.send_tcp()`: `FrameService.SendFrame` rejects a TCP frame with
  `UNIMPLEMENTED` by design, so the method could only ever raise. Drive TCP through the `tcp.so`
  plugin's connection API.
- The `FrameForwarder` plugin and the `can_io` direct-SocketCAN alternative — the core
  `FrameSink` is the one path to the wire.
- The unused C++ COM signal library.

### Fixed

- A plugin-lifetime use-after-free: `Unload()` is reachable from gRPC and can race the tick
  thread. `TickAll` / `DispatchFrame` now pin `PluginManager::busy_count_` atomically with their
  snapshot, and `Unload()` waits for it to drain before `destroy_fn` + `dlclose`.
- Replay pacing scaled with the node tick interval rather than being independent of it.
- Signed and Motorola (big-endian) signal packing.
- A scheduled PDU route transmitting off-schedule when `send()` was called — `send()` on a
  scheduled route now only updates the payload, and the schedule decides wire timing.
- `BOAT_HOST` was not honoured everywhere a gateway address is resolved.
- The gateway now refuses to start if its gRPC port is taken, rather than silently sharing it via
  `SO_REUSEPORT`.
- Determinism under load: frame content, ordering **and** tick attribution are now asserted
  bit-identical across replay runs with a plain `REQUIRE`, verified 10/10 under 2× CPU
  oversubscription — the load that previously failed 0/6.
- `ctest` reported 27 failures out of 184 on a clean checkout, none of them real: 22 phantom
  tests registered by gRPC's bundled re2 and zlib, and 5 Catch2 `SKIP()`s counted as failures.
  Now 164/164 pass. A HIL test that hung indefinitely was unpassable by construction and has
  been replaced.
- CI had never run: the workflows lived in `boat-platform/.github/workflows/`, which GitHub does
  not read. They are now at the repository root, and two gates that matched zero tests while
  reporting success have been corrected.
- `boat test run <manifest>` only worked from `boat-platform/`; relative paths in a manifest or
  environment config now resolve against that file's own directory, and an unfindable
  `gateway.binary` raises instead of silently starting no gateway.

[Unreleased]: https://github.com/AlexTech-stack/BoAt/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/AlexTech-stack/BoAt/releases/tag/v0.1.0

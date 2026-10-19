# Changelog

All notable changes to BoAt are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims at
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) — with the pre-1.0 caveat that a
**minor** bump may contain a breaking change. Breaking changes are marked **BREAKING** and carry
a `!` in their commit subject.

The plugin ABI is versioned separately from the project; see
[CONTRIBUTING.md](CONTRIBUTING.md#versioning-and-stability).

## [Unreleased]

### Added

- **Plugin crash attribution (#19) — attribution, not containment.** The gateway installs a
  fatal-signal handler (`SIGSEGV`/`SIGBUS`/`SIGILL`/`SIGFPE`/`SIGABRT`) at startup. When a
  plugin crashes, the gateway now writes `[Gateway] FATAL: plugin '<so>' (key '<key>') via=pc|stack|callback`
  to stderr, plus which callback the thread was in (`load`, `on_tick`, `on_frame`, `destroy`)
  and a backtrace. This covers `abort()` and exceptions escaping a callback, faults in host
  code a plugin called into, and faults on threads the plugin started itself. The handler then
  chains to whatever disposition was there before, so the process still dies by the original
  signal: exit status, core dumps and sanitizer reports are unchanged. A crashing plugin
  **still takes the gateway down**; containment is still open under #19. No ABI, proto or CLI
  change. `src/core/plugin/crash_attribution.{h,cpp}`.
- **CAN bus health is observable.** `SocketCanDriver` requests a larger `SO_RCVBUF`
  (`BOAT_CAN_RCVBUF`, default 4 MiB, `0` = kernel default) and says so when the kernel capped
  it; RX drop counts (`SO_RXQ_OVFL`, device-level `rx_dropped`/`rx_errors` from netlink) and
  error frames — bus-off, error-passive, error-warning, restarted — are counted instead of
  being dropped silently, and controller state plus TEC/REC come from netlink. All of it is
  exposed as `can.<iface>.*` points through `MetricsService`, and the requested buffer is
  recorded in the effective-config artifact. A bus that has gone bus-off no longer looks
  identical to one that is merely idle.
- **`tools/gen_proto_docs.py`** generates the proto file/service/RPC tables in
  `docs/api/protobuf-definitions.md` from `proto/boat/v1/`; a `doc-facts-in-sync` CI job fails
  when the committed document is stale. The hand-typed totals are no longer maintained
  anywhere in the docs.
- **gRPC streaming is bounded, and its losses are visible.** Every streaming RPC hands each
  subscriber a bounded drop-oldest queue (`BOAT_STREAM_QUEUE_CAPACITY`, default 4096, max
  1,000,000, recorded in the effective-config artifact) instead of buffering a slow client's
  backlog without limit; the metrics stream conflates latest-value-per-name instead. Overflow
  is cumulative and reported as `grpc.<stream>.dropped` points through `MetricsService` (zeros
  included, so "no drops" and "does not report" are distinguishable) with a rate-limited
  stderr line on the first and every 1000th drop. `ReplayController`'s StreamReplay queue is
  bounded the same way — it had been fed on every replayed frame even with no subscriber
  attached. Producers are never blocked for backpressure: they run on bus-dispatch threads,
  so a slow client must not stall hardware RX for everyone else. Lossless capture belongs to
  the gateway's own trace recording, not a client stream.
- **arm64 (aarch64) release packages.** Releases now ship `.tar.gz`, `.deb` and `.rpm` for
  both amd64 and arm64 (Raspberry Pi OS 64-bit, Jetson, Graviton, NXP i.MX8/S32G-class
  boards), not only an image.

### Fixed

- **The launcher agent fails closed on an empty bind and guards browser request paths**
  (#39). `BOAT_AGENT_BIND=""` is treated as unset (loopback) instead of uvicorn's
  all-interfaces, loopback detection uses `ipaddress`, and requests are rejected when the
  `Host` header is not an IP literal/local name/`BOAT_AGENT_ALLOWED_HOSTS` or when a mutating
  request carries a non-loopback browser `Origin` (DNS-rebinding/CSRF defence).
  `external:<pid>` stop now requires a positive pid whose `/proc/<pid>/comm` is
  `boat_gateway`; previously `external:-1` signalled every process the user owned.
- **Documentation described storage that never existed** (#14). `docs/diagrams/class-diagrams.md`
  drew a `TimescaleEventStore` class and an `ISignalRouter`/`LocalSignalRouter`/
  `DistributedSignalRouter` hierarchy — none of those types exist anywhere in the tree — and
  `docs/database/database-design.md` listed TimescaleDB, Prometheus remote write and an async
  event-store writer thread that were never implemented. The diagram now shows the single
  `SignalRouter` and `SqliteEventStore` that do exist, and the database document says what is
  real (SQLite, synchronous writes — [#23](https://github.com/AlexTech-stack/BoAt/issues/23)).
- **`FetchContent_Populate` was used in the form CMP0169 deprecates** (#16), which CMake 3.30+
  warns about and a future version will remove entirely. gRPC, iceoryx2 and sqlite3 are now
  populated through `boat_populate()`, a wrapper around the fully-specified form that keeps
  the `_deps` layout, the patch-before-`add_subdirectory` flows and the
  `FETCHCONTENT_SOURCE_DIR_<NAME>` override working. Verified under CMake 4.4.4: the old file
  warns, the new one configures cleanly from both a fresh download and a local source tree.
- **JUnit reports were empty for script tests, even when they failed** (#50). The generator
  built `<testcase>` elements only from recorded steps; a script test (a subprocess whose exit
  code is the verdict) has none, so a failed run produced `tests="0" failures="0"` — a CI
  JUnit reader saw green for a script that exited 1. A script test is now one `<testcase>`
  named after the test, with the failure element carrying the reason (`script exited with
  code N`, a timeout, or a missing file), and the same reason is stored as `summary` in
  `report.json`.
- **Extended CAN identifiers below `0x800` could not round-trip** (#34). Frame format was
  inferred from `can_id > 0x7FF`, but the 29-bit range includes small values: an imported
  extended frame with ID `0x123` replayed as a *standard* `0x123` — same number, different
  frame on the wire. Format is now an explicit flag (`CanMetadata.flags` bit `0x10` /
  `BOAT_CAN_FLAG_EXTENDED`): the trace importer sets it from the source file's IDE,
  `SocketCanDriver` sets it from `CAN_EFF_FLAG` on RX and honours it on TX (the old
  magnitude rule remains only as a fallback for producers that never set it), and
  `boat replay export` writes `CAN_EFF_FLAG` back.
- **`ScenarioDef.seed` was parsed and then ignored** (#53) — every scenario ran on the
  gateway's fixed seed, so two scenarios asking for different random streams got the same
  one. `DeterminismEngine::SetSeed()` is applied at `StartSimulation` and restored by
  `ResetSimulation`, so a reset run replays the scenario's seed exactly.
- **`SignalRouter`'s ring overflow was counted and then never read** (#55). Its per-
  subscription 4096-slot ring drops the newest event when full and the count died with the
  subscription, so signal loss upstream of every stream was invisible. There is now an
  aggregate `DroppedEventCount()`, reported with the other overflow counts as the
  `signal_router.dropped` metric point.
- **The `linux/arm64` container image contained an x86-64 `boat_gateway`** (#51) and failed
  with `exec format error` on every arm64 host. CI built the binary once on an x86 runner and
  `Dockerfile.runtime` COPYed that one file into both platforms. Binaries are now built
  natively per architecture (arm64 on an arm64 runner) and the Dockerfile copies
  `dist/<TARGETARCH>/boat_gateway`, so a missing platform fails the build instead of
  shipping. **Every previously published arm64 image is affected**, including `v0.1.1`,
  `latest` and all `ci-*` tags — pull the next release on arm64.
- **Release binaries did not run on older or non-Ubuntu distributions.** Built on an Ubuntu
  22.04 runner, they required glibc 2.35, so they failed to load on RHEL/Rocky/Alma 8 and 9,
  Debian 11 and Ubuntu 20.04. They are now built in an AlmaLinux 8 container (glibc 2.28,
  `Dockerfile.build`) and run on Debian 10+, Ubuntu 20.04+, RHEL-family 8+, Fedora and
  openSUSE; CI starts the gateway on ten such images before publishing.
- **The RPM could not be installed, and every package carried ~1080 stray files.** protobuf's
  install rules (and through them Abseil's and utf8_range's) ran into BoAt's install tree, so
  all three packages shipped protobuf/absl/upb headers, static libraries, CMake configs,
  `.pc` files and `/usr/bin/protoc` (which collides with a distro `protobuf-compiler`) — and
  rpmbuild turned the `.pc` files into ~200 `Requires: pkgconfig(absl_*)` that no repository
  provides. Those installs are now disabled; a package holds the gateway, the five plugins and
  `boat/plugin.h`, and the release job fails on anything else.
- **Plugin install path is fixed at `/usr/lib/boat/plugins`** regardless of the build host's
  libdir, so it does not move to `lib64` now that packages are built on AlmaLinux.
- **The `.deb` declared unversioned dependencies** (`libc6`, `libstdc++6`), so apt would
  install it where the binary could not load. It now requires `libc6 (>= 2.28)`,
  `libstdc++6 (>= 8)` and `libacl1`.
- **The build failed on glibc < 2.34** with `undefined reference to pthread_create` /
  `dlopen`: targets using threads or `dlopen` relied on those living in libc, which is only
  true since glibc 2.34. `boat_core`, every plugin and the bundled sqlite3 now link
  `Threads::Threads` / `${CMAKE_DL_LIBS}` explicitly.

- **Re-configuring an existing build dir failed after a dependency bump** with
  `Failed to unstash changes in .../_deps/grpc-src`. `CMakeLists.txt` patches files inside
  FetchContent's git checkouts, and the update step stashes those edits and pops them back
  after checking out the new tag — which conflicts once the tag changes a patched file (the
  gRPC v1.65 → v1.75 bump did). Patched files are now reset to upstream before FetchContent
  updates and re-patched afterwards. The gRPC `cmake/protobuf.cmake` patch is removed: v1.75
  no longer contains the line it replaced, so it had silently become a no-op.
- **Streaming subscriptions could write into a dead RPC handler** (use-after-free). Producers
  snapshot their subscriber callbacks and invoke them after releasing the lock, and
  `Unsubscribe` does not wait for a callback already in flight, so a callback racing a
  handler's return wrote into captured stack locals — including, in `FrameService`, the gRPC
  writer itself. `PduService.SubscribePdus`, `FrameService.SubscribeFrames`/`StreamFrames` and
  `MetricsService.StreamMetrics` now use a heap-owned subscriber queue captured by value; the
  bidirectional stream writes from a dedicated writer thread that is joined before the handler
  returns. `BusService.Subscribe`, `SignalService.SubscribeSignals` and
  `DebugService.StreamEvents` were still on the stack-local pattern — the debug stream even
  captured its filter string by reference — and now share the same queue.
- **`ResetSimulation` now means reset, and one simulation is active at a time.** It stopped
  the scheduler and reported `IDLE` without touching the clock, so a reset simulation kept
  its tick and a definition created afterwards inherited it — a long-lived gateway could even
  refuse to start anything with `INTERNAL: tick must be monotonically increasing`.
  `TickScheduler::Reset()` rewinds the clock and clears the determinism engine's tick memory
  under the same lock the tick pipeline uses; `ResetSimulation` additionally tears down the
  scenario's registry subscriptions and plugins and drops scheduled faults and queued events,
  and it now works from RUNNING/PAUSED instead of failing. `CreateSimulation` starts a fresh
  definition at tick 0 when no session is live, and `StartSimulation` on a *different*
  definition while one is active is refused rather than silently driving the shared session.

### Changed

- **Configuring the C++ project on a non-Linux host now fails immediately** with a message
  pointing at the supported-platforms docs. It could never build there anyway (SocketCAN,
  `AF_PACKET`, `timerfd`, `dlopen`), but used to fail much later on a missing system header.
- **BREAKING: `ui/launcher_agent.py` binds `127.0.0.1` by default.** Reaching the agent's API
  is equivalent to remote process execution on its host (it spawns gateways and node scripts
  from caller-supplied paths), so that is no longer the default. A federated admin setup that
  administers this host from another machine must start the agent with
  `BOAT_AGENT_BIND=0.0.0.0`, on a trusted network; `SECURITY.md` names the exposure.

### Documentation

- **Platform support is now stated explicitly: the gateway is Linux-only, the clients are
  OS-agnostic.** New "Supported platforms" section in `boat-platform/README.md`, summarized in
  the root README. Removed `project-plan.md`'s "Portability target: macOS and Windows", which
  was never true, and the Windows/MSVC scenario in `docs/execution_pipeline.md`.
  `admin_gui/README.md` now says the hosts it administers must be Linux.
- Project status is stated once (pre-1.0) in the README and referenced from `CLAUDE.md` and
  `AGENTS.md`, instead of three files disagreeing; the stale "v8 ABI" references are corrected
  and `guidebook.html`'s callout updated.
- The RFC process `project-plan.md` required now exists: what counts as breaking per surface,
  an issue template, and the acceptance/notice rules, linked from `CONTRIBUTING.md`.
- `frame.proto` documents that `SELF_SENT` marks the transmitting gateway, not the originating
  client — a bridge cannot use it to suppress only its own echo.

## [0.1.1] — 2026-10-08

Packaging fix. No change to the gateway, the SDK, the CLI or the plugin ABI — if you are not
installing from a `.deb`/`.rpm`/`.tar.gz`, `v0.1.0` and `v0.1.1` are the same software.

### Fixed

- **The `v0.1.0` release packages were not BoAt.** `cpack` produced
  `c-ares_1.34.5-1_amd64.deb`, `c-ares-1.34.5-1.src.rpm`, and an 8.6 GB
  `boat-platform-0.1.0-Source.tar.gz` of the entire working tree including `build/`.
  `include(CPack)` writes `CPackConfig.cmake` into the shared top-level binary directory, so the
  last `include(CPack)` wins; `cmake/Packaging.cmake` was included before the dependencies, and
  gRPC's bundled c-ares calls `include(CPack)` itself and clobbered it. `include(Packaging)` now
  runs last, the version fields are set explicitly, `CPACK_PACKAGE_FILE_NAME` is pinned, binaries
  are stripped, and the source generator can no longer sweep up `build/`.
  `v0.1.0`'s release page carried no assets, so nothing incorrect was ever downloadable from it.
- **Release packages are now attached to the GitHub release.** They were only being uploaded as
  workflow artifacts — behind a login, expiring with retention, and absent from the release page.
- The release job verified its output with `ls build/release/*.deb`, which passed on a `.deb`
  that was not ours. It now checks by name and content: one of each `boat-platform-*` artifact,
  no `-Source` tarball, `dpkg-deb -f` reporting `Package: boat-platform` at the tag's version,
  `bin/boat_gateway` present inside the `.deb`, and a size ceiling.

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

- Bumped gRPC v1.65.0 → **v1.75.0** and Protocol Buffers v27.3 → **v31.1**, so the project
  configures under **CMake 4.x**. gRPC's bundled c-ares declared
  `cmake_minimum_required(VERSION 3.1.0)` up to and including v1.74.1, and CMake 4.0 removed
  compatibility with minimums below 3.5 — so a clean checkout could not configure on any CMake
  4 host. v1.75.0 is the first release whose c-ares uses `3.5.0...3.10.0`. The two move together
  because `gRPC_PROTOBUF_PROVIDER "package"` points gRPC at the separately fetched protobuf,
  and v1.75.0 pins v31.1.

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

[Unreleased]: https://github.com/AlexTech-stack/BoAt/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/AlexTech-stack/BoAt/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/AlexTech-stack/BoAt/releases/tag/v0.1.0

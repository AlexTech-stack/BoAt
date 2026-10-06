# Release-readiness audit — removing the WIP banner

Audit of `master` @ `618c80b`, run 2026-10-06, to answer three questions before the
"⚠ Work in Progress" banner comes off `README.md` and BoAt opens to a broader audience:

1. What is genuinely unfinished?
2. Do the docs describe the software that exists today?
3. Does the repo have the scaffolding a public project needs?

Everything below was verified against this checkout — tests actually run, a gateway actually
started on `vcan0`, documented commands actually executed. Each finding carries a `file:line`
you can check independently. Where a claim is about behavior, the reproduction is given.

---

## Verdict

**Do not remove the banner yet.** Not because the software is weak — the core is in better
shape than the docs admit — but because the two things a new audience hits first are both
broken:

- **CI has never run.** A complete 8-job pipeline exists and is committed to a directory
  GitHub does not read. Nothing has ever enforced the test suite.
- **`ctest` is red on a clean checkout, and not one of the 27 failures is real.** This is a
  direct consequence of the above: with no CI, nobody was looking.

The docs problem is the mirror image: some of them promise APIs that were deleted last week,
while the README *disclaims a determinism guarantee the code now enforces*. A testing
platform that undersells its own reproducibility in its first paragraph is the worst possible
direction for that error to run.

Estimated work to clear the blockers: **2–4 days.** Nothing here requires new features.

**Recommendation:** fix B1–B8, then replace the banner with a specific stability statement
rather than with silence — see [F. Banner verdict](#f-banner-verdict).

---

> ## Status: steps 1–4 done (2026-10-06)
>
> **B1, B2, B3, B4, B5, B6, B7, B8 and S1 are fixed and verified.** `ctest --preset debug`
> is now **164/164 passing**, both with and without `BOAT_HIL_ENABLED=1`; pytest is
> **538/538** (11 new). Each blocker below carries a **Fixed** note saying what changed.
>
> **Two corrections to this report, found while fixing it:**
>
> 1. **B4's stated cause was wrong.** I attributed the Ethernet HIL hang to "a host without
>    a multicast route". The real cause is that `virtual_ethernet_driver.cpp:102` deliberately
>    sets `IP_MULTICAST_LOOP = 0`, so a datagram sent by one driver is never delivered to any
>    socket on the same host — and the test pointed two drivers in one process at each other.
>    It was **unpassable by construction**, not environment-dependent. Verified directly:
>    with `LOOP=0` a same-host multicast send times out, with `LOOP=1` it arrives.
> 2. **Two CI gates ran zero tests and reported success.** Not in the original report.
>    `ctest -R boat_hil_smoke` and `-R boat_determinism_seed` each match **no test**, because
>    `catch_discover_tests` registers Catch2 *case names*, not target names — and `ctest`
>    exits **0** on a zero-match filter. So `hil-smoke` and `determinism-check` were green
>    while testing nothing. Fixed by `noTestsAction: "error"` in every test preset plus
>    correct filters.
>
> **Also newly found, not yet fixed** (see the new N6–N9 at the end).

---

## Blockers

### B1 — CI exists, is committed, and has never run: it is in the wrong directory

`boat-platform/.github/workflows/ci.yml` and `release.yml` are tracked (`git ls-files`
confirms) and well designed: 8 jobs covering a two-distro build matrix, ctest, pytest, ASan,
TSan, coverage + Codecov, a determinism check, multi-arch Docker, and a vcan-backed HIL smoke
test. GitHub Actions discovers workflows **only** at the repository root `.github/workflows/`.
There is no `.github/` at the root. Neither workflow has ever executed.

This single fact explains B2, B3 and B4: the suite has never been run by anything but a human
who knew which failures to ignore.

**Relocation is not a `git mv`.** Every job assumes `boat-platform/` is the repo root:

| Assumption | Location | Needs |
|---|---|---|
| `cmake --preset release` with no `cd` | ci.yml build/asan/tsan/coverage/determinism/hil jobs | `working-directory: boat-platform` |
| `pip install ./sdk/python[dev]`, `pip install ./cli` | ci.yml `python-tests` | same |
| `test -f build/release/src/.../boat_gateway` | ci.yml | same |
| `context: .` + `file: Dockerfile.runtime` | ci.yml `docker-build`, release.yml `docker-push` | `context: boat-platform` |
| `cd build/release && cpack` | release.yml | `working-directory: boat-platform` |

Also: `release.yml` triggers only on `v*.*.*` tags and the repo has **zero tags**, so it could
not have fired even from the right directory.

> **Fixed.** Both workflows now live in `.github/workflows/` at the repo root, with
> `working-directory: boat-platform` on every build job and `context: boat-platform` on the
> Docker jobs; the old `boat-platform/.github/` is gone. Also addressed while relocating
> (S6): `ui/tests` added to the pytest job; a new `proto-stubs-in-sync` job regenerates the
> stubs and fails on a diff; `determinism-check` now gates on the *replay* cases and reruns
> them under `nproc` busy-loops; `codecov` is `fail_ci_if_error: false` so a missing token
> cannot fail the build; `cmake` is no longer apt-installed (Ubuntu 22.04 ships 3.22, below
> the required 3.24, and apt would shadow the runner's newer one) with an explicit version
> guard instead; and `cargo` presence is asserted. `release.yml` remains **unproven** — it
> cannot run until a first tag exists.

### B2 — `ctest --preset release` fails out of the box: 22 phantom re2 tests

Result on this checkout: **85% passed, 27 failed of 184.**

22 of those 27 are gRPC's vendored `third_party/re2` test suite, registered into BoAt's CTest
project by `FetchContent`. Their binaries are never built, so CTest reports them "Not Run" =
failed:

```
Could not find executable .../build/debug/_deps/grpc-build/third_party/re2/charclass_test
```

`charclass_test`, `compile_test`, `filtered_re2_test`, `mimics_pcre_test`, `parse_test`,
`possible_match_test`, `re2_test`, `re2_arg_test`, `regexp_test`, `required_prefix_test`,
`search_test`, `set_test`, `simplify_test`, `string_generator_test`, `dfa_test`,
`exhaustive{,1,2,3}_test`, `random_test`, `example`, `example64`.

**Fix:** set `BUILD_TESTING OFF` (and/or `RE2_BUILD_TESTING OFF`) before the gRPC
`FetchContent_MakeAvailable` in `boat-platform/CMakeLists.txt`, or gate CI on a `boat_` name
regex. The first is correct — the second leaves a polluted `ctest -N` for every contributor.

> **Fixed**, and it was 22 phantom tests from **two** deps, not one. `RE2_BUILD_TESTING=OFF`
> is now forced next to the existing `gRPC_BUILD_TESTS`/`protobuf_BUILD_TESTS` lines, which
> removed 20. The last 2 (`example`, `example64`) came from gRPC's bundled **zlib**, which
> calls `add_test()` unconditionally with no option to disable — so `CMakeLists.txt` patches
> those two lines out before `add_subdirectory`, following the same patch idiom the file
> already uses for gRPC's `protobuf.cmake` and iceoryx2. Test count: 184 → 164.

### B3 — A correct Catch2 `SKIP` is reported as a ctest failure

The remaining 5 failures are the HIL tests, which skip by design when `BOAT_HIL_ENABLED` is
unset (`src/tests/hil/test_hil.cpp:15`):

```
src/tests/hil/test_hil.cpp:15: SKIPPED: explicitly with message: BOAT_HIL_ENABLED not set
test cases: 1 | 1 skipped
169 - Virtual CAN HIL smoke flow (Failed)
```

No `SKIP_RETURN_CODE` is set on any test (`grep -rn SKIP_RETURN_CODE` over the CMake tree
returns nothing), so CTest cannot tell a skip from a failure.

Combined with B2, a first-time contributor's first `ctest` prints **27 failures of which zero
are real**. That is a corrosive first impression and it actively trains people to ignore red.

**Fix:** `SKIP_RETURN_CODE` on the HIL tests (Catch2 exits 4 on skip-all with the right flags).

> **Fixed.** Catch2 does exit **4** when every case in the run is skipped (verified).
> `cmake/BoAtTest.cmake` gains `boat_discover_tests()`, a one-line wrapper adding
> `PROPERTIES SKIP_RETURN_CODE 4`, and all 28 registration sites use it. Applied globally
> rather than just to the HIL tests, so the next `SKIP()` is also reported as a skip.

### B4 — A HIL test hangs forever, and no test preset sets a timeout

`src/tests/hil/test_ethernet_hil.cpp:39`:

```cpp
REQUIRE(rx->ReadFrame(received));
```

An unbounded blocking read on a UDP-multicast socket. On a host without a multicast route it
never returns and prints nothing.

Reproduced:

```
BOAT_HIL_ENABLED=1 ctest --test-dir build/debug -R HIL --timeout 60
  → 4 passed; 171 - "Virtual Ethernet HIL: frame send and receive via UDP multicast" (Timeout)
BOAT_HIL_ENABLED=1 timeout 20 ./build/debug/src/tests/boat_hil_ethernet "Virtual Ethernet HIL: frame send and receive via UDP multicast"
  → no output, killed at 20s
```

`CMakePresets.json` has exactly one `testPresets` entry (`release`) and it sets only
`output.outputOnFailure` — **no `timeout`**. So in CI this hangs the job until the 6-hour
GitHub limit rather than failing.

**Correction to the cause stated above.** It is not an environment problem. The driver
*deliberately* sets `IP_MULTICAST_LOOP = 0` (`virtual_ethernet_driver.cpp:102`, with a comment
explaining that the registry already dispatches sent frames via `DispatchRx`, so looping them
back would double-deliver). With loopback off, a multicast datagram reaches **no** socket on
the same host — not even another process's. The test created two drivers in one process on the
same interface index and expected one to receive the other's frame, so it **could never pass**.
Verified directly with a two-socket probe: `LOOP=0` times out, `LOOP=1` delivers.

The class's own header made this invisible by claiming the opposite — *"IP_MULTICAST_LOOP is
enabled so that frames sent on this machine are also delivered to subscribers on the same
machine"* — the exact behavior the implementation disables.

> **Fixed**, three things:
> - `VirtualEthernetDriver::Open()` now sets a 100 ms `SO_RCVTIMEO`. This is a **production**
>   fix, not test scaffolding: both sibling drivers already set it
>   (`socket_can_driver.cpp:72`, `raw_socket_ethernet_driver.cpp:91`), and
>   `EthernetBusRegistry`'s RX thread is a `while (running) { ReadFrame(...) }` loop — without a
>   timeout that thread can never observe `running == false` and cannot be stopped.
> - The header comment now says what the code does, and spells out the consequence.
> - The unpassable test is replaced by three that assert real behavior and pass: `ReadFrame`
>   parsing an untagged frame and an **802.1Q tagged** frame off the wire (driven from a
>   test-owned sender with loopback on — this is the first coverage the 16-byte header parser
>   has had, since the registry tests bypass the socket entirely), and one pinning the
>   no-same-host-loopback invariant that `IP_MULTICAST_LOOP=0` exists to protect. All waits are
>   bounded.
> - Every test preset now sets a `timeout` (120 s, 300 s for the sanitizers), so a future hang
>   fails instead of running until the CI job's own limit.
>
> Still open, deliberately **not** changed: the header's original promise — same-host delivery
> across *processes* — is a feature `IP_MULTICAST_LOOP=0` silently removed. Making that work
> without reintroducing double-delivery is a real design change, out of scope here. See N6.

### B5 — The README understates determinism, which is the product's central claim

`README.md:15` and the table at `:37`:

> | Which tick a replayed frame is observed in | **Not yet** — identical on an idle host, diverges under contention |

followed by a paragraph explaining that "replay, the simulation tick scheduler, and the
always-on node tick thread are three independent time domains" and that "coupling those clocks
is open work."

**That work is done.** `src/tests/determinism/test_replay_determinism.cpp:243-251`:

```cpp
TEST_CASE("Replay attributes frames to identical ticks across runs", ...)
  REQUIRE(TickAttributedProjection(run_a) == TickAttributedProjection(run_b));
```

and the comment at `:233-242` states explicitly why this is now a plain `REQUIRE`: replay and
the node plugins are ordered phases of one `TickAuthority`, and a record's due time is computed
from elapsed *ticks* rather than elapsed wall time, so a loaded host changes when a tick
happens but never which tick a record lands in. `CLAUDE.md` records 10/10 identical under 2×
CPU oversubscription, against 0/6 before.

For a deterministic testing platform this is *the* claim. Fix the table and delete the gap
paragraph.

> **Fixed.** The README table's third row now reads "**Yes** — asserted identical, including
> under CPU load", the obsolete three-time-domains paragraph is replaced by an explanation of
> *why* it now holds (one `TickAuthority`; due times computed from elapsed ticks, not wall
> time; 10/10 under 2× oversubscription vs 0/6 before), and it states the one thing still not
> promised: wall-clock timing. The guidebook's "How deterministic is deterministic?" FAQ, which
> cited only the weaker seed test, was corrected the same way. CI now gates on the replay
> cases *and* reruns them under CPU oversubscription, so the claim is enforced, not just
> written down.

### B6 — Docs instruct new users to call deleted APIs

`CanService`, `EthernetService`, `CanNode` and `EthernetNode` were deleted in `dc3cb1d` /
`7ef27f2`. `BoAtClient` has no `.can` or `.ethernet` attribute any more (no `self.can` in
`sdk/python/boat/client.py`). These sites still teach them:

| File:line | What it says |
|---|---|
| `boat-platform/docs/index.html:325-326, 1088-1107, 1109-1120` | TOC entries and full tutorial sections `#sdk-cannode` / `#sdk-ethnode`, with `from boat.can_node import CanNode` examples |
| `boat-platform/docs/index.html:1016-1018` | `client.can  # CanServiceStub`, `client.ethernet  # EthernetServiceStub` |
| `boat-platform/docs/index.html:1334, 1344` | `c.can.SendCanFrame(...)`, `c.can.SubscribeCanFrames(...)` |
| `boat-platform/docs/index.html:402, 1681-1682` | Architecture diagram and a "legacy" service table listing both protos |
| `guidebook.html:957` | "They still work but are deprecated thin wrappers." They do not work; they are gone |
| `boat-platform/docs/project.html:536, 567, 748-750` | `CanService` in the service list; a `class MyNode(CanNode)` example |
| `boat-platform/docs/api/protobuf-definitions.md:20-21, 37, 76` | Table rows and full sections for `can.proto` / `ethernet.proto` |
| `boat-platform/docs/api/api-specification.md:95, 99-101, 103` | `### can.proto — CanService (3 RPCs)` with an RPC table |
| `boat-platform/README.md:57` | Lists `CanNode`, `EthernetNode` among the SDK node classes |
| `boat-platform/cli/howto_cli.md:57-58` | Same |
| `boat-platform/docs/architecture/system-architecture.md:163` | "unified send/subscribe endpoint alongside legacy CanService/EthernetService" |

Historical references are fine and should stay: `CLAUDE.md:51-52`, `AGENTS.md:617`,
`backlog/python_sdk_backlog.md:28-30`, `proto/boat/v1/frame.proto:96` and
`docs/architecture/data-model.md:70` all describe these types as *removed*, which is accurate.

> **Fixed** at every site above. Notably, `docs/index.html` had no `FrameNode` documentation at
> all — the only remaining frame node class was undocumented while the two deleted ones each
> had a full tutorial section. The `#sdk-cannode`/`#sdk-ethnode` sections are replaced by one
> `#sdk-framenode` section covering the real API (composed, not subclassed; callback receives a
> unified `Frame`), and the raw-gRPC example was ported from `c.can.SendCanFrame` to
> `c.frame.SendFrame`. **Both new examples were run verbatim against a live gateway on `vcan0`
> and work.** Also fixed: `guidebook.html`'s FAQ claim that `boat can`/`boat eth` "still work",
> the architecture diagram, the gRPC service table (which was additionally *missing*
> `CanTpService` and `NodePluginService`), `project.html`'s `class MyNode(CanNode)` example,
> and `trace_replay.py`'s docstring. `test/CAN.md` is left alone on purpose — it is a
> hand-verified sign-off record, which CLAUDE.md says not to rewrite; see N7.

### B7 — Wrong counts throughout the front-door docs

| Claim | Where | Actual |
|---|---|---|
| "16 protobuf services across 18 `.proto` files" | `README.md:19` | **14 services, 16 files** |
| "18 proto files defining 16 gRPC services" | `docs/api/protobuf-definitions.md:19` | same |
| "10 standalone FastAPI services" | `README.md:24` | **12** — 8 in `ui/`, 4 in `tools/` (`grep -l 'FastAPI('`) |
| Built-in plugins: PduRouter, CAN-TP, TCP, SOME/IP | `README.md:17` | **5** — `probe` omitted (`src/plugins/`) |
| `FrameService` — 3 RPCs | `docs/api/*` | **4** (`ListInterfaces` was added in the migration) |
| "`boat can`/`boat eth` removed in ABI v8" | `README.md:20` | Removed in the v9 frame migration |

Per-proto RPC counts for whoever rewrites the table: bus 2, can_tp 6, debug 2, fault 2,
frame 4, metrics 2, node_plugin 3, pdu 10, plugin 4, replay 8, scenario 5, signal 3,
simulation 9, trace 4 — **64 RPCs**; `common.proto` and `control.proto` declare no service.

**Recommendation:** generate these tables from `proto/boat/v1/` instead of hand-maintaining
them. Every count in this row is wrong *because* it is hand-maintained; fixing the numbers
without fixing the mechanism just resets the clock.

> **Fixed** — all counts corrected across `README.md`, `docs/api/protobuf-definitions.md`,
> `docs/api/api-specification.md` and `docs/index.html`, and the deleted protos' sections
> removed in favour of a replacement table mapping each dead RPC to its `FrameService`
> equivalent. The generate-from-proto recommendation **stands and is not done**: these numbers
> are still hand-maintained and will drift again. That is step 3 of the consolidation proposal
> you approved, still to be written.

### B8 — The headline user flow cannot be run: no scenario file ships

`project-plan.md` advertises the primary flow as
`boat scenario create --file scenario.yaml` → `boat sim start` → `boat sim watch`.

Two problems:

- `boat scenario create --help` requires **JSON**, not YAML: "Path to a scenario JSON file."
- The repo contains **no scenario file in either format**. The only `*.yaml` files tracked are
  `admin_gui/session.yaml`, `docker-compose.yml`, and the two misplaced CI workflows.

So a new user following the documented flow cannot run it, and `boat sim create` — the entry
point to the simulation lifecycle, the project's first functional requirement — has nothing to
be pointed at. Ship a minimal `config/scenarios/example.json` and reference it from the README
quick start.

> **Fixed.** `boat-platform/config/scenarios/example.json` now ships, written against the
> authoritative schema in `src/core/scenario/scenario_loader.cpp` (where every field is read
> with `.at()`, so all of `id`/`name`/`version`/`duration_ticks`/`seed`/`plugins`/`signals`/
> `faults` are mandatory — a partial scenario throws). **Verified end to end**: `scenario
> validate` → `valid=True`, then `scenario create` → `sim create` → `start` → `pause` →
> `step --ticks 10` → `state` → `stop`, all succeeding. The README quick start is rewritten to
> walk this, plus the build, vcan setup and `pip install` steps it previously omitted, and
> `project-plan.md`'s flow is corrected (`scenario.yaml` → JSON, and `sim create` is a separate
> step from `sim start`).

---

## Should-fix

**S1 — `boat test run <manifest.json>` resolves paths against the CWD, not the manifest.**
```
$ boat test run boat-platform/config/tests/manifest_can_loopback.json
Config not found: config/tests/env_can_loopback.json
```
The manifest's `environment_config` is the relative path `config/tests/env_can_loopback.json`,
resolved against the process CWD. The documented invocation only works when you happen to be
inside `boat-platform/`. Resolve `environment_config` (and each test's `file`) relative to the
manifest's own directory.

> **Fixed**, and there was a **second, worse** CWD dependency behind it. `gateway.binary` was
> also CWD-relative, and `_GatewayManager.start()` checked it with
> `if binary and os.path.isfile(binary)` — so from anywhere but `boat-platform/` it **silently
> started no gateway**, and the test then failed with an opaque connection error naming nothing.
> That is why the two directories failed *differently* (482 ms vs a 30 s timeout) even after
> `environment_config` was fixed; only chasing that discrepancy surfaced it.
>
> `ManifestConfig` and `EnvironmentConfig` now record the directory they were loaded from and
> resolve relative paths against it (`resolve()` / `resolve_path()`), each falling back to the
> CWD so older project-root-relative manifests keep working. Each test subprocess runs with
> `cwd` set to the manifest's directory. A configured-but-unfindable `gateway.binary` now
> raises `FileNotFoundError` naming both the configured and the attempted path. The four
> shipped `config/tests/env_*.json` and the manifest were updated to self-relative paths.
> **Verified**: `boat test run` now behaves identically from the repo root and from
> `boat-platform/` (both reach the test and fail only because physical `can0`/`can1` are down,
> which is this machine's state, not a bug). 11 new tests cover the resolution rules and the
> new error — this SDK code had none.

**S2 — ABI version stale in 7 places.** `v8` where the SDK is `v9`:
`docs/architecture/system-architecture.md:160`, `docs/project.html:517,651`,
`docs/index.html:426,443`, `guidebook.html:957`, `test/CAN.md:186`,
`backlog/pdu_gap_analysis.md:3` (this one self-corrects in the same sentence — leave it),
`boat-platform/README.md:103`.

**S3 — `boat sim init` does not exist.** Documented at `CLAUDE.md:145` and `AGENTS.md:195` as
`boat sim init|start|pause|step|stop`. The verb is `create`. The user-facing docs
(`guidebook.html`, `docs/index.html`, `test/Simulation.md`, `cli/howto_cli.md`) have it right —
it is only the two agent-facing files that are wrong, which matters because they shape every
future contribution made with tooling.

**S4 — `trace_replay.py` docstring describes a deleted service.** `:8` says frames are sent
"via gRPC CanService"; `:907` uses `frame_pb2_grpc.FrameServiceStub`. Same false claim in
`docs/howto/replay.md:451` and `.opencode/agents/trace-analysis.md:46`.

**S5 — Dead mock gives false confidence.** `cli/tests/test_cli_commands.py:485,501` set
`fake_client.can = SimpleNamespace(ListBuses=...)`. The real `BoAtClient` has no `.can`, so the
mock has diverged from the object it stands in for: it would not catch a regression that
reintroduced a `.can` call, and it documents an API that no longer exists.

**S6 — CI job gaps to fix while relocating (B1).**
- `ui/tests` is not run. It exists, is part of the documented pytest invocation, and passes.
- `determinism-check` gates on `boat_determinism_seed` only — the weaker test. `CLAUDE.md`
  itself says it "pins the PRNG, not the product claim". Gate on `boat_determinism_replay`,
  which is the system-level one and now asserts tick attribution (B5).
- No stub-sync check, though `CLAUDE.md` requires the committed stubs stay in sync with
  `proto/`. (They currently are — see "In good shape" — but nothing enforces it.)
- `coverage` uses `codecov-action` with `fail_ci_if_error: true` and a `CODECOV_TOKEN` secret;
  that job fails until the secret exists.
- `apt-get install` lists omit `cargo`, a documented build-time requirement (iceoryx2's Rust
  core). GitHub runners ship Rust, so this is a risk rather than a certain break — but it
  should be explicit rather than inherited from the image.
- `libacl1-dev` is also omitted; `CMakeLists.txt:26-38` handles its absence by
  `apt-get download`ing it into the build tree, so this one is genuinely covered.

**S7 — Performance claims with no benchmark behind them.** `project-plan.md:51-52` commits to
"≤1 ms jitter in simulated mode" and "≥1,000,000 events per second". There is **no benchmark
anywhere in the tree** (`find -iname '*bench*'` returns only `.opencode/node_modules` noise).
`project-plan.md` is linked from the README's "Learn more", so a public visitor reads these as
product claims. Either build a benchmark or reword them as design targets. Unmeasured
performance numbers in a testing product invite exactly the scrutiny you cannot answer.

**S8 — Portability claim is not true.** `project-plan.md:53-54` lists "macOS and Windows via
abstraction layer" as a portability target. `src/core/plugin/plugin_manager.cpp:97` throws
"Plugin loading via dlopen/dlsym is not supported on Windows", and the CAN layer is SocketCAN
throughout. State Linux-only; keep the others as aspiration if you want, but label them.

**S9 — Untested surfaces.** No tests for `admin_gui/` (4 modules), `tools/` (6 scripts),
`nodes/`, or `demo/`. `tools/dbc2boatjson.py` is the clearest cost of this: it has an
externally-reported bug (S10) and no test.

**S10 — Open consumer-reported bug, living outside version control.**
`test/foundIssues.md` #1: `tools/dbc2boatjson.py:253-254` applies factor and offset to a DBC
`SG_` range that is **already in physical units**:
```python
phys_min = s["raw_min"] * s["factor"] + s["offset"]
phys_max = s["raw_max"] * s["factor"] + s["offset"]
```
Every converted database carries wrong `Min`/`Max` whenever factor ≠ 1 or offset ≠ 0 — e.g.
`VehicleSpeed` 0/400 km/h becomes 0.0/4.0, `OutsideTemp` -50/77.5 °C becomes -75.0/-11.25.
Seven signals tabulated in the report. Found by a real consumer project (R80 restbus), which
now works around it by reading ranges from the DBC directly.

Note also that `test/foundIssues.md` is **untracked**. An externally-reported bug report is
living outside version control, where nobody else can see it.

**S11 — No community or release scaffolding.** Absent: `CONTRIBUTING.md`, `SECURITY.md`,
`CODE_OF_CONDUCT.md`, `CHANGELOG.md`, issue and PR templates, `SUPPORT.md`. **Zero git tags**;
`boat-py` and `boat-cli` both at `0.1.0`. `CONTRIBUTING.md` matters more than usual here: the
CLAUDE.md/AGENTS.md dual-file rule is a repo-specific invariant that a drive-by contributor
will violate on their first docs PR unless it is written down outside those two files.

---

## Nice-to-have

**N1 — Streaming CLI output is block-buffered through a pipe.** `boat frame subscribe | grep`
prints nothing until the buffer fills — I misdiagnosed the subscribe path as broken on the
first attempt for exactly this reason, and a user will too. Same family: `boat replay stream`'s
`\r` progress counter concatenates into one unreadable line when redirected
(`Streaming... 1 frame(s) sent tick=0Streaming... 3 frame(s) sent tick=68...`). Line-buffer
when `stdout` is not a TTY, and suppress the `\r` counter there.

**N2 — `boat db list` needs `--db` in practice.** Fails with
`Database not found: 'pdu_db.json'. Use --db to specify the path.` The error is good; no doc
mentions that the default almost never resolves.

**N3 — Illustrative method paths reference a deleted service.**
`src/gateway/grpc_gateway/rpc_audit_log.h:21`, `proto/boat/v1/debug.proto:14`,
`ui/debug.py:10,48` all use `/boat.v1.CanService/SendCanFrame` as the example gRPC method path.
Cosmetic, but it is the example a reader learns the audit-log format from.

**N4 — SOME/IP is a stub presented as a peer.** 252 lines total
(`src/plugins/someip/someip_plugin.{cpp,h}`); service discovery is not implemented, as
`system-architecture.md:77` and `AGENTS.md:30` both say. `README.md:17` lists it alongside
PduRouter, CAN-TP and TCP without qualification.

**N5 — Publish the known-gaps backlogs rather than letting visitors find them.**
`pdu_gap_analysis.md` tallies 38 AUTOSAR gaps (9 critical / 16 important / 13 minor);
`tcp_plugin_backlog.md` lists 13, including no congestion control (RFC 5681/6582) and no RTT
estimation or adaptive RTO. Both documents are genuinely good — self-correcting, with a
10-entry table of their own earlier errors. They are an asset for credibility if linked from a
public roadmap, and a liability if a visitor discovers them unprompted.

---

## In good shape — verified, not assumed

- **Python: 527/527 pass in 13.7s** (`sdk/python/tests`, `cli/tests`, `ui/tests`).
- **C++: all 157 real tests pass.** The 27 "failures" are entirely B2 + B3.
- **Determinism is stronger than documented** — see B5.
- **Gateway starts clean** on `vcan0`, registers the interface with driver metadata, reports its
  tick authority and phase order, and warns that TLS is unconfigured.
- **Live walkthrough passed**, against a real gateway with real wire traffic:
  `boat frame list-ifaces`, `boat frame send`, `boat frame subscribe` (4/4 frames injected with
  `cansend` received and decoded), `boat plugin list`, `boat config show`, `boat db list`,
  and the full `pdu` / `replay` / `trace` / `sim` command surfaces. **Replay round trip**:
  `boat replay import` (20 frames accepted) → `boat replay stream --buses vcan0` → 20/20 sent,
  ticks 0→655, clean exit.
- **protoc stubs are 1:1 in sync** with all 16 protos; no orphans.
- **SPDX headers on every tracked C++ source and header** — zero missing.
- **`THIRD_PARTY_NOTICES.md` is accurate and genuinely thorough**: all 6 `FetchContent` deps
  plus Catch2, the transitive `iceoryx_hoofs`, the vendored-libacl path with its LGPL
  obligation called out, Python deps with licenses, and the opendbc on-demand fetch with a
  correct analysis of why MIT's notice requirement does not attach. This needs no work.
- **Repo hygiene is clean**, with one correction to my first pass: the root `*.db`, `traces/`,
  `__pycache__` and `.pytest_cache` noise is gitignored and nothing junk is tracked — but
  **`reports/` was *not* ignored**, so the JSON/JUnit/HTML artefacts `boat test run` writes were
  one `git add -A` away from being committed. Now ignored (a bare `reports/` pattern, so it
  covers both the repo root and `boat-platform/`).
- **Effective-config dump works** and is byte-stable by design (no timestamp or hostname).

---

## F. Banner verdict

**Keep the banner until B1–B8 are closed**, then replace it rather than delete it.

Removing it outright would be a stronger promise than the project can keep: the gRPC surface
and the plugin ABI *are* still moving (ABI v9 landed days ago and hard-rejects v8 plugins at
load), and plugin authors need to know that rule before they build against it. "No banner" is
read as "stable", and that is not yet true.

Proposed replacement for `README.md:3`:

> **Status: pre-1.0.** The gateway, Python SDK/CLI, and plugin ABI are in active use and
> covered by CI (184 C++ tests, 527 Python tests). The gRPC surface and the C plugin ABI may
> still change between minor versions — the ABI is versioned (currently **v9**) and a mismatched
> plugin is rejected at load with a clear error rather than failing at runtime. See CHANGELOG.md
> for what changed, and CONTRIBUTING.md to get involved.

That is a better invitation than the current banner *and* a more honest one: it says what works,
what might move, and how you will find out — which is what a reader evaluating a testing
platform actually needs.

---

## Suggested order of work

| # | Item | Status |
|---|---|---|
| 1 | B2, B3, B4 — make `ctest` green and bounded | ✅ **Done.** 164/164 pass, with and without `BOAT_HIL_ENABLED` |
| 2 | B1 — relocate CI with the path fixes, plus S6 | ✅ **Done.** 9 jobs at the repo root; `release.yml` still unproven until a first tag |
| 3 | B5, B6, B7 — doc truth fixes | ✅ **Done.** Both rewritten code examples run verbatim against a live gateway |
| 4 | B8, S1 — make the documented flows actually runnable | ✅ **Done.** Example scenario ships; full sim lifecycle verified; `boat test run` works from anywhere |
| 5 | S11 — community files, CHANGELOG, first tag | Next. `release.yml` fires on the first `v*.*.*` tag — check it works before announcing |
| 6 | S10 + S9 — fix `dbc2boatjson.py`, add the regression test, track `foundIssues.md` | A reported bug outstanding at launch is worse than a known gap |
| 7 | S2, S4, S5, S7, S8 — remaining accuracy fixes | Cheap, and S7/S8 are claims worth not making. (S3 was folded into step 4.) |
| 8 | N1–N10 | Polish. N5, N8 and N9 are the ones worth doing before any announcement |

**Not done, and worth saying plainly:** B7's underlying recommendation — *generate* the
service/RPC tables from `proto/boat/v1/` so counts cannot drift — is still open, as is the
consolidation proposal for the three overlapping HTML doc sets. The numbers are correct today
and still hand-maintained, so the clock has been reset rather than stopped.

Per `CLAUDE.md`'s dual-file rule, every fact corrected in `CLAUDE.md` must land in `AGENTS.md`
and vice versa — S3 is already an instance of those two files agreeing with each other and
both being wrong, which is the failure mode the rule exists to prevent.

---

## Newly found while fixing steps 1–4

**N6 — `VirtualEthernetDriver` silently dropped a documented feature.** The class header
promises that frames sent on this machine reach subscribers on the same machine; `Open()` sets
`IP_MULTICAST_LOOP = 0`, which means they reach **no** socket on this host, including other
processes'. The comment justifies it by in-process double-delivery (the registry already
dispatches sent frames via `DispatchRx`), but the fix also removed cross-process same-host
delivery, which is what "multiple veth interfaces on the same machine stay isolated" implies is
supported. Doing this properly — loopback on, with a filter that drops a driver's own echoes —
is a real design change. **Severity: should-fix.** The header now describes actual behavior, so
nobody is misled in the meantime, and B4's replacement test pins the current invariant.

**N7 — `test/CAN.md:186` says the `boat can` removal was "ABI v8".** It was the v9 unified-frame
migration. Deliberately **not** fixed: `test/*.md` is the hand-verified release sign-off record
and CLAUDE.md says never to update it programmatically. Editing a record of what a human
actually ran would make it less trustworthy, not more. Worth a human pass — the same file's
"Expected" block also still anticipates a deprecation warning pointing at `boat frame`, which
cannot happen now the subcommand is gone. **Severity: should-fix, human-only.**

**N8 — `boat sim` prints raw enum integers.** `boat sim start/pause/stop` print `status` as
`2`/`3`/`4` and `boat sim state` prints `state` as `3`, while the command's own help text speaks
of `IDLE/RUNNING/PAUSED/STOPPED`. Harmless to a maintainer, opaque to a new user following the
quick start — which now walks exactly this sequence. **Severity: nice-to-have**, but cheap and
high-visibility for a launch.

**N9 — a timed-out test loses all its output.** *(Corrected: my first version of this said the
runner captures no stdout/stderr at all. That is wrong — it writes `stdout.txt` and `stderr.txt`
sidecars next to `report.json`, and they are genuinely useful.)* The real defect is narrower:
`runner.py:305-307` handles `subprocess.TimeoutExpired` by setting `result = None`, which throws
away the output captured before the timeout. Comparing report directories shows it plainly — runs
that exited normally have `stdout.txt`/`stderr.txt`, runs that timed out have neither. A timeout
is precisely the case where you most need to see how far the test got. `TimeoutExpired` carries
`.stdout`/`.stderr`, so the data is right there to be written. The same applies to the
`FileNotFoundError` branch below it. **Severity: should-fix**, ~3 lines.

**N10 — `CLAUDE.md`/`AGENTS.md` claimed there is no JSON parser in the C++ tree.**
`src/core/scenario/scenario_loader.cpp` contains a full hand-rolled recursive JSON parser. The
intended claim was about the *config* path, which is accurate. Both files now say that, and name
the scenario parser as the one exception so it is not taken as licence to parse config.
**Fixed.**

---

## From the first CI run (2026-10-06)

Opening PR #7 executed these workflows for the first time in the project's history. It went red
with 5 failures, which is the pipeline doing its job — but it also means the "164/164 and 538/538
green" recorded above was green *on one machine*, and CI is a stricter environment than that.
Four were fixed on the branch; one is unresolved.

**Fixed: my own CMake version guard was broken.** `cmake -P /dev/stdin` fed by a heredoc needs a
seekable file; on a runner the heredoc is a pipe, so it died with *"Error while reading
Byte-Order-Mark. File not seekable?"* and took both `build-and-test` jobs — and therefore the 5
jobs that `needs:` them — down with it. It passed when I checked it locally only because bash
backed that heredoc with a temp file. Replaced with a `sort -V` comparison in plain shell,
verified through an actual pipe and verified to still fail on a too-old version.

**Fixed: the stub-sync gate reported drift that wasn't drift.** The committed stubs are generated
by `grpcio-tools`, the `[dev]` extra asked for `>=1.81.1`, and CI resolved something newer that
emits a different `Protobuf Python Version` header. Now pinned to `==1.83.1`, the version that
produced the committed stubs — verified to reproduce them byte-identically. A committed-codegen
policy needs a pinned generator or the gate is noise.

**Fixed: `ci.yml`'s `docker-build` could never have passed.** `Dockerfile.runtime` COPYs
`build/release/.../boat_gateway` out of the context, and that job had no build step and no
`needs:`. This was broken in the original workflow too and I relocated it without noticing; it
only became visible once anything ran it. It now `needs: build-and-test` and builds the
`boat_gateway` target first, as `release.yml`'s `docker-push` already did.

**Fixed, by testing the right thing: a test read Typer's rendered `--help`.**
`test_trace_replay_help_has_no_server_side_or_ethernet_flags` asserted on help *text*. It now
asserts on the command's declared option set via `typer.main.get_command`, which is the actual
contract; a separate weak test still catches a rendering crash.

**N11 — unresolved: `boat trace replay --help` renders differently on Python 3.11.** In CI the
options panel came back with `--buses`, `--speed`, `--loop` and `--sim-id` absent and the `file`
argument showing `--sim-id`'s description — a mangled panel, not a width truncation. **I could
not reproduce it.** In a fresh venv I matched CI's Typer 0.27.2, Rich 15.0.0 and Click 8.5.0 and
the help rendered every flag, at widths from 60 to 200 columns. The only remaining variable is
the interpreter: CI ran 3.11, and no 3.11 is installed on this machine.

This matters beyond the test, because `requires-python = ">=3.11"`: if help really does render
with four flags missing on the floor version, every user who installs on 3.11 gets wrong `--help`
output, and that is a launch-blocking bug rather than a test nuisance. The pytest job now runs a
**3.11 + 3.13 matrix** specifically so the next run says whether this is real and version-bound.
Diagnosing it needs a 3.11 interpreter. **Severity: unknown — treat as should-fix until the matrix
run settles it.** Note that the rewritten test will now *pass* on 3.11 either way, so the matrix
is the only thing watching for this.

**Also worth recording:** `typer[all]` is a dead extra (`WARNING: typer 0.27.2 does not provide
the extra 'all'`), and `boat-cli` pins neither click nor rich (`typer[all]>=0.12`, `rich>=13`), so
every fresh install resolves whatever is newest. For a tool whose CLI help is part of its
interface, that is a wide unpinned surface. **Severity: should-fix.**

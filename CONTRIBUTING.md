# Contributing to BoAt

Thanks for looking. BoAt is a deterministic automotive simulation and testing platform, and
"deterministic" is a load-bearing word — a few of the rules below exist specifically to keep
it true. The rest is ordinary.

Contributions are accepted under the [Apache License 2.0](LICENSE) (§5), the same licence the
project ships under.

## Where to start

Open work is in [GitHub Issues](https://github.com/AlexTech-stack/BoAt/issues), grouped into
four [milestones](https://github.com/AlexTech-stack/BoAt/milestones) and labelled by area
(`area:*`), kind and priority (`P0`–`P3`).

- **[`good first issue`](https://github.com/AlexTech-stack/BoAt/issues?q=is%3Aopen+label%3A%22good+first+issue%22)**
  — self-contained, well specified, and a reasonable way to read through one part of the tree.
- **[`help wanted`](https://github.com/AlexTech-stack/BoAt/issues?q=is%3Aopen+label%3A%22help+wanted%22)**
  — where an extra pair of eyes would make the most difference.

Issues carry `file:line` evidence and link to [`backlog/`](backlog/) for the longer analysis
behind them — gap assessments, incident write-ups, and in several cases a record of the wrong
hypotheses that cost time first. [`backlog/README.md`](backlog/README.md) maps the files to
the issues that track them. [`boat-platform/project-plan.md`](boat-platform/project-plan.md)
is the plan of record and says plainly which requirements are not met.

Two things worth knowing before you pick something up:

- **`master` is not currently green.** The TSan job fails 6 of 164 tests
  ([#10](https://github.com/AlexTech-stack/BoAt/issues/10)). Every other job passes. If your
  change is unrelated and TSan is the only red job, that is the known failure and not you.
- **If you find something while working on something else**, a one-line issue is welcome even
  without a fix. Several of the most useful entries in `backlog/` started that way, and two
  of the bugs fixed this year were reported by people using BoAt from another project rather
  than working on it.

## Getting set up

**Prerequisites.** CMake **3.24+** (Ubuntu 22.04's 3.22 is too old), Ninja, a C++20 g++, and a
Rust toolchain (`cargo`) — the last is a build-time-only dependency of iceoryx2's core.
`libacl1-dev` is needed by `iceoryx_hoofs`; if it is not installed system-wide the build
downloads it into the build tree for you.

```bash
# C++ — run from boat-platform/
cd boat-platform
cmake --preset debug && cmake --build --preset debug

# Python SDK + CLI — run from the repository root
pip install -e ./boat-platform/sdk/python[dev]
pip install -e ./boat-platform/cli
pip install -r ui/requirements.txt          # only if you touch ui/
```

A virtual CAN bus to point it at:

```bash
sudo modprobe vcan
sudo ip link add vcan0 type vcan && sudo ip link set vcan0 up
```

## Running the tests

```bash
cd boat-platform && ctest --preset debug            # 164 tests
# 7 of those are HIL tests and report Skipped unless you opt in:
BOAT_HIL_ENABLED=1 BOAT_VCAN_IFACE=vcan0 ctest --preset debug
```

```bash
# from the repository root — ui/tests lives here, not under boat-platform/
pytest boat-platform/sdk/python/tests boat-platform/cli/tests ui/tests -v   # 538 tests
```

Both suites should be fully green before you open a PR. CI runs them, plus ASan, TSan,
coverage, a stub-sync check, a determinism check under CPU load, and a vcan-backed HIL run.

Three gotchas that have each bitten someone:

- **Register new C++ tests with `boat_discover_tests()`**, not `catch_discover_tests()`
  directly. It sets `SKIP_RETURN_CODE 4`; without it a Catch2 `SKIP()` is reported as a
  *failure*.
- **`ctest -R` matches Catch2 test-case names, not target names.** `-R boat_hil_smoke` matches
  nothing. The test presets set `noTestsAction: error` so an empty filter fails loudly rather
  than passing silently, which it used to do.
- **A new third-party dependency must not register its tests into our CTest project.**
  `gRPC_BUILD_TESTS=OFF` does not reach gRPC's bundled re2 or zlib; see the comments in
  `boat-platform/CMakeLists.txt`.

## Three different things are called "test" here

Be explicit about which you mean:

| | What it is |
|---|---|
| `ctest` / `pytest` | Tests of the codebase itself. Add these. |
| `test/*.md` | The **manual**, hand-verified release sign-off record. **Never** update these verdicts programmatically — they record what a human actually observed. |
| `boat test run <manifest>` | An automated CI-style HIL suite runner, driven by an `EnvironmentConfig` + a `ManifestConfig`. |

## House rules

**`CLAUDE.md` and `AGENTS.md` must never disagree.** They describe the same repository, split
by audience rather than depth: Claude Code reads the first, every other agent tool reads the
second. Neither is authoritative over the other — the source code is authoritative over both.
If your change makes a fact in one of them wrong, fix it in **both** in the same PR. This is
the single easiest rule to break, because nothing fails when you do: each file's readers never
see the other.

**New source files get a two-line SPDX header**, matching the comment syntax of the language
and placed after any `#!` shebang:

```
// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0
```

Don't paste the full Apache boilerplate. Generated files are deliberately header-less (the
protoc stubs under `sdk/python/boat/stubs/boat/v1/`, `tools/wireshark/boat_pdu_db.lua`) because
their generators would overwrite it — leave them alone.

**After editing any `.proto`, regenerate the stubs** and commit the result:

```bash
bash boat-platform/sdk/python/boat/stubs/generate_stubs.sh
```

The generated stubs are committed and CI fails if they drift from `proto/`.

**Don't introduce unseeded randomness or nondeterministic ordering** in core, scheduling or
replay code, and don't give replay or a plugin a clock of its own. Since ABI v9 a plugin that
needs time receives it from the host via `set_time_source`. This is the whole point of the
`TickAuthority` design: `boat_determinism_replay` asserts that frame content, ordering *and*
tick attribution are bit-identical across runs, with a plain `REQUIRE` and no tolerance. If
your change makes that test need a tolerance, the change is wrong.

**Adding a dependency?** Add it to [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) too.
Nothing third-party is vendored into this tree — C++ deps arrive via CMake `FetchContent` at
build time, Python deps via pip.

## Commits and pull requests

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/), as the
existing history does: `feat(sdk): …`, `fix(test): …`, `refactor(hil): …`, `docs: …`, with a
`!` for a breaking change (`refactor(plugins)!: …`). Explain *why* in the body — the history
here is used as documentation.

Open the PR against `master`. Say what you changed, how you verified it, and call out anything
you deliberately did not do. A PR that says "tests pass" is less useful than one that says
which command you ran.

## Versioning and stability

BoAt is **pre-1.0**. Expect the following:

- **The project version** (`0.x.y`) is in `boat-platform/CMakeLists.txt` and both
  `pyproject.toml` files; they move together. Before 1.0, a **minor** bump may contain a
  breaking change. Breaking changes are listed in [CHANGELOG.md](CHANGELOG.md) and carry a `!`
  in their commit subject.
- **The plugin ABI** is versioned separately by a single integer,
  `BOAT_PLUGIN_ABI_VERSION` in `sdk/cpp/include/boat/plugin.h` (currently **9**). Any change
  to the vtable's shape or to the meaning of an existing entry bumps it. There are **no
  compatibility shims**: a plugin reporting a different version is rejected at load with a
  clear error, deliberately, so a stale `.so` fails at startup instead of misbehaving at
  runtime. A bump means every out-of-tree plugin must be recompiled, so it needs a reason.
- **The gRPC surface** (`proto/boat/v1/`) grows additively where possible. Removing a service
  or an RPC is a breaking change and gets a `!`. Deletions are real: `CanService` and
  `EthernetService` were removed outright rather than left as deprecated wrappers.

## Questions

Open an issue. For anything security-related, see [SECURITY.md](SECURITY.md) instead — please
don't file a public issue for a vulnerability.

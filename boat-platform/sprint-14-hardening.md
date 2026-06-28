# Sprint 14 — Hardening & Risk Mitigation

Target: Close remaining M6 GA hardening gaps.

## H1 — Plugin crash containment

**Risk:** R01 — A segfault or abort in any plugin `.so` kills the entire gateway process.

**Scope:** Introduce process-level isolation for plugins so that a crash in one plugin does not take down the gateway or other plugins. Evaluate approaches:
- Subprocess per plugin with SHM or UDS communication channel
- `fork()` + sandbox per `on_tick` cycle with controlled teardown

**Acceptance:** A plugin that crashes (SIGSEGV, SIGABRT) does not affect the gateway or other plugins. The gateway logs the crash and continues ticking.

---

## H2 — gRPC TLS + authentication

**Risk:** R08 — Gateway binds `0.0.0.0:50051` with no transport security.

**Scope:**
1. Add `--tls-cert` and `--tls-key` flags to the gateway binary
2. Load credentials via `grpc::SslServerCredentials` when flags are present
3. Default remains insecure (backward compatible)
4. Optionally support mutual TLS (`--tls-ca`)

**Acceptance:** Gateway can be started with TLS enabled using a cert/key pair. All gRPC clients fail to connect without the correct root CA. Without flags, behavior is identical to current.

---

## H3 — gRPC health check service

**Risk:** None directly, but absence blocks orchestration integration.

**Scope:** Implement `grpc::Health::CheckService` (standard gRPC health checking protocol). Report `SERVING` when the gateway is alive and the gRPC server has started.

**Acceptance:** `grpc_health_probe` against `0.0.0.0:50051` returns `SERVING`. Docker HEALTHCHECK and K8s liveness probes can use this.

---

## H4 — Auto-generated Python gRPC stubs in CMake build

**Risk:** R04 — Python stubs require manual `generate_stubs.sh`. Proto changes silently drift from Python SDK.

**Scope:** Add a CMake custom target (or `add_custom_command`) that runs `protoc` with the Python gRPC plugin at build time, placing generated stubs directly into `sdk/python/boat/stubs/boat/v1/`. Wire it as a dependency of the `boat_py_stubs` target so it re-runs whenever proto files change.

**Acceptance:** Changing a `.proto` file and running `cmake --build <preset> --target boat_py_stubs` regenerates Python stubs. CI deterministically uses current stubs.

---

## H5 — docker-compose agent and store services

**Risk:** None direct, but `boat-agent` and `boat-store` are placeholders (`sleep infinity`).

**Scope:**
- `boat-store`: Stand up SQLite-backed store as a sidecar with a gRPC endpoint exposing event/trace/config store operations
- `boat-agent`: Implement multi-instance coordination stub (at minimum a health-reporting service that registers with the gateway)

**Acceptance:** `docker-compose up` starts gateway + agent + store. Store persists events to a named volume. Agent reports health to the gateway.

---

## H6 — Semantic versioning enforcement in CI

**Risk:** Version is hardcoded as `0.1.0` in `CMakeLists.txt` with no release process enforcement.

**Scope:**
1. Read version from a single source (`VERSION` file at repo root)
2. CI release job validates the tag matches `project(VERSION)` before building
3. Automatically stamp Docker images and CPack packages with the tag version

**Acceptance:** Pushing a `v1.2.3` tag triggers a release that builds packages and Docker images tagged `1.2.3`. A tag that does not match `project(VERSION)` is rejected. The version string appears in `boat_gateway --version`.

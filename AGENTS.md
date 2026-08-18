# AGENTS.md — BoAt Platform

## Repository structure

- **`boat-platform/`** — Main platform (C++20, CMake+Ninja, gRPC)
  - `src/core/` — Simulation engine (scheduler, signal router, determinism, plugin mgr, `core::Frame`)
  - `src/gateway/grpc_gateway/` — gRPC server → `boat_gateway` binary, listens on `0.0.0.0:50051` by default (`BOAT_GRPC_PORT` overrides — needed to run more than one instance on one host; the gateway refuses to start rather than silently sharing a port via gRPC's `SO_REUSEPORT`, see `backlog/gateway_backlog.md`)
  - `src/hil/` — HIL bridge (CAN/Ethernet drivers, bus registries, tick timer)
    - `can/` — `SocketCanDriver` (raw AF_CAN/SOCK_RAW), `PhysicalCanDriver` (sysfs-probing physical HW)
    - `virtual/` — `VirtualCanDriver` (SocketCan wrapper for vcan*)
    - `ethernet/` — Ethernet drivers (virtual multicast, raw AF_PACKET)
    - `pdu/com/` — COM signal library (bit pack/unpack, E2E CRC, Intel/Motorola)
    - `pdu/tick_timer.h/.cpp` — Dual-backend tick timer (sleep_for / timerfd)
  - `src/store/` — SQLite event/trace/config stores
  - `src/ipc/` — Inter-process comm (gRPC, iceoryx2 SHM, UDS)
  - `src/plugins/` — Built-in plugins (all v8 ABI, `on_frame`/`set_frame_publisher`).
    Plugins own **stateful conversations / variation** only; stateless CAN/Ethernet
    transport is core (see the `FrameSink` note under Replay).
    - `pdu_router/` — PduRouter plugin (routes PDUs over CAN/Ethernet, transmission engine, deadline monitoring, groups)
    - `can_tp/` — ISO 15765-2 CAN Transport Protocol (segmentation/reassembly)
    - `someip/` — SOME/IP middleware (service discovery stub, request/response)
    - `tcp/` — TCP transport plugin (state machine only; transmits via the core Eth registry when gateway-resident)
    - `probe/` — gateway conformance probe (verifies delivery, declared_buses filtering, self-sent tagging, round-trip from inside the dispatch loop)
  - `src/replay/` — Replay engine
  - `proto/boat/v1/` — 16 protobuf definitions defining all gRPC services
  - `sdk/python/` — `boat-py` package (BoAtClient gRPC client, frame nodes, trace tools)
  - `sdk/cpp/include/boat/` — C++ SDK headers
    - `plugin.h` — Plugin ABI v8 (unified `on_frame`, `set_frame_publisher`, `declared_buses`)
    - `frame.h` — Unified `BoatFrame` type (CAN, CANFD, Ethernet, TCP, PDU bus types)
    - `can_tp.h` — Standalone CanTp C API (can_tp_send, can_tp_configure, can_tp_remove)
    - `someip.h` — SOME/IP protocol constants
  - `cli/` — `boat-cli` package (Typer CLI: `boat sim|scenario|replay|frame|can|eth|pdu|can-tp|plugin|...`)
  - `config/` — PDU database JSON files
  - `demo/` — Demo node scripts (not web UI; scenario-specific, e.g. `cyclic_sender_node.py`'s CAN-ID start/stop trigger)
  - `nodes/` — General-purpose node scripts, discoverable/runnable two ways: `ui/control_panel.py`'s "Nodes" web UI (any `.py` file not prefixed `_`, auto-listed with its module docstring's first line), and `ui/launcher_agent.py`/`admin_gui`'s Nodes tab (same discovery, but as a tracked, multi-node, multi-host registry -- see "Launcher Agent"/"Admin GUI" below). Each script accepts `--address` (default `None`, so `BOAT_HOST` decides when omitted) and its own behavior flags -- see `cyclic_can_sender.py` (configurable periodic CAN(FD) frame) and `can_request_responder.py` (replies to one CAN ID with a fixed response) for the raw-CAN pattern, or `pdu_cyclic_publisher.py`/`can_tp_trigger_sender.py` for the equivalent pattern **through a plugin** (`pdu_router`/`can_tp` respectively) instead of the gateway's core FrameSink -- see "Plugin-based node scripts" below. `control_panel.py` always passes `--address <its gateway field>` explicitly; `launcher_agent.py` sets `BOAT_HOST` in the spawned process's env instead -- both work since `--address` defaults to `None`.
- **`ui/`** — 7 standalone FastAPI/uvicorn web services requiring a running gateway (launcher:8086, dashboard:8080, commander:8082, recorder:8083, control_panel, debug, system_dashboard)
- **`tools/`** — 2 standalone tools (pdu_editor:8087, trace_analyzer:8088)
- **`traces/`** — Trace output directory (gitignored)

## Build & run

```bash
# Build C++ (presets: debug, release, asan, tsan, coverage)
cmake --preset debug && cmake --build --preset debug

# Gateway binary
build/debug/src/gateway/grpc_gateway/boat_gateway

# Prerequisites (Ubuntu 22.04 ships CMake 3.22 — need 3.24+)
# Rust toolchain required (transitive dep of iceoryx2 for SHM IPC)
# libacl1-dev for sys/acl.h (CMake auto-downloads if missing)
sudo apt install cmake ninja-build g++ libacl1-dev
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Run gateway with virtual CAN
sudo modprobe vcan
sudo ip link add vcan0 type vcan && sudo ip link set vcan0 up
BOAT_CAN_INTERFACES=vcan0 ./build/debug/src/gateway/grpc_gateway/boat_gateway

# Run gateway with physical CAN (e.g. PEAK PCAN-USB Pro FD)
sudo ip link set can0 up type can bitrate 500000
sudo ip link set can1 up type can bitrate 500000
BOAT_CAN_INTERFACES=can0,can1,vcan0 ./build/debug/src/gateway/grpc_gateway/boat_gateway

# Enable CAN FD (optional, requires FD-capable hardware)
sudo ip link set can0 up type can bitrate 500000 dbitrate 2000000 fd on

# Run gateway with plugins (PduRouter for PDU routing + CanTp for transport)
BOAT_CAN_INTERFACES=vcan0 \
  BOAT_NODE_PLUGINS=./build/debug/src/plugins/pdu_router/pdu_router.so,\
./build/debug/src/plugins/can_tp/can_tp.so?{\"iface\":\"vcan0\"} \
  ./build/debug/src/gateway/grpc_gateway/boat_gateway

# Run gateway with all kept plugins
BOAT_CAN_INTERFACES=vcan0 \
  BOAT_NODE_PLUGINS=./build/debug/src/plugins/pdu_router/pdu_router.so,\
./build/debug/src/plugins/can_tp/can_tp.so?{\"iface\":\"vcan0\"},\
./build/debug/src/plugins/someip/someip.so,\
./build/debug/src/plugins/tcp/tcp.so?{\"mode\":\"server\",\"listen_port\":8080,\"iface\":\"eth0\"} \
  ./build/debug/src/gateway/grpc_gateway/boat_gateway
```

## CAN Hardware Integration

The gateway distinguishes between virtual (`vcan*`) and physical CAN interfaces at startup:
- `vcan*` → `VirtualCanDriver` (wraps SocketCAN)
- all others → `PhysicalCanDriver` (reads sysfs for driver metadata, e.g. `peak_usb`)

The `ListBuses` gRPC response now returns per-interface metadata (driver name, state, FD support, bitrate).

### CLI CAN commands

The standalone `boat can` / `boat eth` Typer commands (including `list-buses` and
`detect`) were retired in favor of the unified `boat frame` command; there is
no CLI hardware-detection command anymore — inspect `/sys/class/net/` or use
`ip -d link show type can` directly.

```bash
# List interfaces the gateway has access to, with metadata (requires gateway)
boat frame list-ifaces
boat --json frame list-ifaces

# v8: Unified frame send/subscribe
boat frame send --bus-type can --can-id 0x123 --iface vcan0 --data AABBCCDD
boat frame subscribe --bus-types can
boat frame send --bus-type ethernet --ethertype 0x0800 --dst-ip 10.0.0.1 --data AABB
```

## Test

```bash
# C++ (Catch2)
ctest --preset release --output-on-failure
ctest --test-dir build/debug -R TestName --timeout 30 --output-on-failure
ctest --test-dir build/debug -N  # list tests

# Python SDK + CLI
pip install -e ./sdk/python[dev] && pip install -e ./cli
pytest sdk/python/tests cli/tests -v
```

Test binary naming: `boat_unit_*` (unit), `boat_integration_*`, `boat_hil_*`, `boat_determinism_seed`.

Manual verification runbooks for specific feature areas live under `boat-platform/docs/testing/`, e.g. `cantp-plugin-manager-verification.md` (CanTp gRPC bridge, multi-instance `--iface`, `NodePluginService`/`boat plugin list`, PDU-bus dispatch).

**Three distinct things are all called some variant of "test" in this
repo -- worth being precise about which one is meant:**
1. `ctest`/`pytest` above -- unit/integration tests of the codebase itself.
2. `test/*.md` (`test/Structure.md`: TestSuite → TestSet → TestCase) --
   the **manual**, hand-verified record used for release sign-off; every
   TestCase's Verdict/Result was produced by an actual human/agent
   running it against real hardware, not by any script. Never update
   these verdicts programmatically.
3. `boat test run <manifest.json>` (`boat_cli/test.py` +
   `sdk/python/boat/test/`) -- a separate, **automated** CI-style HIL
   suite runner: `EnvironmentConfig` (gateway/buses/DUT/plugins, JSON,
   `config/tests/env_*.json`) + `ManifestConfig` (setup/teardown actions
   + a list of test-file subprocesses to run, JSON) → `TestSuiteRunner`
   spins up (or connects to, if `gateway.binary` is left unset) one
   gateway per run, executes each test file with a timeout
   (sequentially or `--parallel N`), and writes a JSON/JUnit/HTML/
   optional-Allure report per test. `boat test list-environments/
   show-config/validate-config/check-env/run`. As of 2026-08-17 this had
   never actually been executed end-to-end in this repo -- verifying it
   for real surfaced and fixed two genuine bugs (a preflight check that
   always falsely reported "no driver detected" for any physical CAN
   interface, and a spawned-gateway port/tick that silently ignored the
   environment config). See `backlog/test_runner_backlog.md` for the
   full account, the real HIL test built to verify it
   (`config/tests/{env,manifest}_can_loopback.json` +
   `can_loopback_routing_test.py`), and open questions before anything
   builds further on top of this (e.g. surfacing it in `admin_gui`).

## Python SDK / CLI

```bash
# Editable installs
pip install -e ./boat-platform/sdk/python
pip install -e ./boat-platform/cli

# Regenerate gRPC stubs after proto changes
bash boat-platform/sdk/python/boat/stubs/generate_stubs.sh

# CLI
boat --help
boat sim init|start|pause|step|stop
boat frame send|subscribe|list-ifaces
boat scenario create|validate|get|list

# SDK (programmatic) -- omit the address to resolve BOAT_HOST env var,
# then "localhost:50051"; pass one explicitly to pin a specific gateway
from boat.client import BoAtClient
from boat.frame_node import FrameNode
client = BoAtClient()          # or BoAtClient("192.168.1.50:50052")
node = FrameNode()              # or FrameNode("192.168.1.50:50052")
node.send_can("vcan0", 0x123, b"hello")
```

All `*Node` classes (`FrameNode`, `BusNode`, `CanNode`, `EthernetNode`, `PduNode`,
`PduMessageNode`) and `BoAtClient` resolve their gateway address the same way:
explicit `address=` argument > `BOAT_HOST` env var > `localhost:50051`. This is
what keeps a node script portable across gateways/devices -- write it once with
no address hardcoded, then `BOAT_HOST=192.168.1.50:50052 python my_node.py`
points the same script at a different gateway without touching its code. The
`boat` CLI's `--host` flag follows the identical resolution order.

`FrameNode` survives its gateway restarting: `send()`/`send_can()`/etc.
re-raise on failure (so a caller sees the error) but also discard and
recreate the underlying gRPC channel first, and `subscribe()`'s background
stream auto-reconnects with capped exponential backoff on any failure.
Both matter -- just retrying an RPC against the *same* channel can stay
stuck behind grpc-python's own internal reconnect backoff (up to ~120s by
default) long after the gateway is reachable again; recreating the
channel forces an immediate fresh attempt instead. See
`backlog/nodes_backlog.md`'s "gateway restart left nodes stuck" entry for
the full incident (a real bug caught via a user manually stopping a
gateway with two different node types pointed at it).

**Plugin-based node scripts.** `FrameNode.send_can()`/`send_eth()` go
straight through the gateway's core `FrameSink` -- stateless, nothing to
lose on a restart. `PduNode`/`CanTpHandle` are different: they're thin
gRPC wrappers around a *plugin's* own service (`PduService`/
`CanTpService`), and the plugin holds its configuration (routes, N-SDU
sessions) in the gateway process's memory. A gateway restart wipes that
state along with the connection -- a node using either must re-run
`configure_route()`/`configure()` on reconnect, not just retry the data
call, or it'll keep silently talking to a route/session that no longer
exists server-side. `nodes/pdu_cyclic_publisher.py` (`pdu_router` plugin)
and `nodes/can_tp_trigger_sender.py` (`can_tp` plugin) are the reference
examples for this pattern -- each requires its plugin loaded via
`BOAT_NODE_PLUGINS` (see their docstrings for the exact flag) and prints a
clear "is the plugin loaded?" hint if `configure()`/`configure_route()`
fails. One asymmetry worth knowing if writing a new one:
`PduNode.configure_route()`/`send()` catch `grpc.RpcError` internally and
return `False`; `CanTpHandle.configure()`/`send()`/`subscribe()`
(`sdk/python/boat/can_tp.py`) do **not** -- they raise. A node built on
`CanTpHandle` needs its own `try/except` around those calls (see
`can_tp_trigger_sender.py`'s `ensure_configured()`) or a transient
disconnect crashes it instead of retrying.

`can_tp_trigger_sender.py` is a rework of an earlier version
(`can_tp_echo_responder.py`) that tried to echo back whatever ISO-TP
message it received -- discovered on real hardware to be untestable by
hand: reassembling an incoming multi-frame message requires being a full
ISO-TP *requester* yourself (send Consecutive Frames in response to the
plugin's own Flow Control), not something a single `cansend` can do. The
rework flips the direction: a plain CAN trigger frame (no plugin
involved) causes the node to `send()` a fresh incrementing-byte payload
through the plugin's own segmentation -- a human only needs to supply
Flow Control by hand to watch a real multi-frame exchange, which
`cansend` *can* do. See `backlog/nodes_backlog.md` for the full account.

## UI services

```bash
bash start_ui.sh   # launches all 10 services in background
bash stop_ui.sh    # kills them all
```

Each service is a standalone `python3 ui/<name>.py` FastAPI/uvicorn app with embedded HTML. SDK path is resolved via `sys.path.insert(0, ...)` relative to the script location.

## Launcher Agent (gateway administration, in progress)

`ui/launcher_agent.py` is a **separate** service from `ui/launcher.py` (which
stays as-is, single-instance, browser-facing). It's a per-host, headless REST
API for running **multiple** `boat_gateway` instances on one machine, each
with its own `BOAT_GRPC_PORT` (explicit or auto-allocated), CAN/Eth
interfaces, and `BOAT_NODE_PLUGINS` set. It's the foundation of a **federated
admin-tool architecture**: one small agent per host (no SSH, agents only ever
touch their own machine), with a single client aggregating several agents
over the network into one view. See `backlog/launcher_agent_backlog.md` for
status/known gaps and `test/LauncherAgent.md` for verified behavior.

```bash
BOAT_AGENT_PORT=8090 python3 ui/launcher_agent.py   # default port 8090

# Define + start an instance (grpc_port auto-allocated if omitted)
curl -X POST localhost:8090/api/instances -H 'Content-Type: application/json' \
  -d '{"name": "main", "can_ifaces": ["vcan0"], "node_plugins": [{"path": ".../pdu_router.so"}]}'
curl -X POST localhost:8090/api/instances/<id>/start

# Inspect / control
curl localhost:8090/api/instances                  # list all, with status/pid/port
curl localhost:8090/api/instances/<id>/log          # tail stdout/stderr
curl -X POST localhost:8090/api/instances/<id>/stop
curl -X PUT localhost:8090/api/instances/<id> -d '...'  # edit in place, refused (409) while running
curl -X DELETE localhost:8090/api/instances/<id>    # refused (409) while running
curl localhost:8090/api/host/info                   # interfaces, gateway binaries, plugins found on this host
```

`GET /api/host/info`'s `"plugins"` entries carry a `"config_schema"` alongside each `.so`'s `"path"`, read from an optional `<name>.schema.json` sidecar file next to it (`{"key": {"type","default","help",...}}`, written by the plugin's own author and copied there at build time by `add_boat_plugin()` -- `cmake/BoAtPlugin.cmake`). A compiled `.so` has nothing to import/introspect at runtime the way a node script's `build_parser()` does (see "Plugin-based node scripts" above), so this is a static, hand-maintained equivalent for the same purpose: `admin_gui`'s New/Edit Instance dialog builds one field per key from it. `can_tp.so`/`tcp.so`/`probe.so`/`someip.so` ship one; `pdu_router.so` takes no config and has none. A plugin with no sidecar just isn't offered per-key fields -- the dialog's flat JSON config field still works exactly as before.

`PUT` takes the same body shape as `POST` and replaces the stopped
instance's definition in place (same id) -- the same
edit-refused-while-running pattern `CanTpService`'s re-run-`configure`
already uses. `grpc_port` re-runs through the same allocator, with this
instance's own current port excluded from the collision check, so
resubmitting the same port (what the Edit dialog pre-fills) never conflicts
with itself.

`GET /api/instances` doesn't only return instances this agent created --
it also scans `/proc` for any other `boat_gateway` process on the host
(started by hand, by a script, or by a now-exited earlier agent) and
recovers its config from `/proc/<pid>/environ`, the same `BOAT_*` vars this
agent itself sets. Those entries get `"id": "external:<pid>"`,
`"managed": false` (vs. `true` for agent-created ones), and can only be
targeted by `stop` (`POST .../external:<pid>/stop` sends a plain
`SIGTERM`/pid-based signal, which works regardless of who spawned the
process) -- `start`/`edit`/`delete`/`GET` on an `external:` id are refused
with 400 (no stored definition to act on), and `log` returns a fixed
"not captured" message (stdout was never piped to this agent).

Not yet built: instance persistence across an agent restart (v1 is
in-memory only).

**Nodes** (scripts under `boat-platform/nodes/`, see below) get a parallel
but separate registry -- `GET /api/node-scripts` (discovery),
`GET/POST /api/nodes`, `GET/PUT/DELETE /api/nodes/<id>`,
`POST /api/nodes/<id>/start|stop`, `GET /api/nodes/<id>/log` -- same
create/edit/start/stop/delete shape as instances, but a node has no port to
allocate or ifaces/plugins of its own: it just needs `target_host` (sets
`BOAT_HOST` in the spawned process's env) and `extra_args` (a plain list of
CLI args, since each node script's own flags differ). No external-discovery
equivalent for nodes (arbitrary Python scripts aren't reliably identifiable
by process name the way `boat_gateway` is). Node subprocesses are spawned
with `PYTHONUNBUFFERED=1` -- without it, CPython fully block-buffers
stdout whenever it isn't a tty (which a piped subprocess never is), so a
node's ordinary `print()` output would sit invisibly in its own ~8KB libc
buffer until it filled or the process exited; only `stderr` writes (always
unbuffered in Python) showed up promptly. This was a real, previously
unnoticed gap -- every node's live log in `admin_gui`/`control_panel` was
effectively silent except for stderr warnings and the buffer's eventual
flush.

`GET /api/node-scripts` also carries each script's argument schema under
`"args"`, when discoverable: `_introspect_node_args()` imports the script
(never running `main()`) and, if it defines a module-level
`build_parser() -> argparse.ArgumentParser` (the convention
`cyclic_can_sender.py`/`can_request_responder.py` follow), turns
`parser._actions` into `[{"flag", "help", "default", "is_flag"}, ...]` --
skipping `--address`/`-h`. Any failure (no `build_parser()`, an import
error in this environment, anything) degrades to an empty list, never a
broken response. `admin_gui`'s New/Edit Node dialog uses this to build one
input field per argument (see below).

```bash
curl -X POST localhost:8090/api/nodes -H 'Content-Type: application/json' \
  -d '{"name":"responder","script_path":".../nodes/can_request_responder.py",
       "target_host":"localhost:50051","extra_args":["--iface","vcan0"]}'
curl -X POST localhost:8090/api/nodes/<id>/start
curl localhost:8090/api/node-scripts   # discovered boat-platform/nodes/*.py, with docstrings
```

## Admin GUI (PySide6 client)

`admin_gui/` is the desktop client for one or more launcher agents above —
host list (persisted to `~/.boat/admin_hosts.json`) → aggregated instance
table (with a **Managed** column, `Yes`/`No` per the `external:` discovery
above), polled every 2s on a background `QThread` → create/**edit**/start/
stop/delete, plus a log viewer and an **equivalent command line** panel
(the `BOAT_*=... ./boat_gateway` form of whatever instance is selected,
with a Copy button -- for pasting into a script) for the selected instance.
The New/Edit Instance dialog also runs the *reverse* direction: paste a
command line into **From command line** and **Parse && Fill** populates
every field from it. **Save Session…**/**Load Session…** write/read a
docker-compose-style YAML file (`session.py`, using `PyYAML`) capturing
every host plus its agent-managed instance definitions (never
`managed: false` rows -- nothing owned to save for those) *and* every
node *and* test-run definition (every node and every test run is
agent-created already, so all of them are saved); loading recreates each
one fresh (new id every time, never a resume) but leaves it **stopped**
-- unlike `docker-compose up`, nothing auto-starts. A node's saved
`target_host` is the concrete address it had already resolved to, so it
round-trips regardless of whether the gateway it points at is even in the
same session file; a test run's saved `manifest_path`/`env_config_path`
are the relative paths the agent itself reported (relative to
`boat-platform/` on that host), so a load just needs the same files to
still exist on that host. `agent_client.py`/`host_store.py`/`session.py`
have no Qt import, so they're usable/testable headlessly.

A second tab, **Nodes**, is the same shape (table, New/Edit/Start/Stop/
Delete, log viewer, equivalent command line) driving the `/api/nodes`
endpoints above instead. Its New/Edit dialog's **Script** dropdown is
populated from the selected host's `GET /api/node-scripts` and shows each
script's module docstring underneath; **Target gateway** is a dropdown
spanning *every configured host's* `GET /api/instances`, not just the
node's own -- same-host entries resolve to `localhost:<port>` (robust, no
DNS), cross-host entries resolve to that other host's own address (parsed
from its agent URL, tagged `[host-name]` in the label) since `localhost`
from the node's point of view would mean itself, not the other machine.
(A host added as `localhost:<agent-port>` rather than its real hostname/IP
will produce a `localhost` cross-host entry too, which is only actually
correct if the node's own host is that literal same box -- add hosts by
real address to target them from nodes elsewhere.) Typing a bare port
normalizes to `localhost:<port>`; typing a full `host:port` by hand always
works too. **Script arguments** builds one input field per argument the
selected script's schema declares (see `/api/node-scripts`'s `"args"`
above) -- a checkbox per boolean flag, a text field for everything else
with an `e.g. <default>` placeholder (falling back to the argument's help
text when the default is empty, e.g. `--data`). Empty/hidden for a script
with no discoverable schema. **Extra args** remains a free-text field
(`shlex.split()` on submit) as the escape hatch for anything outside that
schema -- populated per-argument fields are combined with it on submit;
in Edit mode, `_prefill_arg_fields()` walks the node's saved `extra_args`
and pulls recognized `--flag [value]` pairs back into their matching
field, leaving only the unrecognized leftovers here. Also has its own
**From command line** / **Parse && Fill** (`_parse_node_command_line()`,
shlex-based -- not the brace-aware tokenizer the Gateways one uses, since
node args can contain quoted values with no JSON to protect), which does
the same recognized/leftover split.

A third tab, **Test Runs**, treats one `boat test run <manifest.json>`
invocation (the automated CI-style HIL suite runner -- `boat_cli/test.py`,
distinct from the manual, hand-verified `test/*.md` TestSuite and from
ctest/pytest unit tests) as a third kind of agent-managed process, reusing
the exact subprocess-lifecycle plumbing built for Nodes (`TestRunInstance`/
`TestRunRegistry` in `launcher_agent.py`, its own registry -- deliberately
not folded into `NodeRegistry`). Table columns: Host, Name, ID, Manifest,
Environment, Result (PASS/FAIL/— once it's exited), Status, PID, Uptime.
The New/Edit dialog's **Manifest** dropdown is populated from the selected
host's `GET /api/test-manifests` (scans `boat-platform/config/tests/
manifest_*.json`) and **Environment** from `GET /api/test-environments`
(scans `env_*.json`) -- unlike a node's Target gateway, an environment
config is a local file read by `boat test run` on the same host, so there's
no cross-host resolution here. Selecting a manifest pre-selects its own
declared `environment_config` in the Environment dropdown (still
overridable), mirroring `boat test run <manifest> --config <override>`'s
own semantics: the manifest's own choice is the default, an explicit
override wins. **Extra args** is a flat free-text field (`shlex.split()` on
submit, e.g. `--stop-on-failure --parallel 2 -v`) rather than one field per
flag -- the `boat test run` flag surface is small and fixed regardless of
manifest, so there's no per-manifest schema to build fields from the way
node scripts have. The agent locates the `boat` CLI itself via
`_discover_boat_cli()` (`BOAT_CLI_BIN` env override → `shutil.which("boat")`
→ literal `~/.local/bin/boat` fallback, since a non-interactively-started
agent process may not have `~/.local/bin` on `PATH` even when `boat` is
installed there) and reports it back as `"boat_cli_bin"` in
`GET /api/host/info`. A **Report directory** field under the log viewer
shows the run's `report_dir` (relative to `boat-platform/` on the *agent's*
host) with a Copy button -- deliberately no "Open" button, since in the
federated multi-host case admin_gui may not be running on that same
machine. A **View Report** button opens `TestReportDialog`, which instead
fetches the actual report content over HTTP (`GET /api/test-runs/{id}/
report`, reading back the per-test `report.json` files
`TestSuiteRunner._run_single_test()` wrote under `report_dir` -- there's
no single aggregate report file, one subfolder per manifest test entry)
and renders a verdict-colored tree (one row per test: id, verdict,
duration, summary) with a per-test detail pane (steps, assertions,
description, which raw artifact files -- `report.html`/`.junit.xml`/
stdout/stderr -- exist alongside it). This is what actually retires the
Report directory field's own federated-host limitation for the report
*content* specifically: the agent reads the file, the client never needs
filesystem access to that host. Test run definitions are also captured by
**Save Session…**/**Load Session…**, same as instances and nodes (see
above) -- wired in a later follow-up after this tab first landed.

A fourth tab, **Interfaces**, manages network interfaces on a host --
create/delete vcan and veth pairs, bring any interface up/down, and
configure a `type can` link's bitrate (virtual or physical -- the exact
`ip link set ... type can bitrate ...` commands `boat_cli/
bus_setup_context.py`'s "Physical CAN" section documents). Table columns:
Host, Name, Type, Up, Operstate, MAC -- one row per host per interface,
aggregating `GET /api/interfaces` across every configured host on the
same 2s poll cycle as the other tabs, so it reflects real system state
including physical hardware and interfaces created by any other means
(`ip` by hand, `ui/launcher.py`'s own equivalent endpoints -- either tool
shells out to the same commands against the same host). **New vcan…**/
**New veth…** pick a host + name (veth auto-derives a `<name>_peer` for
the pair's other end, live-validated in the dialog -- see the ifname
length note below); **Configure CAN…** opens a small dialog for bitrate
+ optional CAN FD data-bitrate against the selected interface; **Up**/
**Down** act on any selected interface, including physical hardware
(a confirmation guards **Down** specifically, since bringing down an
interface a running gateway is actively using will disrupt it); **Delete**
is refused for anything but a vcan/veth row -- a real network device isn't
something this tool should be able to remove, only reconfigure or toggle.
Interface names are capped at 15 characters by the kernel (`IFNAMSIZ`),
checked both client-side (live in the New veth… dialog, since the
`_peer` suffix is what most often pushes a name over the limit) and
server-side, with a clear message either way instead of `ip`'s own
cryptic `"name" not a valid ifname` -- found by hitting exactly that
during this feature's own real-hardware verification.

```bash
pip install -r admin_gui/requirements.txt   # Debian/Ubuntu: add --break-system-packages
sudo apt install libxcb-cursor0             # Linux only -- system dep Qt6's xcb plugin needs
python3 admin_gui/main.py
```

See `admin_gui/README.md` for usage and `test/AdminGui.md` for what's
verified. Headless verification uses `QT_QPA_PLATFORM=offscreen` — exercises
every code path (construction, polling, every button's action method,
including ones gated behind a modal confirmation) without a display. A real
`xcb` render pass (Xvfb + a real screenshot) has also confirmed the layout
looks right, and specifically caught the missing `libxcb-cursor0` system
dependency above.

## Quirks & gotchas

- **Plugin ABI v8** (current, on `ABI_v8_frame_unification_and_major-refactor`):
  - Unified `BoatFrame` type (CAN, CANFD, Ethernet, TCP, PDU)
  - Plugin vtable (9 fields): `initialize`, `on_tick`, `shutdown`, `set_publisher`, `set_bus_publisher`, `set_pdu_publisher`, `on_frame`, `set_frame_publisher`, `declared_buses`
  - `BOAT_PLUGIN_ABI_VERSION = 8` — v7 plugins rejected with clear error
  - `PduRouter` is a plugin (`pdu_router.so`), loaded by the gateway
  - `boat plugin list` shows loaded plugins from **both** `PluginManager` instances (sim-scoped + always-on `node_manager`) in one table with a `scope` column — `PluginService` (register/list/info/unload) only ever reaches the sim-scoped one; `NodePluginService` (list/info/unload, no register) reaches `node_manager`. `boat plugin info|unload` need `--scope {sim,node}`; `--scope node` unload additionally needs `--yes`. See `README.md`'s "Dual PluginManager".
  - `FrameService` gRPC provides unified send/subscribe for all bus types
  - `boat frame send` / `boat frame subscribe` CLI replaces `boat can` / `boat eth`
  - TCP plugin uses v8 ABI (config-driven, gateway-resident); old C API removed
  - `BoatCanFrame`, `BoatEthFrame` and their associated typedefs are removed
  - Architecture reference: `boat-platform/docs/architecture/system-architecture.md`

- Gateway binary path: `build/{preset}/src/gateway/grpc_gateway/boat_gateway`
- `boat` CLI entry point (boat_cli/main.py): Typer app with subcommands. Uses `BoAtClient(address)` from `boat-py`.
- `python3 -m boat` dispatches: subcommands `can|pdu|eth|db` → `boat/cmd.py` (one-shot), anything else → `boat/cli.py` (interactive REPL).
- Proto stubs in `sdk/python/boat/stubs/boat/v1/` must be regenerated when proto files change (`generate_stubs.sh`).
- iceoryx2 requires `cargo` (Rust) at build time only; the resulting shared-memory IPC is used at runtime for large payloads (>4KB).
- HIL tests need `BOAT_HIL_ENABLED=1` and a real or virtual CAN interface (`vcan0`).
- Determinism test runs simulation twice with same seed and expects bit-exact output.
- Coverage report: `gcovr --root . --exclude build/ --xml coverage.xml`.
- Release packaging: `cpack -G "TGZ;DEB;RPM"`.
- Docker images pushed to `ghcr.io/boat-platform/boat-platform:*`.

## PDU Features

### I-PDU Groups

Groups enable/disable sets of PDUs at runtime. PDUs in a disabled group are silently dropped.

```bash
# Create a group with two PDUs, disabled
boat pdu group --id 1 --name "Safety" --pdu 0x100 --pdu 0x200 --disabled

# Create a group with two PDUs, enabled (--enabled/--disabled toggle)
boat pdu group --id 2 --name "Chassis" --pdu 0x300 --pdu 0x400 --enabled

# Enable/disable at runtime
boat pdu enable-group --id 1
boat pdu disable-group --id 1

# List groups
boat pdu list-groups

# Programmatic (Python)
node = PduNode()
node.configure_group(group_id=1, name="Safety", pdu_ids=[0x100, 0x200], enabled=False)
node.enable_group(1)
node.disable_group(1)
groups = node.list_groups()
```

### Transmission Schedules

Routes can specify automatic sending behavior (Cyclic, OnChange, Mixed with n-times fast repetitions).

```bash
# Cyclic: send every 100ms
boat pdu route --id 0x100 --transport can --iface vcan0 --send-type cyclic --cycle-ms 100

# OnChange: send only when payload changes, with 3 fast repetitions at 10ms intervals
boat pdu route --id 0x200 --transport can --iface vcan0 --send-type onchange --fast-ms 10 --reps 3

# Mixed: cyclic background at 200ms + OnChange triggers with 2 fast reps at 20ms
boat pdu route --id 0x300 --transport can --iface vcan0 --send-type mixed --cycle-ms 200 --fast-ms 20 --reps 2

# Additional optional parameters for routes:
#   --can-id N         CAN frame ID override (default: same as pdu_id)
#   --ethertype 0x0800 EtherType (default 0x88B5 sim-only; set 0x0800 for IPv4)
#   --src-ip A.B.C.D   Source IP (enables IP/UDP/IpduM transport)
#   --dst-ip A.B.C.D   Destination IP (required for IP/UDP transport)
#   --src-port N       UDP source port
#   --dst-port N       UDP destination port
#   --ttl N            IPv4 TTL / IPv6 Hop Limit (default 64)
#   --vlan N           802.1Q VLAN ID

# The gateway's OnTick() drives the transmission engine (1ms default tick interval,
# set via BOAT_NODE_TICK_MS).  The tick interval is the minimum cycle time — e.g.
# a 1ms tick supports cycle_ms >= 1ms.  For sub-ms precision use BOAT_NODE_TICK_US
# (e.g. BOAT_NODE_TICK_US=100 for 100μs ticks, uses high-precision timerfd backend).
# Lower intervals increase CPU load — 100μs tick on a typical x86 adds ~1-2% CPU per
# 10 scheduled PDUs.
#
# Timer backend: Linux timerfd with absolute-time scheduling, no drift.
# TickTimer::Create always returns a TimerfdTickTimer on this platform.

# ── STOP sending ──────────────────────────────────────────────────────────

# Option A: Reconfigure with --send-type none to keep the route but stop auto-sends
boat pdu route --id 0x100 --transport can --iface vcan0 --send-type none

# Option B: Remove the route and schedule entirely
boat pdu remove-route --id 0x100

# Option C: Disable the PDU's group (keeps config, silences the PDU)
boat pdu group --id 1 --pdu 0x100
boat pdu disable-group --id 1
```

### COM Signal Library (C++)

Bit-level signal packing with Intel/Motorola support, physical-to-raw conversion, AUTOSAR E2E CRC.

```cpp
#include "pdu/com/com_signal.h"
using namespace boat::hil::com;

MessageDef msg;
msg.length_bytes = 8;
SignalDef sig;
sig.name = "Speed";
sig.bit_length = 16;
sig.start_pos = 0;
sig.is_motorola = false;  // Intel
sig.factor = 0.5;
sig.offset = 0.0;

auto packed = PackSignals(msg, {{"Speed", 100.0}});
// unpacked["Speed"] == 100.0
auto unpacked = UnpackSignals(msg, packed.data(), packed.size());

// E2E CRC
uint8_t crc8 = E2eCrc8(data, len);
uint16_t crc16 = E2eCrc16(data, len);
uint32_t crc32 = E2eCrc32(data, len);
```

### CanTp — CAN Transport Protocol (Plugin)

ISO 15765-2 segmentation/reassembly for PDUs larger than 8 bytes. Operates as a `BOAT_NODE_PLUGINS` node plugin using the v8 ABI (`on_frame`/`set_frame_publisher`). `boat can-tp configure`/`send` talk to the live plugin instance inside the gateway process via the `CanTpService` gRPC service (`CanTpServiceImpl` looks it up via `PluginManager::FindService("can_tp:" + iface)`) — there is no offline/local mode.

Each connection is identified by `nsdu_id` and represents a session between
`source_addr` (this node) and `target_addr` (peer node) — both required,
non-zero, and set only via `configure`; there is no fallback to `nsdu_id`
(a single-ID session, one CAN ID for both directions, is expressed by
passing the same value for both explicitly). `send`/`remove`/`subscribe`
then only need `--nsdu-id` — addressing lives entirely in the prior
`configure` call. `nsdu_id` must be numeric (`int(x, 0)` — hex or decimal,
not a symbolic name).

```bash
# Build plugin
cmake --build --preset debug

# Run gateway with CanTp plugin
BOAT_NODE_PLUGINS=./build/debug/src/plugins/can_tp/can_tp.so?{"iface":"vcan0"} \
  BOAT_CAN_INTERFACES=vcan0 \
  ./build/debug/src/gateway/grpc_gateway/boat_gateway

# Configure a session (dual-ID, tester→ECU) -- also how you edit one:
# re-running configure for an already-configured nsdu_id overwrites it in place
boat can-tp configure --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8 --bs 0 --stmin 0

# Send large PDU via CanTp CLI -- SF or FF+CF is chosen automatically by
# payload length. No addressing here -- it comes from configure, above.
boat can-tp send --nsdu-id 0x7E0 --data 0123456789ABCDEF...

# Stream decoded RX payloads (completed SF, or fully reassembled FF+CF...)
boat can-tp subscribe --nsdu-id 0x7E0

# Delete a configured session (fails while a multi-frame transfer is in flight)
boat can-tp remove --nsdu-id 0x7E0

# List currently-configured sessions (nsdu_id, addrs, rx/tx state) --
# across every loaded instance, or scoped to one with --iface
boat can-tp list-sessions

# Stream N_Result error/abort events (N_Bs/N_Cr timeout, wrong CF sequence
# number, buffer overflow) -- fires instead of a subscribe() event for an
# attempt that didn't complete
boat can-tp subscribe-errors --nsdu-id 0x7E0
```

**N_Bs/N_Cr watchdogs.** Of ISO 15765-2's six timing parameters, only N_Bs
(TX waiting for FC) and N_Cr (RX waiting for the next CF) are enforced — the
two whose expiry actually leaves a session stuck forever. `--n-bs-ms`/
`--n-cr-ms` on `configure` (default 1000ms each, ISO default; OBD-II/ISO
15765-4 uses 75/150). N_As/N_Ar have no analogue in this software transport
(`frame_publish_fn` is synchronous — nothing to time out waiting for);
N_Br/N_Cs are soft performance targets, not correctness bugs, and aren't
enforced. The single `n_bs_ms` value (`kDefaultTimeoutMs` in
`can_tp_plugin.cpp`, resolved from the `n_bs_ms=0` "use ISO default"
sentinel by `resolve_timeout_ms()`) governs *every* wait for a Flow
Control on a TX connection, not just the first one: the deadline
(`tx_fc_deadline`) is (re)armed after sending the First Frame, again at
every Block Size boundary once a full block of Consecutive Frames has
gone out (still waiting for FC = still `TX_WAIT_FC`, ISO 15765-2 §9.8
doesn't distinguish the two cases), and again on each FC(Wait) response
(which *extends* the deadline rather than aborting — an unresponsive peer
that keeps sending WT can hold a session open indefinitely; only genuine
silence trips the watchdog). Relevant when hand-driving a session via
`cansend` (`nodes/can_tp_trigger_sender.py`, see its docstring): you have
~1000ms after the First Frame *and* after every subsequent block to get
the next Flow Control frame out by hand before the plugin aborts the
transfer.

**Addressing modes** (`--addressing-mode {normal,extended,mixed}`, ISO
15765-2 §10.3). `normal` (default) has no address byte — `source_addr`/
`target_addr` are the literal CAN IDs. `extended`/`mixed` prepend an address
byte (N_TA/N_AE respectively — wire-identical, only the AUTOSAR/ISO semantic
label differs) and, combined with `--address-byte` (independently settable,
not derived from `target_addr`), let multiple connections share one
`target_addr`, disambiguated by that byte — the actual point of those modes.
11-bit vs. 29-bit CAN ID isn't a separate mode — any `source_addr`/
`target_addr` value > `0x7FF` gets the CAN extended-frame flag automatically,
so conventional 29-bit "Normal Fixed" (`0x18DA<TA><SA>`/`0x18DB<TA><SA>`) and
"Mixed 29-bit" (`0x18CE<TA><SA>`/`0x18CD<TA><SA>`) IDs are just constructed by
the caller and passed like any other CAN ID.

**CAN FD and padding.** `--dlc 64` for CAN FD (SF/FF gain a 2-byte PCI escape
format per ISO 15765-2:2016 Table 11, since a 1-byte nibble-encoded length
caps out at 7); `--brs` sets the Bit Rate Switch flag (not forced on, since
not every CAN FD bus has a distinct data-phase bit rate configured). Every
emitted frame is padded to the connection's `can_dlc` with `--pad-byte`
(default `0xCC`, ISO/AUTOSAR default).

**Multiple instances (one per CAN interface).** Each loaded CanTp instance is
bound to exactly one interface at load time and registers itself under an
iface-scoped service name. Load one entry per interface in `BOAT_NODE_PLUGINS`
(comma-separated, each with its own `?{"iface":...}` config):

```bash
BOAT_NODE_PLUGINS='./build/debug/src/plugins/can_tp/can_tp.so?{"iface":"vcan0"},./build/debug/src/plugins/can_tp/can_tp.so?{"iface":"vcan1"}' \
  BOAT_CAN_INTERFACES=vcan0,vcan1 \
  ./build/debug/src/gateway/grpc_gateway/boat_gateway

boat can-tp configure --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8 --iface vcan1
boat can-tp send --nsdu-id 0x7E0 --data 0123 --iface vcan1
```

`--iface` is only *required* once more than one instance is loaded — while
there's exactly one, omitting it falls back to that instance automatically.
That fallback is fragile across config changes: a command that worked without
`--iface` today will start failing with a `FAILED_PRECONDITION` "multiple
CanTp instances loaded... specify --iface" error the moment a second
interface is added, with no other warning. Prefer always passing `--iface`
in scripts/automation that might later run against a multi-interface gateway.

The plugin's raw C ABI (`boat/can_tp.h`'s `can_tp_configure`/`can_tp_send`)
is still what the gRPC layer calls into internally, and remains available for
plugins/tests that link against `can_tp.so` directly in-process (see
`src/tests/hw_can_tp_hil_test.py`) — but it operates on whatever instance's
`ctx` you pass it, with no gRPC/CLI path involved.

**Two ways to trigger a send.** CanTp declares `["can","pdu"]` in
`declared_buses()`, so a send can be triggered either via the bespoke
`CanTpService.Send` RPC (above), or generically via any `BOAT_BUS_PDU`
frame whose `pdu_id` matches a configured connection's `nsdu_id` (e.g.
`boat frame send --bus-type pdu --iface vcan0 --id 0x7E0 --data 0123`) --
symmetric with the RX side, which already emits reassembled I-PDUs as
`BOAT_BUS_PDU` frames the same way (`pdu_id = nsdu_id`). **The PDU-bus path
requires `iface` to be set and match the target instance** -- unlike the
CAN-bus RX path, where an absent iface means "accept from anyone". This is
deliberate: the plugin's own RX-reassembly-complete handler republishes
onto the same PDU bus with no iface set, and if an absent iface were
accepted here that internal echo would loop straight back into
`can_tp_send()` and re-transmit the payload it just finished receiving.
Practical implication: if you drive CanTp via the generic PDU bus rather
than `CanTpService`, `nsdu_id` must stay unique across every CanTp instance
sharing that PDU-bus namespace, since there's no ambiguity-detection here
the way `CanTpService.Send`'s "multiple instances loaded" error provides.

### SOME/IP Plugin

Service-oriented middleware over Ethernet UDP. Listens on configured ports, responds to SOME/IP requests, supports Service Discovery.

```bash
BOAT_NODE_PLUGINS=./build/debug/src/plugins/someip/someip.so \
  BOAT_ETH_INTERFACES=veth0 \
  ./build/debug/src/gateway/grpc_gateway/boat_gateway
```

Config: `{"sd_port": 30490}`. Registers offered services; responds to REQUEST messages with RESPONSE echoes.

### Probe Plugin (gateway conformance)

A test/diagnostic plugin that verifies the gateway's frame plumbing from *inside*
the dispatch loop — things a gRPC client can't observe. Useful for HW bring-up.

It checks: **delivery** (`on_frame` fires for declared buses), **filtering**
(no deliveries for undeclared buses → `unexpected_bus` stays 0), **self-sent
tagging** (a frame it publishes returns with `SELF_SENT` set), and **round-trip**
(active mode injects a tagged CAN frame and asserts the self-sent echo arrives
within a timeout — PASS/FAIL). Results go to stderr *and* the signal bus
(`probe.rx_total`, `probe.self_echoes`, `probe.unexpected_bus`, `probe.checks_pass`,
`probe.checks_fail`, …), watchable live via `boat`/dashboards.

```bash
# Passive observer (never injects — safe alongside real ECU traffic)
BOAT_CAN_INTERFACES=vcan0 \
  BOAT_NODE_PLUGINS=./build/debug/src/plugins/probe/probe.so?{\"mode\":\"passive\",\"buses\":[\"can\"]} \
  ./build/debug/src/gateway/grpc_gateway/boat_gateway

# Active self-test on vcan0 (injects probe frames, asserts self-sent echo)
BOAT_CAN_INTERFACES=vcan0 \
  BOAT_NODE_PLUGINS=./build/debug/src/plugins/probe/probe.so?{\"mode\":\"active\",\"iface\":\"vcan0\",\"probe_id\":\"0x7FF\",\"probe_period_ticks\":1000} \
  ./build/debug/src/gateway/grpc_gateway/boat_gateway
```

Config keys: `iface` (default `vcan0`), `buses` (default `["can"]`), `mode`
(`passive`|`active`|`both`, default `both`), `probe_id` (default `0x7FF`),
`probe_period_ticks` (1000), `echo_timeout_ticks` (50), `report_period_ticks`
(5000). It's also the canonical minimal v8 plugin example. Note: periods are in
node ticks (tick length = `BOAT_NODE_TICK_MS`/`_US`).

> Plugin config JSON may contain commas — `BOAT_NODE_PLUGINS` is split
> brace-aware, so commas inside a `{...}` config do not split the entry.

## Replay System (ABI v8) — Core-Sink Architecture

The replay system reads trace files (.asc, .blf, .pcap), converts them to protobuf
`boat.v1.Frame` records, and transmits them through the single core `FrameSink`.

### Architecture overview

```
Import                Replay Engine                Core Sink                 Hardware
───────             ───────────────             ──────────────────         ────────
.asc/.blf/.pcap  →  convert_to_binary()     →  Frame protobuf records
                        │                          │
                        ▼                          ▼
                 ImportTraceData gRPC      ReplayLoop parsing
                        │                          │
                        ▼                          ▼
                   trace_store               ProtoToCoreFrame()
                      (mmap)                      │
                                                  ▼
                                     replay_controller.SetEventForwarder
                                                  │  (core::Frame)
                                                  ▼
                                          ┌─────────────────┐
                                          │ FrameSink::      │  single frame→wire sink
                                          │ Publish()        │  (routes by bus_type)
                                          └───────┬─────────┘
                                                  ▼
                                   can_registry.SendFrame(iface)
                                   eth_registry.SendFrame(iface)
                                                  │  writes wire + DispatchRx
                                                  ▼
                            Physical bus (vcan0, can1, eth0,...)  +  plugins' on_frame
```

The registry's RX dispatch delivers each replayed frame to plugins' `on_frame`
(tagged self-sent), so plugins still observe replayed traffic — no forwarder
plugin is involved.

### Key components

#### Trace format

Each event is stored as a length-delimited `boat.v1.Frame` protobuf record:
```
┌──────────────┬───────────────────┐
│ uint32 len   │ Frame protobuf    │
│ (4 bytes)    │ (variable)        │
└──────────────┴───────────────────┘
```

The `Frame` message contains full bus-agnostic metadata: `bus_type`, `iface`,
`timestamp_ns`, `payload`, plus CAN metadata (`can_id`, `dlc`, `flags`,
`channel`) or Ethernet metadata (`dst_mac`, `src_mac`, `ethertype`, `src_ip`,
`dst_ip`, `ip_version`, `flags`).

Import is hardware-independent: `convert_to_binary()` does not bake a target
interface into CAN records (it stores the original trace `channel` in
`CanMetadata.channel` instead, leaving `Frame.iface` empty), and Ethernet
records only get an `iface` baked in if the caller explicitly set
`eth_iface`/`buses` on `TraceReplayer` (the CLI's `boat replay import` never
does). Interface (and MAC) targeting is a replay-time decision, resolved in
`ProtoToCoreFrame` (`replay_engine.cpp`) from `ReplayConfig.buses` /
`.eth_iface` / `.mac_map` — see below. This means the same imported trace can
be replayed against different interfaces/MACs without re-importing it.

#### FrameSink (`src/gateway/grpc_gateway/frame_sink.{h,cpp}`)

The single path a frame reaches a bus. It routes a `core::Frame` by `bus_type`
to `CanBusRegistry` / `EthernetBusRegistry`. Every producer uses it — plugins
(via `frame_publish_fn`), replay (`SetEventForwarder`), and gRPC
`FrameService.SendFrame`. There is no forwarder plugin and no `can_io`
direct-socket alternative. Loopback prevention lives in the registry send path
(the one site that tags `BOAT_CAN_FLAG_SELF_SENT` / `BOAT_ETH_FLAG_SELF_SENT`).

#### ReplayController (`src/replay/`)

Manages the replay thread (`ReplayLoop`), timing (absolute-time `timerfd`
scheduling), pause/resume/seek, loop with gap, and event buffering for
gRPC streaming via `ConsumeEvents()`.

### Usage

#### Import
```bash
# Convert and upload a trace file with filters
boat replay import trace.asc --trace-id myrun \
  --channel 1                     # CAN channel filter
  --id 0x100,0x200                # CAN ID filter
  --ip-map 10.0.0.1=192.168.0.1  # IP rewriting (Ethernet)
  --ethertype ipv4                # EtherType filter
  --protocol udp                  # L4 protocol filter
  --ip-filter 192.168.0.0/24      # Post-rewrite IP filter
  --src-ip-filter 192.168.0.100   # Source IP filter
  --dst-ip-filter 192.168.0.101   # Destination IP filter
  --src-port 67,68                # Source port filter
  --dst-port 30490                # Destination port filter
  --replay-src-ip 10.0.0.1        # IP override for reconstruction
  --replay-dst-ip 10.0.0.2        # IP override for reconstruction
```

#### Stream (replay)
```bash
# Replay an imported trace -- interface/MAC targeting happens here, not at
# import time, so the same trace_id can be replayed with different flags
# against different hardware without re-importing.
boat replay stream --trace myrun \
  --speed accelerated             # real-time / accelerated / step
  --multiplier 2.0                # speed factor
  --loop 1000                     # loop with 1s gap between passes
  --verbose                       # print per-frame hex
  --buses vcan0,can1              # CAN channel->interface mapping (ch1->vcan0, ch2->can1)
  --eth-iface eth0                # Ethernet target interface (overrides broadcast-to-all)
  --mac-map 192.168.0.1=02:de:ad:be:ef:01  # rewritten-IP->MAC mappings
```

`boat replay start` accepts the same `--buses`/`--eth-iface`/`--mac-map`/
`--loop` flags (it just doesn't block to stream events afterward).

#### `boat trace replay` (direct, CAN-only)
```bash
# Sends each frame individually via gRPC, paced in real time by the client
# process -- no import/upload, no server-side session, no pause/resume/seek.
# CAN (.asc/.blf) only; .pcap is rejected -- use `boat replay import` +
# `boat replay start`/`stream` above for Ethernet.
boat trace replay trace.asc --loop 250 --verbose --buses vcan0
```

### Loopback prevention

The registry send path is the single site that tags locally-sent frames to
prevent infinite dispatch loops:

- **CAN**: `CanBusRegistry::SendFrame()` sets `BOAT_CAN_FLAG_SELF_SENT = 0x08`
  in `CanFrame.flags`, then `DispatchRx`. Propagates through `core::Frame` →
  `BoatFrame.meta.can.flags`.
- **Ethernet**: `EthernetBusRegistry::SendFrame()` sets
  `BOAT_ETH_FLAG_SELF_SENT = 0x01` in `EthernetFrame.flags`, then `DispatchRx`.
  Propagates through `core::Frame` → `BoatFrame.meta.eth.flags`.

Plugins that only want wire RX check these flags in `on_frame` to skip their own
echoes. Because there is no forwarder plugin re-transmitting frames, echo-safety
is no longer per-plugin discipline — it's owned by the one registry send path.

### Frame dispatch filtering

`PluginManager::DispatchFrame()` calls `on_frame` **only** on plugins whose
`declared_buses()` includes the frame's `bus_type` (parsed once at load into a
bitmask). A plugin that declares nothing receives all bus types. This avoids
fanning every frame out to every plugin.

## AUTOSAR specification reference

AUTOSAR specs are available locally via `spec/` (symlinked, gitignored — populate on each machine):

```bash
# Content expected under spec/:
spec/
├── GUIDE.md          # Search workflow
├── latest/           # 266 PDFs
├── text/             # Flat UTF-8 text (97 MB)
└── search.db         # SQLite FTS5 index (115 MB)
```

Search workflow (see `spec/GUIDE.md` for details):
```bash
# Find which document covers your topic
python3 -c "
import sqlite3
conn = sqlite3.connect('spec/search.db')
cur = conn.execute(
    \"SELECT rank, filename FROM docs WHERE docs MATCH ? ORDER BY rank LIMIT 5\",
    ('\"remote frame\" CAN',)
)
for rank, fname in cur:
    print(f'  [{rank:.1f}] {fname}')
"

# Read the relevant section
grep -n -B 2 -A 10 -i "remote frame" spec/text/AUTOSAR_SWS_CAN_Driver.txt
```

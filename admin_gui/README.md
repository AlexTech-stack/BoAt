# BoAt Admin

A PySide6 desktop client for one or more `ui/launcher_agent.py` instances.
Add a host per machine that runs gateways; the app polls each host's REST API
and shows one aggregated table of every gateway instance across all of them,
across three tabs: **Gateways** (`boat_gateway` processes), **Nodes**
(scripts under `boat-platform/nodes/`, see `AGENTS.md`), and **Test Runs**
(`boat test run <manifest.json>` invocations, the automated CI-style HIL
suite runner).

No SSH is involved anywhere in this app — it only ever calls each agent's own
HTTP API, and each agent only ever touches processes on its own machine. See
`AGENTS.md`'s "Launcher Agent" section and `backlog/launcher_agent_backlog.md`
for the architecture rationale and current scope/gaps.

## Install & run

```bash
pip install -r admin_gui/requirements.txt      # Debian/Ubuntu with PEP 668
                                                 # ("externally-managed-environment"):
                                                 # add --break-system-packages, or use a venv
python3 admin_gui/main.py
```

Runs on any platform PySide6 supports (Windows, Linux, macOS) — it's a plain
HTTP client, so it doesn't need to run on the same machine as any gateway.

**Linux only:** Qt6's `xcb` platform plugin additionally needs a *system*
library that `pip install pyside6` does not provide:

```bash
sudo apt install libxcb-cursor0
```

Without it you'll see `qt.qpa.plugin: Could not load the Qt platform plugin
"xcb"` and the app aborts immediately on launch. This is a one-time,
per-machine system dependency, unrelated to the Python packages above (and
unrelated to whether you're on a real desktop or connecting via RDP/VNC —
either way it's the same `xcb` plugin doing the rendering).

![admin_gui main window, showing a host with a running instance](docs/screenshot.png)

![New Instance dialog, showing the dropdown pickers for interfaces and plugins](docs/new_instance_dialog.png)

## Usage

1. **Add Host** — display name + agent URL (e.g. `agn-testcomputer:8090`;
   `http://` is added automatically if omitted). Each host needs
   `ui/launcher_agent.py` already running there (`python3 ui/launcher_agent.py`,
   default port 8090). Hosts persist across runs in `~/.boat/admin_hosts.json`.
   **Save Session…** / **Load Session…** capture/restore *all* hosts and
   their agent-managed instance and node definitions at once,
   docker-compose-style — see the dedicated section below.
2. The instance table (Host, Name, ID, Port, Status, PID, **Managed**,
   Interfaces, Plugins, Uptime) aggregates every instance from every added
   host, refreshing every 2 seconds. A host's dot in the host list is
   filled (●) when reachable, hollow (○) when not. **Plugins** shows each
   plugin's `.so` basename, with the interface it's bound to in brackets
   when its config carries one (`can_tp.so [vcan0]`). **Managed** is
   `Yes` for instances this agent created, or `No` for a `boat_gateway`
   the agent found already running on that host but didn't start itself
   (started by hand, by a script, or by a now-exited earlier agent process)
   — its port/interfaces/plugins are recovered from the process's own
   environment (`/proc/<pid>/environ`) and shown the same as any other row.
   `Edit`/`Start`/`Delete` don't apply to an unmanaged row (there's no
   stored definition to act on) and are refused with a clear message;
   `Stop` still works — it's a plain signal by pid, which doesn't care who
   spawned the process.
3. **New Instance…** picks a host, then defines CAN interfaces, Eth
   interfaces, and node plugins via a dropdown + **+ Add** pattern: each
   dropdown is pre-populated from that host's `GET /api/host/info`
   (interfaces it actually has, plugin `.so` files discovered under its
   `build/{debug,release}`), but stays editable — type anything not listed
   (e.g. an interface you're about to create) and **+ Add** still accepts
   it. Selecting a plugin also builds a **Plugin config** field per key its
   config schema declares, if it has one — a `<name>.schema.json` sidecar
   file next to its `.so` (see `cmake/BoAtPlugin.cmake`), since a compiled
   `.so` has nothing to introspect at runtime the way a node script's
   `build_parser()` does. Each field shows an `e.g. <default>` placeholder
   (an enum key renders as a dropdown of its allowed values instead); a
   plugin with no sidecar schema — or any key not covered by one — falls
   back to the flat optional JSON config field alongside the path (e.g.
   `{"iface": "vcan0"}`), same as before this existed. **Remove selected**
   drops an already-added entry. Also: an optional explicit gRPC port
   (blank = auto-allocated by that host's agent), and an optional gateway
   binary override.
   At the top, **From command line** takes a pasted
   `BOAT_CAN_INTERFACES=... BOAT_NODE_PLUGINS=... ./boat_gateway` line
   (exactly what the "Equivalent command line" panel below produces) and
   **Parse && Fill** populates every field above from it in one shot —
   the reverse direction of that panel.
4. Select a row, then **Start** / **Stop** / **Delete** act on it (Delete is
   refused by the agent while the instance is running). **Edit…** reopens
   the same dialog pre-filled with that instance's current definition
   (host locked -- an instance can't move agents) and submits an update in
   place, same id, same rules as Delete: refused by the agent while running.
5. Below the log panel, **Equivalent command line** shows the
   `BOAT_CAN_INTERFACES=... BOAT_NODE_PLUGINS=... ./boat_gateway` form of
   whatever instance is selected -- for copying into a script (**Copy**
   button included). Updates automatically as the selection or that
   instance's config changes.

## Nodes tab

![Nodes tab, showing a running node instance targeting a gateway](docs/nodes_tab.png)

![New Node dialog, showing the Target gateway dropdown and paste field](docs/new_node_dialog.png)

Same shape as the Gateways tab (table, New/Edit/Start/Stop/Delete, log
viewer, equivalent command line), driving script processes under
`boat-platform/nodes/` instead of `boat_gateway`. Differences from
Gateways, since a node has no port to allocate or ifaces/plugins of its
own:

- **New Node…**'s **Script** dropdown is populated from the selected
  host's discovered `boat-platform/nodes/*.py` files, with each script's
  module docstring shown underneath once picked.
- **Target gateway** is the `BOAT_HOST` value set in the spawned node's
  environment -- a dropdown spanning *every configured host's* gateway
  instances, not just the one the node itself will run on. Entries on the
  **same** host as the node (`main — localhost:50051 (running)`) resolve
  to `localhost:<port>` -- robust, no DNS involved, and correct, since
  they're the literal same machine. Entries on a **different** host are
  tagged and resolve to that host's own address instead (`[secondary-box]
  main — secondary-box:50051 (running)`) -- taken from that host's agent
  URL, since from the node's own point of view `localhost` would mean
  itself, not the other machine. (If a host was added to this app as
  `localhost:<agent-port>` rather than its real hostname/IP, cross-host
  entries for *that* host will resolve to `localhost` too, which is only
  correct if the node's own host happens to be that literal same box --
  add hosts by their real address if you plan to target them from nodes
  running elsewhere.) Typing a bare port number (e.g. `50052`) normalizes
  to `localhost:50052`; typing a full `host:port` by hand is always
  accepted too, for anything not in either list.
- **Script arguments** builds one input field per CLI argument the
  selected script declares, if the script follows the `build_parser()`
  convention (see `boat-platform/nodes/cyclic_can_sender.py`'s
  docstring): a checkbox for boolean flags (`--fd`, `--brs`, ...), a text
  field for everything else, pre-filled with an `e.g. <default>`
  placeholder -- falling back to the argument's help text when its
  default is empty, so an argument like `--data` (default `""`) still
  shows a usable example (`Payload as hex bytes, e.g. AABBCCDD ...`)
  instead of a blank hint. `--address` is never one of these fields --
  that's the **Target gateway** dropdown above. The group is empty/hidden
  for a script with no discoverable `build_parser()` (introspection
  failures on the agent side -- import errors, missing convention, etc.
  -- degrade silently to an empty schema, never a broken dialog).
- **Extra args** stays a free-text field parsed with `shlex.split()` on
  submit, now specifically the escape hatch for anything the per-argument
  fields above don't cover -- a flag genuinely outside the script's
  declared schema. Populated per-argument fields are combined with
  whatever's typed here when the node is created. In **Edit**, an
  existing node's saved `extra_args` are walked and any recognized
  `--flag [value]` pairs are pulled back into their matching field
  automatically, leaving only the unrecognized leftovers in this field.
  At the top, **From command line** takes a pasted `BOAT_HOST=...
  python3 <script> <args>` line (exactly what the "Equivalent command
  line" panel produces) and **Parse && Fill** does the same
  recognized/leftover split into Target gateway/Script/Script
  arguments/Extra args in one shot -- same idea as the Gateways tab's
  paste feature.
- No **Managed** column or external-process discovery -- every row here is
  something this agent created; arbitrary Python scripts aren't reliably
  identifiable by process name the way `boat_gateway` is, so unmanaged node
  discovery isn't attempted. (This also means every node -- not just
  agent-managed ones -- is captured by **Save Session…**, unlike the
  Gateways tab where only `Managed: Yes` rows are.)

## Test Runs tab

Treats one `boat test run <manifest.json>` invocation as a third kind of
agent-managed process, reusing the same subprocess lifecycle as the Nodes
tab (its own registry server-side, `TestRunInstance`/`TestRunRegistry` --
deliberately separate from `NodeRegistry`). This is the automated,
CI-style HIL suite runner (`boat_cli/test.py` + `sdk/python/boat/test/`)
-- a different thing from the manual, hand-verified `test/*.md` TestSuite
and from ctest/pytest unit tests.

- Table columns: Host, Name, ID, Manifest, Environment, Result
  (`PASS`/`FAIL`/`—` while still running or never started), Status, PID,
  Uptime.
- **New Test Run…**'s **Manifest** dropdown is populated from the selected
  host's discovered `boat-platform/config/tests/manifest_*.json` files
  (name + test count + description shown underneath), and **Environment**
  from its `env_*.json` files -- both are local files read by `boat test
  run` on that same host, so unlike a node's Target gateway there's no
  cross-host resolution to do here. Picking a manifest pre-selects its own
  declared `environment_config` in the Environment dropdown (still
  overridable) -- mirrors `boat test run <manifest> --config <override>`'s
  own semantics: the manifest's own choice is the default, an explicit
  override wins.
- **Extra args** is a flat free-text field (`shlex.split()` on submit,
  e.g. `--stop-on-failure --parallel 2 --preflight -v`) rather than one
  field per flag -- the `boat test run` flag surface is small and fixed
  regardless of which manifest is picked, so there's no per-manifest
  argument schema to introspect the way node scripts have.
- Below the log viewer, a **Report directory** field shows the run's
  `report_dir` (where `report.json`/`report.junit.xml`/`report.html` land,
  relative to `boat-platform/` **on the agent's own host**) with a Copy
  button -- deliberately no "Open" button: in the federated multi-host
  case this app may not be running on that same machine, so
  `QDesktopServices.openUrl()` on that path would be unreliable or wrong.
- **View Report** opens a dialog that fetches the actual report *content*
  instead of just the path -- `GET /api/test-runs/{id}/report` reads back
  every per-test `report.json` the run wrote under `report_dir` (one
  subfolder per manifest test entry; there's no single aggregate report
  file) and hands the parsed content straight to the client. A tree lists
  each test (id, verdict -- color-coded, duration, summary); selecting one
  shows its steps/assertions/description plus which raw artifact files
  (`report.html`/`.junit.xml`/stdout/stderr) exist alongside it in that
  folder, in a detail pane below. This is what actually solves the Report
  directory field's federated-host limitation for the report content
  specifically -- the agent does the file reading, so it works from any
  client regardless of which host it's running on. **Refresh** re-fetches
  (useful mid-run, since each test's `report.json` lands as soon as that
  test finishes, not all at once at the end).
- The agent locates the `boat` CLI itself (`BOAT_CLI_BIN` env override →
  `shutil.which("boat")` → literal `~/.local/bin/boat` fallback, since a
  non-interactively-started agent process may not have `~/.local/bin` on
  `PATH` even when `boat` is installed there) and reports the resolved
  path back as `boat_cli_bin` in `GET /api/host/info`.
- Included in **Save Session…**/**Load Session…**, same as instances and
  nodes -- see "Session files" below.

## Session files (save/load your whole setup)

**Save Session…** writes every added host and its **agent-managed**
instance *definitions* (name, interfaces, plugins+configs, port, tick
settings, gateway binary), **every node definition** (name, script path,
target gateway, extra args), **and every test-run definition** (name,
manifest path, environment config path, extra args) to a YAML file,
docker-compose-style — a recipe, not a live snapshot. Externally-discovered
(`Managed: No`) gateway rows are never included, since there's no owned
definition to save for them; every node and every test run is included,
since both are agent-created already (see the Nodes/Test Runs tab
sections above).

```yaml
version: '1'
hosts:
- name: agn-testcomputer
  url: http://agn-testcomputer:8090
  instances:
  - name: main
    can_ifaces: [vcan0]
    eth_ifaces: [veth0]
    node_plugins:
    - path: /home/.../can_tp.so
      config: {iface: vcan0}
    grpc_port: 50051
    tick_ms: null
    tick_us: null
    gateway_bin: /home/.../boat_gateway
  nodes:
  - name: responder
    script_path: /home/.../nodes/can_request_responder.py
    target_host: localhost:50051
    extra_args: [--iface, vcan0, --request-id, '0x7E0']
  test_runs:
  - name: routing-check
    manifest_path: config/tests/manifest_can_loopback.json
    env_config_path: config/tests/env_can_loopback.json
    extra_args: [--verbose]
```

**Load Session…** adds every host in the file (skipping ones already
present) and, for every saved instance, node, and test run, creates a
fresh one from that definition — left **stopped** (unlike `docker-compose
up`, loading does not start anything automatically; review the tables and
hit Start on what you want). It's a recipe replay either way: a loaded
instance/node/test run never resumes the exact old process, it gets a new
id every time. A node's saved `target_host` is whatever concrete address
it already resolved to (e.g. `localhost:50051`) — it just needs that
address to be reachable after the load, not the *other* host to be in
this same session file (a node can point at a gateway on a host this
session doesn't even list). A test run's saved `manifest_path`/
`env_config_path` are the relative paths the agent itself reported
(relative to `boat-platform/` on that host) — it just needs those files
to still exist on that host. Loading the same file twice against a host
that still has those instances *running* will hit a port conflict on the
second load for instances (the saved `grpc_port` is explicit, not
auto-allocated) — nodes and test runs have no such conflict (no port of
their own) and will just create additional, separate rows — stop/remove
first if you want to reload cleanly either way.

## What's not here yet

- No interface-creation UI (create vcan/veth from this app) — the agent
  doesn't expose that yet either; use `ui/launcher.py`'s browser UI for
  interface setup on a given host for now.
- No persistence for *instance/node/test-run definitions* on the agent
  side — an agent restart forgets stopped instances, nodes, and test runs
  (see the backlog docs). Session files are the client-side answer to
  this (save before a restart, reload after — covers Gateways, Nodes, and
  Test Runs alike), but there's still no automatic recovery.
- No auth — same trust model as every other `ui/*.py`/`tools/*.py` service
  in this repo today (assumes a trusted lab network).

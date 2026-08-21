# Launcher Agent / Admin Tool Backlog

Tracking the gateway-administration effort: a per-host **launcher agent**
(`ui/launcher_agent.py`) exposing a multi-instance gateway lifecycle REST API,
eventually driven by a **PySide6 admin app** that talks to one or more agents
over the network. See `AGENTS.md`'s "Launcher Agent" section for the API and
the federated architecture rationale.

Status: agent v1 and a PySide6 admin client both exist, hardware-verified
both headlessly and with a real render pass (screenshot) on a real machine.

---

## Done (2026-08-10)

- `GatewayInstance`/`InstanceRegistry`: multiple concurrent `boat_gateway`
  processes per host, each with its own CAN/Eth interfaces,
  `BOAT_NODE_PLUGINS` (structured `{path, config}` list, not a raw env
  string), and `BOAT_GRPC_PORT` (explicit or auto-allocated — probes a real
  `bind()` the same way the gateway's own `RefuseIfPortInUse` does, and also
  avoids ports already claimed by other *tracked* instances regardless of
  whether they're currently running).
- REST API: `GET/POST /api/instances`, `GET /api/instances/{id}`,
  `POST /api/instances/{id}/start|stop`, `DELETE /api/instances/{id}`
  (refused while running), `GET /api/instances/{id}/log`,
  `GET /api/instances/{id}/sim-state`, `GET /api/host/info` (interfaces,
  discovered gateway binaries, discovered plugin `.so` files).
- Verified on real hardware (`agn-testcomputer`): two instances created
  without explicit ports auto-allocated 50051/50052 (second correctly
  skipped the first's *reserved-but-not-yet-started* port); both started
  independently, logged their own port, were reachable via
  `boat --host localhost:<port>`; a third instance explicitly requesting
  50051 was rejected with 400; stopping both gave clean `exit_code: 0`;
  deleting a running instance was refused with 409 until stopped.

## Known v1 gaps (not fixed — deliberate scope cuts, revisit if they bite)

- **In-memory registry only.** Agent restart forgets every *stopped*
  instance's definition; a still-*running* gateway process is unaffected
  (it keeps running, orphaned) but the agent no longer tracks or can
  stop/inspect it via the API — only `pkill`/manual intervention finds it
  again. No persistence (JSON file, SQLite) added yet. Add if agent
  restarts during real use turn out to be common enough to be painful.
- **No auth / no TLS.** The agent's REST API is plain HTTP with no
  authentication — anyone who can reach the port can start/stop/delete
  gateway instances on that host. Fine for a trusted lab network (matches
  every other `ui/*.py` service today); would need real auth before being
  reachable from anything less trusted.
- **`admin_gui/requirements.txt` doesn't (can't) capture the `libxcb-cursor0`
  system dependency** Qt6's `xcb` platform plugin needs on Linux —
  documented as a manual `apt install` step instead (see "visual
  verification" below). Not a gap in the app; just not something `pip`
  can express.
- **Not wired into `start_ui.sh`/`stop_ui.sh`.** New/still-evolving; start
  manually (`python3 ui/launcher_agent.py`) until the API and client have
  settled. Add to the standard scripts once it has.
- **`sim-state`'s `connected: false` on "no active simulation".** Copied
  verbatim from `ui/launcher.py`'s existing `/api/simulation/state` — a
  `GetSimulationState` call against a gateway with no simulation raises
  gRPC `NOT_FOUND`, which this endpoint (like the one it was copied from)
  reports as `connected: false` even though the gRPC connection itself
  succeeded. Cosmetic; matches existing precedent rather than introducing a
  new pattern, not worth fixing in isolation.

## Done (2026-08-10, continued) — PySide6 admin client

`admin_gui/` (`main.py`, `agent_client.py`, `host_store.py`) — a desktop
client for one or more agents. Host list (persisted to
`~/.boat/admin_hosts.json`) → aggregated instance table, polled every 2s via
a background `QThread` → New Instance dialog, Start/Stop/Delete on the
selected row, and a log viewer that follows the selection. `agent_client.py`
and `host_store.py` are plain Python (no Qt import), so they're usable/
testable headlessly.

Verified headlessly on real hardware (`agn-testcomputer`, `QT_QPA_PLATFORM=
offscreen`, no real display): `MainWindow` constructs and its background
`PollWorker` pulls a real snapshot from a live agent; created + started an
instance via `AgentClient` the same way the New Instance dialog does,
confirmed it showed `status: running` with a real PID; drove the actual
`MainWindow.stop_selected()` UI method against the selected row and confirmed
the real subprocess stopped with `exit_code: 0`; two agents (stand-in for two
hosts) aggregated correctly into one table with distinct Host-column values;
`delete_selected()` on a running instance correctly surfaced the agent's 409
in a second dialog and left the instance running.

That last check caught a real bug in the *test*, not the app: the first
attempt dismissed the confirmation dialog via `.accept()`, which sets the
dialog's result code but not `clickedButton()` — `QMessageBox.question()`
then reads back `NoButton`, not `Yes`, so `delete_selected()` silently took
its early-return path without ever calling `delete_instance()`. That gave a
false "PASS" (instance still running) for the wrong reason. Fixed by
clicking the dialog's actual `Yes` button object; re-verified with a
repeating watchdog that caught both the confirmation dialog and the
subsequent "Delete failed" 409 dialog in one call. Worth remembering for any
future headless Qt dialog test in this codebase.

## Done (2026-08-10, continued) — visual verification + a real missing dependency

The user tried running `admin_gui/main.py` over RDP on `agn-testcomputer`
and hit an immediate crash:

```
qt.qpa.plugin: From 6.5.0, xcb-cursor0 or libxcb-cursor0 is needed to load
the Qt xcb platform plugin.
qt.qpa.plugin: Could not load the Qt platform plugin "xcb" in "" even
though it was found.
Aborted (core dumped)
```

`libxcb-cursor0` is a *system* library Qt6's `xcb` platform plugin needs at
runtime — `pip install pyside6` never installs it (it's not a Python
package). The earlier `QT_QPA_PLATFORM=offscreen` headless verification
never exercised the real `xcb` plugin at all, so this gap wasn't visible
until a real display (RDP, in this case) was actually tried. Fixed with
`sudo apt install libxcb-cursor0`; documented in `admin_gui/README.md` and
`AGENTS.md` as a one-time per-machine Linux prerequisite. Also hit and noted
the same PEP 668 "externally-managed-environment" `pip install` friction
already documented elsewhere in this repo (`--break-system-packages`).

With the library installed, verified for real (not headless): ran the app
under `Xvfb` (a virtual X server, standing in for "some display exists") on
`agn-testcomputer`, added a host, created+started a real instance, and took
an actual screenshot (`QWidget.grab()`) confirming the layout renders
correctly — host list with a filled health dot, instance table showing the
real running PID/uptime, action buttons, log panel all positioned and
readable as designed.

## Done (2026-08-12) — Interfaces/Plugins columns, and a real timeout bug they surfaced

Added `Interfaces` and `Plugins` columns to the instance table (full order:
Host, Name, ID, Port, Status, PID, Interfaces, Plugins, Uptime).
`Interfaces` combines `can_ifaces` + `eth_ifaces`. `Plugins` shows each node
plugin's `.so` basename, with the interface it's bound to in brackets when
its config carries one (`can_tp.so [vcan0]`) — the "linked to the plugin"
association the user asked for. `QTableWidget.resizeColumnsToContents()`
added after populating rows, since the default even-width split truncated
the wider Interfaces/Plugins cells.

Building a real screenshot to check this (two plugins, one with an `iface`
config) surfaced a genuine, previously-unnoticed bug, not a test artifact
this time: `AgentClient`'s default 5s timeout could be shorter than
`GatewayInstance.stop()`'s own worst case (SIGTERM + up to a 5s wait, then
SIGKILL) — a real user clicking **Stop** in the UI on a slightly slow
shutdown could get a false "Stop failed" read-timeout error even though the
gateway did actually stop a moment later. First reproduction: two demo
gateway processes ended up killed with `exit_code: -9` because an *external*
test-harness `timeout` wrapper (not the app) expired while waiting on that
same slow HTTP response and SIGKILLed the whole process group. Fixed by
giving `start_instance`/`stop_instance` specifically a 15s client-side
timeout (`_LIFECYCLE_TIMEOUT`), well clear of the server's worst case;
quick reads (list/get/log) keep the original 5s default.

Verified on real hardware: re-ran the same scenario with the fix and an
adequately long harness timeout — no timeout, `stop_instance` returned
normally, and the screenshot confirmed both new columns render correctly:
`Interfaces: vcan0, vcan1, veth0`, `Plugins: pdu_router.so, can_tp.so
[vcan0]`.

## Done (2026-08-12, continued) — dropdown pickers for interfaces/plugins in New Instance

The New Instance dialog's CAN/Eth interface and node-plugin fields were
free-typed text (comma-separated / one-per-line), error-prone and requiring
the user to already know exact interface names and full plugin paths. Added
`ListPicker` (CAN/Eth) and `PluginListPicker` (plugins, with an optional
per-entry JSON config) -- both an *editable* `QComboBox` (so manual entry
always still works, e.g. for an interface not created yet) pre-populated
from the selected host's `GET /api/host/info`, plus a `+ Add`/`Remove
selected`-backed accumulated list. Combos reload when the host selection
changes. Plugin entries are stored structured (`{"path", "config"}` dicts
via `Qt.UserRole`), not re-parsed from display text.

Verified with real screenshots on real hardware (`agn-testcomputer`,
Xvfb): dropdown genuinely populated from a live `host_info()` call (real
`vcan0`/`vcan1`/`can0`/`can1`/PDU-DB-imported ifaces, real discovered
`.so` paths); both a dropdown-picked and a manually-typed CAN interface
accepted into the same list; a plugin added with a JSON config and one
without both stored and round-tripped correctly through `result_payload()`;
invalid JSON in the config field rejected via a warning dialog without
adding a stray entry. First screenshot caught a real layout bug --
cramming the plugin path combo, config field, and Add button into one row
left the config field showing only "d json" of its placeholder text; fixed
by stacking path and config onto separate lines, re-verified with a second
screenshot showing the fix.

## Done (2026-08-12, continued) — Edit instance + equivalent command-line panel

Two QoL additions:

- **Edit.** Agent gained `PUT /api/instances/{id}` (`InstanceRegistry.update()`)
  -- same edit-in-place-refused-while-running pattern as `CanTpService`'s
  re-run-`configure`, not delete+recreate (keeps the same id). `grpc_port`
  reuses `_allocate_port()` with the instance's own current port excluded
  from the collision set, so resubmitting the same port (what the dialog
  pre-fills) is never mistaken for a self-conflict. `AgentClient.
  update_instance()` added to match. `NewInstanceDialog` now doubles as the
  Edit dialog (`existing`/`existing_host_url` params): pre-fills every field
  from the instance's current definition, locks the host combo (an instance
  can't move agents), and `MainWindow.edit_selected()` calls
  `update_instance()` instead of `create_instance()` with the same
  `result_payload()`. New **Edit…** button next to New Instance.
- **Equivalent command line.** A read-only field + Copy button below the
  log panel shows the `BOAT_CAN_INTERFACES=... BOAT_NODE_PLUGINS=...
  ./boat_gateway` invocation for whichever instance is selected --
  `_format_command_line()`, updated on selection change and on every
  table rebuild (so an edit is reflected immediately). Matches the env var
  names/format already documented in `README.md`/`AGENTS.md`.

Verified on real hardware (`agn-testcomputer`): raw agent API first --
created an instance, `PUT` while stopped (renamed, added an eth iface, same
port) applied cleanly, started it, confirmed `PUT` while running was correctly refused with 409,
stopped+deleted. Then the full Qt flow with a real screenshot:
built the Edit dialog exactly as `edit_selected()` does and asserted every
field pre-filled correctly (host combo disabled, name/CAN/plugin config all
matching the existing instance, port pre-filled with the current value);
mutated it (added an eth iface, renamed) and submitted via
`update_instance()`; confirmed the change landed server-side
(`get_instance()`) and that both the table row and the command-line panel
picked up the new config afterward. Command-line panel's own content
independently checked before and after the edit, matching the actual
`can_ifaces`/`eth_ifaces`/`node_plugins`/`grpc_port`/`gateway_bin` on the
instance in both cases.

## Done (2026-08-12, continued) — un-resolved `Path(__file__)` produced ugly/wrong-looking paths

User ran `python3 ../ui/launcher_agent.py` from inside `admin_gui/` (a
perfectly reasonable thing to do) and every discovered path -- gateway
binary, plugin `.so` files, and therefore the "Equivalent command line"
panel -- came out as `/home/testuser/ProjectBoat/admin_gui/../boat-platform/
...` instead of the clean `/home/testuser/ProjectBoat/boat-platform/...`.

Root cause: `ui/launcher_agent.py` and `ui/launcher.py` computed
`_PROJECT_ROOT`/`_DEMO_DIR` from `Path(__file__).parent.parent` without
`.resolve()`. When a script is invoked via a relative path that itself
contains `..` (like `../ui/launcher_agent.py` from a sibling directory),
Python absolutizes `__file__` by prepending the CWD *without* collapsing
the `..` -- so `__file__` becomes `.../admin_gui/../ui/launcher_agent.py`,
and `.parent.parent` (which only strips path components lexically, it
doesn't normalize) carries that `admin_gui/..` straight through into every
derived path. Every other script in the repo (`tools/pdu_editor.py`,
`tools/trace_analyzer.py`, `tools/trace_editor.py`,
`tools/eth_trace_analyzer.py`, `ui/commander.py`, `ui/control_panel.py`'s
own `sys.path` line, `ui/dashboard.py`, `ui/debug.py`, `ui/recorder.py`,
`ui/system_dashboard.py`) already used `.resolve()` for exactly this
reason -- `launcher_agent.py`/`launcher.py` just didn't follow that existing
convention. `ui/control_panel.py` had the same lapse in two of its own
path constants (`_NODES_DIR`/`_SDK_PATH`) despite getting its `sys.path`
line right. Fixed all of them to add `.resolve()`.

**Note for anyone touching a *running* agent process to verify this kind of
fix**: editing the `.py` file on disk does not affect an already-running
process -- `_PROJECT_ROOT` is computed once at import time and stays
whatever it was when that process started. Verifying required actually
restarting the process (confirmed on real hardware: reproduced the exact
`admin_gui/../boat-platform` path against the user's own live agent
process before the fix, then a freshly-started process on a scratch port
showed the clean path after).

## Done (2026-08-12, continued) — paste-a-command-line, and see everything running (not just agent-managed)

Two more QoL asks:

- **Paste to create.** Reverse of `_format_command_line()`:
  `_parse_command_line()` (+ brace-aware `_tokenize_command_line()`/
  `_parse_plugins_value()` helpers) parses a pasted
  `BOAT_CAN_INTERFACES=... BOAT_NODE_PLUGINS=... ./boat_gateway` line back
  into the New/Edit-Instance dialog's fields. New **From command line**
  field + **Parse && Fill** button at the top of the dialog; replaces
  (doesn't merge into) whatever's already in the pickers, since pasting is
  "start fresh from this."
- **See what's actually running, not just what this agent started.** The
  agent only ever tracked instances it spawned itself -- a `boat_gateway`
  started by hand (SSH, a script, or orphaned by an earlier agent process
  exiting -- the known in-memory-registry gap above) was invisible to
  `GET /api/instances`. Added `_discover_external_gateways()`: scans
  `/proc` for `boat_gateway` processes not already tracked, and recovers
  their full config -- `BOAT_CAN_INTERFACES`, `BOAT_ETH_INTERFACES`,
  `BOAT_GRPC_PORT`, `BOAT_NODE_PLUGINS` (parsed with the same
  brace-aware-comma-split logic, independently implemented agent-side since
  the agent has no reason to import Qt-side code) -- from
  `/proc/<pid>/environ`, and the binary via `/proc/<pid>/exe`. These get
  `id: "external:<pid>"`, `managed: false` (real registry-tracked instances
  get `managed: true` from `to_dict()`), and appear in `GET /api/instances`
  merged with the agent's own. Only `stop` works on them (`os.kill(pid,
  SIGTERM)` -- doesn't care who spawned the process); `start`/`edit`/
  `delete`/single-`GET` are refused with a clear 400
  (`_reject_if_external()`), and `log` returns a fixed explanatory message
  instead of erroring (stdout was never piped to an agent that didn't spawn
  the process). Client got a new **Managed** table column (`Yes`/`No`) and
  a `MainWindow._warn_if_external()` guard that short-circuits Edit/Start/
  Delete on an external row client-side with a clear message, before even
  making the (would-be-400) network call.

Verified on real hardware (`agn-testcomputer`) end to end:
- Paste-and-fill: parsed a pasted line with two interfaces, a port, and two
  plugins (one with a config) -- every dialog field matched exactly, and
  `create_instance()` from the parsed payload round-tripped back through
  `_format_command_line()` to the same `BOAT_CAN_INTERFACES`/`BOAT_GRPC_PORT`
  values.
- External discovery: started a `boat_gateway` **manually** (not via the
  agent, exactly reproducing the scenario that motivated this --
  `BOAT_CAN_INTERFACES=vcan0,vcan1 BOAT_GRPC_PORT=50077
  BOAT_NODE_PLUGINS=...can_tp.so?{"iface":"vcan0"} ./boat_gateway`
  directly in a shell) and confirmed `GET /api/instances` discovered it
  with every field correct -- interfaces, port, and (this took a second,
  correctly-quoted attempt -- the first attempt's shell-escaping ate the
  JSON's quotes before they reached the actual environment, which the
  parser correctly treated as invalid JSON and fell back to `{}` for,
  exactly as designed) the plugin's `iface` config.
- Confirmed `start`/`delete`/`GET`-single on the external id all returned
  400 with the expected message, `log` returned the friendly stub, and
  `stop` actually sent `SIGTERM` and the process exited -- confirmed both
  via `ps` (process gone) and that it dropped out of the next
  `GET /api/instances`.
- Full Qt pass with a real screenshot: table showed a real agent-managed
  instance (`Managed: Yes`) alongside a real manually-started one
  (`Managed: No`, real physical `can0` + `vcan0` interfaces) side by side;
  `MainWindow.edit_selected()`/`start_selected()`/`delete_selected()` on the
  external row each produced the client-side guard message (asserted via a
  monkey-patched `QMessageBox.information`) without any network call;
  `MainWindow.stop_selected()` on that same row genuinely terminated the
  manually-started process (confirmed via `Popen.wait()`).

## Done (2026-08-12, continued) — fixed a real stale-selection bug the user caught by hand

User's own testing (not a scripted check) found this: create+start a
managed instance A, start an external instance B by hand, select B and
Stop it (worked, B correctly vanished from the table) -- then select what
looked like A's row and click Stop, and got `Stop failed: ... 404: process
not found` naming B's *old* pid, not A's.

Root cause: `rebuild_table()` runs its whole row-repopulation pass inside
`self.table.blockSignals(True)`. When the previously-selected id
(`self._selected`) is no longer present in the new snapshot (B just
vanished), the old code did nothing further -- `select_row` stayed `None`,
so `self.table.selectRow(...)` was never called, but *Qt's own selection
model* was never told anything changed either (signals blocked), so
whatever row **index** was highlighted before stayed highlighted, now
showing completely different data (A's) at that index. The user saw A's
row visually selected and clicked Stop, but `self._selected` -- what every
action button actually reads -- still held B's stale key. A real
correctness hazard, not just a display glitch: with a bigger table, that
stale id could by coincidence still resolve to a *different, still-live*
instance, and Stop/Edit/Delete would silently act on the wrong one with no
error at all.

Fixed: when `select_row` comes back `None` (the previously-selected
instance isn't in this snapshot), explicitly `self.table.clearSelection()`
and `self._selected = None` -- so nothing stale survives a rebuild, and an
empty/no-longer-valid selection is *shown* as no selection, not silently
misattributed to whatever's left.

Verified two ways:
- **Reproduced the user's exact bug against the actual pre-fix code**:
  temporarily reverted just this one change, ran a deterministic test (see
  below) that creates instance A, selects external instance B, "removes"
  B from a hand-crafted snapshot while B is still selected, and asserts
  `_selected` gets cleared -- with the revert in place, the assertion
  correctly *failed* (`_selected` stayed pointing at B's dead key, and
  `table.selectedItems()` showed a row still visually highlighted despite
  no valid selection) -- confirming the test genuinely catches this bug,
  not just a happy-path check. Restored the fix, same test passed cleanly.
- **Deterministic, hand-crafted-snapshot test** (no real subprocess/network
  timing involved, to avoid the flakiness a live-process version of this
  test kept hitting in this environment -- SSH/agent round-trip latency
  made `poll_until`-style waits unreliable): construct `MainWindow`,
  feed `rebuild_table()` synthetic snapshots directly, assert `_selected`
  and the visual selection at each step. All four steps (select B for
  real, B vanishes + selection clears, click A for real, action helper
  resolves to A's real id) verified on real hardware (`agn-testcomputer`).

## Done (2026-08-12, continued) — session save/load (docker-compose-style YAML)

New `admin_gui/session.py` (Qt-free, `PyYAML`): `save_session()` writes
every host + its agent-managed instance definitions (never `managed: false`
rows) to a YAML file; `load_session()` reads one back and performs the
actual `create_instance()` + `start_instance()` calls against each host's
agent, returning `(hosts_to_add, errors)` for the caller (`MainWindow`) to
add via its own `HostStore` and report. New **Save Session…** / **Load
Session…** buttons next to the host controls. Deliberately a *recipe*, not
a live-state resume -- loading always creates fresh instances (new ids,
new PIDs), the same way `docker-compose up` doesn't resume old container
ids; reloading the same file while those instances are still running hits
a port conflict on the explicit saved `grpc_port`, same as
`docker-compose up` against an already-bound port.

This is the client-side answer to the "agent restart loses everything"
in-memory-registry gap above -- not automatic recovery, but "save before
you might lose it, reload after" now has a real workflow, and it doubles
as a way to hand a whole multi-host setup to someone else as one file.

Verified on real hardware (`agn-testcomputer`) with a full round trip:
created two agent-managed instances (one with a plugin config, an eth
interface, and an explicit port) plus one external (manually-started)
process; saved -- confirmed the YAML contains exactly the two managed
instances with every field matching what was created, and the external
one is absent; wiped the agent completely (stopped+deleted both managed
instances, killed the external process); loaded the saved file into a
**totally fresh** `MainWindow`/`HostStore` (no hosts at all, simulating a
different machine or a fresh session) -- `load_session()` reported zero
errors, the host was added correctly, and both instances came back
running with fields matching the original definitions exactly, each under
a **new** id (confirmed `!= ` the original), not a resume.

## Done (2026-08-12, continued) — Load Session leaves instances stopped

User feedback after trying session save/load: instances created by **Load
Session** should not auto-start. Changed `session.load_session()` to only
call `create_instance()` (no `start_instance()`), so a load defines every
saved instance but leaves it `stopped` -- review the table and Start what
you actually want, rather than everything coming up at once. Diverges from
the `docker-compose up` analogy on this one point deliberately (compose
does auto-start); the recipe-not-resume framing (new id every load) still
holds. `load_session()`'s return shape gained a `created_count` so
`MainWindow`'s confirmation message reports how many instances were
defined, not just how many hosts were added.

Verified on real hardware (`agn-testcomputer`): saved a running instance,
wiped it, reloaded -- confirmed `status: "stopped"`, `pid: null`, with
every other field (`can_ifaces`, `grpc_port`, etc.) still matching the
saved definition exactly.

## Done (2026-08-14) — plugin config schema fields (New Instance dialog)

User feedback, having just seen nodes get per-argument fields: "Is it
reasonable and possible to create for plugins something similar?" Answer:
yes, but a different mechanism was needed -- a node script's `argparse`
metadata can be imported and introspected live (`_introspect_node_args()`
in `ui/launcher_agent.py`); a compiled plugin `.so` has nothing equivalent
to import at runtime. Checked all 5 real plugins' actual config parsing
(`can_tp`, `pdu_router`, `tcp`, `probe`, `someip` -- all hand-parse a raw
JSON string via `strstr`, no shared schema anywhere in the C++) before
picking an approach.

Presented two options and let the user choose: a static, hand-written
`<name>.schema.json` sidecar file per plugin (no ABI change, ships today)
vs. a new optional C ABI export each plugin implements to describe itself
at runtime (self-describing, always in sync, but touches `plugin.h` and
every plugin's `.cpp`, needs a full rebuild). User picked the sidecar
file -- same practical result, far less risk for a plugin ecosystem this
small right now.

- **Plugins**: added `<name>.schema.json` next to `can_tp.cpp`/
  `tcp.cpp`/`probe.cpp`/`someip.cpp` (`pdu_router` takes no config, no
  schema needed) describing each plugin's actual accepted keys --
  `{"key": {"type", "default", "help", ["enum"|"item_type"]}}`. Written by
  hand to match each plugin's real `strstr`-based parsing (probe.cpp
  already had a config-JSON doc comment; the others didn't, so those were
  derived directly from the parsing code and its member defaults).
- **Build**: `cmake/BoAtPlugin.cmake`'s `add_boat_plugin()` copies the
  sidecar (if present) next to the built `.so` via a `POST_BUILD` custom
  command, and installs it alongside for `cpack` too -- same journey the
  `.so` itself takes, so `BOAT_NODE_PLUGINS` always finds it in the same
  place.
- **Agent**: `_introspect_plugin_config()` reads the sidecar (if any) next
  to each discovered `.so`; `_discover_plugins()`'s `GET /api/host/info`
  `"plugins"` entries changed shape from flat path strings to
  `{"path", "config_schema"}` objects. Swallows any read/parse failure
  into an empty schema -- same defensive pattern as node introspection, a
  plugin without one (or a corrupt sidecar) never breaks discovery for
  every other plugin.
- **admin_gui**: `PluginListPicker` (New/Edit Instance dialog) grew a
  "Plugin config" group rebuilt from the selected plugin's schema on
  every combo selection change -- a checkbox per `bool` key, a dropdown
  per `enum` key, a comma-separated field parsed into a JSON list per
  `array` key, a text field with an `e.g. <default>` placeholder for
  everything else. The existing flat JSON config field stays as the
  escape hatch for anything not covered by a schema (or a plugin with
  none at all) -- both are merged into one config dict on **+ Add**, flat
  JSON's keys taking precedence on overlap.

A first draft had a real bug caught during verification, not by inspection:
`add_current()` called the same field-rebuild function used on selection
change defensively right before reading field values, "just in case" --
copied from `NewNodeDialog`'s analogous pattern without noticing the
difference in *when* it runs there. Unlike there, calling it here
destroyed and recreated every widget (wiping whatever was just typed/
checked) immediately before harvesting them, so every add silently
produced only default values regardless of what was actually entered. A
driver script that set real values and asserted the resulting dict caught
it immediately (`{"nagle": true}` came back despite explicitly unchecking
it and setting two other fields, which the rebuild had silently erased).
Fixed by removing that call -- `currentIndexChanged` already keeps the
fields in sync with the combo selection at all relevant times; there was
nothing to resync at submit time.

Verified on real hardware (`agn-testcomputer`): rebuilt all plugins,
confirmed each `.schema.json` landed next to its `.so` in the build
output; a scratch agent instance's `/api/host/info` showed the correct
schema per plugin via curl (`can_tp`'s single `iface` key, `tcp`'s ten
keys with correct types, `probe`'s enum/array keys, `someip`'s `sd_port`,
empty for `pdu_router` and every legacy/orphaned `.so` in the build
directory with no current CMake target). A real Qt render (Xvfb + `xcb`,
screenshotted via `QWidget.grab()`) against that same scratch agent
confirmed: selecting `tcp.so` rendered all ten fields with correct
placeholders; selecting `probe.so` rendered its `mode` enum as a real
dropdown (pre-selected to its default, `"both"`) and its `buses` array
field with a comma-separated placeholder; filling `tcp.so`'s `iface`/
`retry_ms`/`nagle` fields and clicking **+ Add** produced exactly
`{"iface": "veth0", "nagle": false, "retry_ms": 500}` in the resulting
list entry (confirming the bug fix), with `retry_ms` correctly typed as a
JSON integer, not a string.

## Done (2026-08-18) — Interfaces tab (create/configure/up/down)

Picked up the "Interface-creation UI / agent endpoints" item from "Next
steps" below, after user: "actually while we are on it. can we also
configure, create, up/down interfaces?" -- said in the context of
considering the Test Runs work (tab, Save/Load Session, report viewer)
complete, and this the natural last piece of "the environment" (hosts/
gateways/plugins/nodes/test suite/interfaces), distinct from Scenarios/
Simulations/Replays and ad-hoc frame send/receive, which the user
explicitly characterized as "more like the actual use of a gw, not so
much a part of the environment."

**Agent side** (`ui/launcher_agent.py`): a new "Interface endpoints"
section -- `GET /api/interfaces` (previously only reachable indirectly
via `/api/host/info`), `POST`/`DELETE /api/interfaces/vcan`,
`POST`/`DELETE /api/interfaces/veth`, `POST /api/interfaces/{name}/up`,
`POST /api/interfaces/{name}/down`, and `POST /api/interfaces/{name}/
can-config` (bitrate + optional CAN FD data-bitrate, for any type-can
link, virtual or physical -- the exact `ip link set ... up type can
bitrate ... [dbitrate ... fd on]` commands `boat_cli/
bus_setup_context.py`'s "Physical CAN" section already documents).
vcan/veth create+delete mirror `ui/launcher.py`'s own equivalent
endpoints exactly (same `ip`/`modprobe` commands, same passwordless-sudo
prerequisite) -- either tool works against the same host, this isn't a
replacement. Deliberately no delete for anything but vcan/veth: a real
network device isn't something this agent should be able to remove, only
reconfigure or toggle up/down. `_list_interfaces()` gained `operstate`/
`lower_up` fields (previously agent-only; `ui/launcher.py`'s own version
already had them).

**A real bug found and fixed during this feature's own verification**:
Linux caps interface names at 15 characters (`IFNAMSIZ`). Testing veth
creation with a plausible-looking test name (`veth_admintest0`, 15 chars)
failed with `ip`'s own cryptic `"name" not a valid ifname` -- the
auto-generated peer name (`veth_admintest0_peer`, 20 chars) silently
exceeded the limit with no indication why. Neither this agent's new
endpoint nor `ui/launcher.py`'s pre-existing identical one validated
this beforehand. Fixed with a `_check_ifname()` helper (agent-side,
clear 400 with the actual limit) and matching live client-side
validation in `NewInterfaceDialog` (a red warning under the Name field,
updating as you type, specifically because the peer suffix is what most
often pushes a plausible name over the limit) -- caught before the
network round trip, not just after it.

**Client side**: `agent_client.py` gained the matching methods.
`admin_gui/main.py` gained a fourth tab, **Interfaces** (table: Host,
Name, Type, Up, Operstate, MAC, aggregated across hosts on the same 2s
poll cycle as everything else), `NewInterfaceDialog` (shared for vcan
and veth, since the two only differ in default name and the veth-only
peer label), and `CanConfigDialog` (Bitrate, CAN FD checkbox, Data
bitrate field enabled only when FD is checked). **Down** guards with a
confirmation dialog specifically -- unlike every other destructive action
in this app, bringing an interface down can disrupt a *different*
process (a running gateway actively using that CAN bus) that this tool
has no record of and no way to warn about more specifically. **Delete**
is refused client-side too for anything but a vcan/veth row. Not
included in Save/Load Session -- interfaces are host-level system state,
not a process definition this tool owns the way an instance/node/test
run is.

**Verified on real hardware** (`agn-testcomputer`), on an isolated test
agent (port 8098 -- the user's own live agent was on the usual 8090 and
their own gateway was actively using real `can0`/`can1`, both confirmed
via `ps`/`ss` and never touched; all test interfaces used clearly
test-scoped names and were cleaned up immediately after each check), two
ways. First via `curl` directly: confirmed `operstate`/`lower_up` in the
listing; created/brought down/brought up/deleted a test vcan; confirmed
`can-config` against that vcan failed cleanly (`RTNETLINK answers:
Operation not supported`, the real, expected kernel rejection for a
virtual interface -- exercising the negative path deliberately, since
the positive path needs real CAN hardware and touching the box's live
`can0`/`can1` was explicitly avoided); hit the real 20-char peer-name bug
above, then re-verified the fix produced a clean 400 with the actual
character counts, followed by a real veth pair (`vethtest0`/
`vethtest0_peer`) created, confirmed, and deleted (both ends gone
together, as `ip`'s own behavior guarantees). Then through the real Qt
code path (Xvfb + `xcb`, a throwaway driver script, not committed):
confirmed the table's 6 columns; drove the real `NewInterfaceDialog` and
confirmed its live peer-name warning appears/disappears correctly as the
name changes and that `result_name()` raises client-side for a too-long
name; created a real vcan through the same call path `new_vcan()` makes,
confirmed it in the table, toggled it **down** then **up** through the
real handlers (`QMessageBox.question` monkeypatched to auto-confirm,
matching this session's established pattern for otherwise-blocking
modals in a headless driver) and confirmed the Up column updated each
time via the real poll cycle; opened the real `CanConfigDialog`,
confirmed its payload, and confirmed the same negative-path rejection;
created and deleted a real veth pair through the dialog and confirmed
both rows appeared and disappeared together. A screenshot confirmed the
table renders correctly, including real physical `can0`/`can1` shown
read-only alongside virtual interfaces, untouched throughout. No
unexpected info/warning dialogs fired during the whole run (asserted
explicitly). All test artifacts (test interfaces, the isolated agent
process, Xvfb, driver script) cleaned up afterward.

## Done (2026-08-18, continued) — "Configure CAN" found and fixed two real bugs on real CAN FD hardware

The Interfaces tab above shipped with `can-config` verified only against
`vcan0`'s negative path (bitrate rejected outright, since vcan has no
real bitrate) -- the positive path needs real CAN hardware, and the
box's live `can0`/`can1` were deliberately left untouched during that
first pass. The user then exercised it for real, on `can0` (a PEAK
PCAN-USB Pro FD already running CAN FD at 500000/2000000), and reported
back precisely what happened at each step -- this is that report,
verbatim: "I tried to change the baudrate on can0. I selected can0 and
pressed Down button. Network went down (OK), Then i pressed on Configure
CAN..., it showed me a config with 500kbaud no CANFD. This is NOK,
actual state is CANFD with 500kbaud & 2000kbaud. I changed to CAN with
250kbaud. Was not applied (NOK), interface can0 is imeadeatly started
(went from down to up) (NOK)." -- with `ip -details link show can0`
output before and after confirming exactly what the interface actually
did (bitrate went 500000 → 250000, but `<FD>` and `dbitrate 2000000`
stayed).

Investigating (`ip -d -j link show can0`, structured JSON, before
touching anything further) confirmed two distinct, real bugs -- not one:

1. **The dialog never reflected the interface's actual state.**
   `CanConfigDialog` always opened with hardcoded defaults (500000,
   unchecked, 2000000) regardless of what the interface was actually
   doing -- so it looked like "the current config" when it was just a
   placeholder. `ip -d -j link show <name>`'s `linkinfo.info_data`
   (`bittiming.bitrate`, `data_bittiming.bitrate`, `ctrlmode` containing
   `"FD"`) turned out to be exactly what was needed to read this back
   reliably (confirmed `vcan*`'s `linkinfo.info_kind` is `"vcan"`, not
   `"can"`, with no `bittiming` at all -- correctly falls through to
   "unknown" rather than a wrong prefill).
2. **Unchecking CAN FD didn't turn CAN FD off.** The CAN netlink
   interface only updates the fields a `type can` message actually
   *includes* -- anything omitted keeps its previous value. The original
   `POST .../can-config` only ever appended `fd on` (when requested) and
   never `fd off`, so reconfiguring an already-FD-enabled interface with
   FD unchecked left FD (and its stale `dbitrate`) completely untouched
   while the classic `bitrate` field still changed underneath it --
   exactly the "bitrate changed, everything else didn't" result the user
   saw. Fixed by always sending `fd on`/`fd off` explicitly, never
   omitting it.
3. **A confirmed design gap, not initially flagged as a bug but agreed
   as one via a direct question to the user:** the endpoint always
   brought the interface back `up` after configuring, silently undoing
   the explicit **Down** the user had just pressed. Fixed by recording
   the interface's up/down state *before* the call (still has to bring
   it down first to apply a bitrate change at all, same as before) and
   restoring that same state afterward, instead of unconditionally `up`.

**Fix**: `_read_can_config()` (agent-side helper, parses `ip -d -j link
show`) + `GET /api/interfaces/{name}/can-config` (new endpoint) +
`agent_client.get_can_config()` (returns `None` on 404/anything not a
real CAN link, a normal "nothing to prefill with" result, not
exceptional) + `CanConfigDialog` now takes an optional `current` dict and
pre-fills Bitrate/CAN FD/Data bitrate from it, with a "Current: ..." label
showing the real state (or a clear "unknown" message when `None`).
`POST .../can-config` now always sends `fd on`/`fd off` explicitly and
restores the interface's prior up/down state instead of forcing `up`.

**Verified on real hardware** (`agn-testcomputer`, `can0` itself -- an
isolated test agent on port 8098, since interface endpoints act on the
shared host's network namespace regardless of which agent port issues
the call, so this didn't need the user's own live agent on 8090 touched
or restarted), against ground truth (`ip -d -j link show`/`ip -details
link show` run directly over ssh, bypassing the agent's own responses
entirely, not just trusting what the API said back): (1) `GET
.../can-config` on `can0` in its broken post-incident state returned
exactly `{"bitrate": 250000, "dbitrate": 2000000, "fd": true}`, matching
`ip -d -j link show` precisely; (2) brought it down explicitly, then
`POST .../can-config` with `fd: false` -- the raw `ip -d -j` output
afterward showed `ctrlmode` and `data_bittiming` gone entirely (FD
genuinely off, not just hidden) and `flags` with no `UP` (state correctly
preserved as down), confirming both bugs 2 and 3 fixed at once; (3)
brought it back up, then `POST .../can-config` with the original
`bitrate: 500000, dbitrate: 2000000, fd: true` -- final `ip -details link
show can0` matched the *very first* output in the user's report
character-for-character (`<NOARP,UP,LOWER_UP,ECHO>`, `bitrate 500000`,
`dbitrate 2000000`, `<FD>`), restoring the box's real hardware to exactly
where it was before any of this started. `CanConfigDialog`'s pre-fill
itself verified separately with three direct widget-construction cases
(offscreen, no live hardware needed for this part since the data-fetching
correctness was already proven above): real FD state (500000/2000000/FD
checked, Data bitrate field enabled), classic CAN state (250000/unchecked,
Data bitrate field disabled), and no current state available (falls back
to the original fixed defaults) -- all three matched exactly. Test agent,
its process, and the driver script all cleaned up afterward; the user's
own live agent/gateway on port 8090 confirmed untouched throughout.

## Done (2026-08-19) — dark theme + sidebar redesign

User provided a mockup image and asked for the UI to be adapted to match
it: sidebar navigation, dark navy theme, blue "primary"/red "danger"
accent buttons, colored status badges. Three scoping questions asked
first (all answered with the recommended option): Settings holds host
management (moved off the always-visible top bar); dark theme applies
everywhere (all pages + all dialogs, not just the shell shown in the
mockup); colors approximated from the mockup image itself.

`QListWidget`+`QStackedWidget` sidebar (five pages: Gateway, Nodes, Test
Runs, Interfaces, Settings) replaces the old `QTabWidget` -- gives full
control over the selected-item pill highlight and per-item icons that
`QTabBar`'s own styling can't easily reach. A single `_DARK_STYLESHEET`
(approximated palette: `#1b1d27` app background, `#14151d` sidebar,
`#3d6fe0` primary/accent blue, `#e0524f` danger red, `#46b285` good-status
green) applied once via `QApplication.setStyleSheet()`, covering every
page and every dialog. A `_mark()` helper tags buttons with a Qt dynamic
`class` property (`primary`/`danger`) the stylesheet's
`QPushButton[class="..."]` selectors key off of -- applied to every
Start/OK button (blue) and every Stop/Down/Delete button (red) across all
four data pages and all five dialogs. Status-ish table cells get small
color helpers: `_process_status_color()` (green "running", red
"exited:N" for N≠0, default for "stopped" -- shared across the
Gateway/Nodes/Test-Runs tables, which all use the same status-string
shape) and `_bool_color()` (green "Yes"/muted "No" for Managed); the Test
Report dialog's existing `_VERDICT_COLORS` reused for the Test Runs
table's Result column and re-tuned from its original light-background
colors to the new dark palette. Host management (host list, Add/Remove
Host, Save/Load Session) moved out of the old always-visible top bar into
a new Settings page -- these are host *definitions*, shared setup every
other page's data depends on, not something touched as often as the
per-page tables themselves.

Verified on real hardware (`agn-testcomputer`), read-only against the
user's own configured host (never created/modified/deleted anything): a
throwaway Qt driver script (Xvfb + `xcb`, not committed) constructed a
real `MainWindow`, clicked through all five sidebar pages, and
screenshotted each one plus a real dialog (`NewInstanceDialog`). All five
nav icons render correctly with no missing-glyph boxes (`▤` Gateway, `◈`
Nodes, `✓` Test Runs, `⇄` Interfaces, `⚙` Settings); real data (gateway
instances, nodes, test runs, and interfaces including physical
`can0`/`can1`, untouched) rendered correctly styled in every table;
Start/OK buttons blue, Stop/Down/Delete buttons red, Managed "Yes" green;
the dialog matched the dark theme throughout, no light-mode popup on a
dark main window. The same screenshots were then used to regenerate
`admin_gui/docs/screenshot.png`, `new_instance_dialog.png`,
`nodes_tab.png`, `new_node_dialog.png` -- the previously-committed ones
were from the old light-mode/tab-bar layout and had gone stale. All test
artifacts (Xvfb, driver scripts, screenshots not meant for the repo)
cleaned up afterward. Full account: `test/AdminGui.md`'s
`TC_AdminGui_021_dark_theme_sidebar_redesign`.

**Update (2026-08-19, continued):** after trying the real redesign, user:
"Please move the buttons add host, remove host save session, load
session back to the gateway tab. The settings tab shall stay empty for
now." Host management (host list, Add/Remove Host, Save/Load Session)
moved from its brief stay on the Settings page back to the top of the
**Gateway** page -- `_build_gateways_tab()` regained the host bar it
originally had before the redesign, `_build_settings_tab()` now just
`return QWidget()`. (The user had also swapped a couple of icon glyphs
themselves in the meantime -- the app-title mark back from anchor to
sailboat, and the sidebar's page icons to chess pieces -- both left
exactly as they set them; only the requested structural move was made.)
Verified on real hardware (`agn-testcomputer`), read-only, the same way
as the redesign itself: a throwaway Qt driver script (Xvfb + `xcb`, not
committed) confirmed the Gateway page now shows the real host list (1
entry) and both host-management buttons rows correctly at the top, and
the Settings page renders genuinely empty. `admin_gui/docs/screenshot.png`
regenerated again to match (the other three doc screenshots -- dialogs
and the Nodes page -- were unaffected by this move and didn't need
regenerating). All test artifacts cleaned up afterward.

## Done (2026-08-20) — CAN Config column + fixed a misleading vcan dialog

User: "add a new coloum where the current settings of a can interface is
shown baudrate canfd maybe also samplepoint(s) in % and/or seg1 seg2 and
sjw ect. When the interface is virtual then just mark it with virtual,
also a virtual can shall not show any boudrate, as it does now when
clicking on configure can." Two parts: a new read-only column, and a
real bug in **Configure CAN…**'s existing behavior for vcan (it opened
the same bitrate-editing dialog with fixed 500000/no-FD defaults
regardless of interface type, misleadingly implying a vcan had a real,
editable bitrate).

**Agent side** (`ui/launcher_agent.py`): `_list_interfaces()` now calls
`ip -d -j link show` (details, `-d`) instead of the plain listing --
still exactly one `ip` subprocess call for the whole table, not one per
row. `linkinfo.info_kind` (`"can"`/`"vcan"`) now classifies `type`
directly, which also let a second, separate `ip ... type vcan` call the
old version needed (just to build a vcan-name set for classification) be
removed entirely -- a real simplification, not just an addition. New
`_parse_can_phase()` (one bittiming block -> `{bitrate, sample_point_pct,
prop_seg, phase_seg1, phase_seg2, sjw}`, converting the raw `sample_point`
fraction like `"0.875"` to a percentage) and `_parse_can_info_data()`
(a full `linkinfo.info_data` -> `{fd, nominal, data?}`) are shared
between the new per-row `can_config` field and `_read_can_config()`
(the Configure CAN dialog's own prefill, refactored to reuse the same
parse and flatten it to its existing `{bitrate, dbitrate, fd}` shape --
no change to that endpoint's contract or to `agent_client.py`/
`CanConfigDialog`).

**Client side**: `admin_gui/main.py` gained a **CAN Config** column
(Host, Name, Type, **CAN Config**, Up, Operstate, MAC) via
`_format_can_config_cell()` (`"virtual"` for vcan, `"<bitrate> bps,
<SP>% SP[ / FD <dbitrate> bps, <SP>% SP]"` for a real configured CAN
link, `"—"` otherwise) and `_format_can_config_tooltip()` (the
prop_seg/phase_seg1/phase_seg2/sjw detail for each phase, on hover,
since it doesn't fit the cell). `configure_can_selected()` now checks
the selected interface's `type` *before* opening `CanConfigDialog`:
`vcan` gets a clear "has no real bitrate or CAN FD configuration to set"
message instead of the dialog (matching **Delete**'s existing
refused-client-side-with-a-clear-message pattern for a vcan/veth-only
action); any other non-`can` type gets an equivalent message rather than
silently attempting a configure that was never going to do anything
sensible either.

**Verified on real hardware** (`agn-testcomputer`), strictly read-only
(the box now has two live gateways, `launcher_agent`, and four other
`ui/*.py` services running at once -- confirmed via `ps`/`ss`, an
isolated test agent used for every check, no create/delete/up/down/
configure calls made against anything real this pass). `curl` directly
against the new listing: `can0`/`can1` (currently FD-enabled, bitrate
250000/2000000 from the user's own separate testing since the last
session, left exactly as found) returned full `can_config` with
`sample_point_pct` 87.5/75.0 and all four seg/sjw fields on both phases;
`vcan0`, `veth0`, `lo` all returned `can_config: null` correctly; the
flat `GET .../can-config` endpoint (dialog prefill) still returns the
unchanged shape post-refactor. Then through the real Qt code path (Xvfb
+ `xcb`, a throwaway driver script, not committed): confirmed the real
table's 7 columns; confirmed `can0`'s real cell text and tooltip
content; confirmed `vcan0`/`veth0`/`lo` cells read `"virtual"`/`"—"`/
`"—"` respectively; selected `vcan0` and called the real
`configure_can_selected()`, confirming it showed exactly one info
message (captured, not a blocking modal) and never constructed
`CanConfigDialog` at all. A screenshot confirmed the column renders
correctly across every interface type present, including the user's own
live `can0`/`can1`, untouched throughout. All test artifacts (isolated
agent process, Xvfb, driver script) cleaned up afterward; every one of
the user's own live processes (two gateways, `launcher_agent`, four
`ui/*.py` services) confirmed still running under the same PIDs
afterward.

## Done (2026-08-20, continued) — audited the gateway's full env var surface, added Node tick fields

User: "figure out how many env variables the gateway can use. I know
there are the can and eth interfaces, the ticktime and the port. have i
forgott anything?" A full source grep (not a doc reread) of everything
`boat_gateway` actually calls `getenv()` on -- `main.cpp` +
`replay_engine.cpp` (same binary; loaded plugins take config only via
their own `?{json}` query string, confirmed zero `getenv()` calls
anywhere under `src/plugins/`) -- found 9 total: the 4 the user named
(`BOAT_CAN_INTERFACES`, `BOAT_ETH_INTERFACES`, `BOAT_GRPC_PORT`,
`BOAT_NODE_TICK_MS`/`_US`), `BOAT_NODE_PLUGINS`, and three genuinely
undocumented ones -- `BOAT_TLS_CERT`/`BOAT_TLS_KEY`/`BOAT_TLS_CLIENT_CA`
(opt-in server-side TLS, confirmed absent from `AGENTS.md`, `README.md`,
and admin_gui, present only in the source). Also flagged `BOAT_HIL_ENABLED`/
`BOAT_VCAN_IFACE` as a likely point of confusion -- real `BOAT_*` env
vars, but read by the ctest HIL test binaries, not by `boat_gateway`
itself.

User: TLS isn't needed for now (Google-requires-it-for-something-else,
not for regular connections) -- skip it. Follow-up: "please add a field
for the BOAT_NODE_TICK_NS and BOAT_NODE_TICK_MS to the New Instance /
Edit window with default BOAT_NODE_TICK_MS = 1 with a comment that
BOAT_NODE_TICK_NS will override BOAT_NODE_TICK_MS when both are set."
Corrected in passing: the real env var is `BOAT_NODE_TICK_US`
(microseconds), not `_NS` (nanoseconds) -- confirmed against the exact
`getenv()` call in `main.cpp` found during the audit above; implemented
using the real name, since a `_NS` field would silently do nothing (the
gateway never reads it).

Found while implementing: `create_instance()`/`update_instance()`
(`agent_client.py`) already accepted `tick_ms`/`tick_us`, and
`_format_command_line()`/`_parse_command_line()` (the Equivalent Command
Line panel and its own paste-and-fill) already handled both -- the *only*
gap was `NewInstanceDialog` itself never having input fields for them,
so `result_payload()` never included them and a pasted
`BOAT_NODE_TICK_MS=...` line's parsed value was silently dropped instead
of landing anywhere.

**Fix**: two new fields, **Node tick (ms)** (pre-filled `"1"`, the
gateway's own compiled-in default -- literal text, not a placeholder, so
it's visibly what's about to be sent, not just implied) and **Node tick
(µs)** (blank, placeholder "leave blank unless you need sub-ms
precision"), plus a small note underneath: "BOAT_NODE_TICK_US overrides
BOAT_NODE_TICK_MS when both are set. This is the minimum achievable
PDU/node-plugin cycle time, not a per-message rate." Edit mode overrides
the `"1"` default only when the instance has its own explicit
`tick_ms` saved (mirroring the existing gRPC-port pre-fill pattern);
`_parse_and_fill()` now also populates both fields from a pasted line
(previously silently ignored parsed tick values); `result_payload()`
includes both, parsed as `int` when non-blank else `None`.

**Verified on real hardware** (`agn-testcomputer`), on an isolated test
agent (a fresh port -- the box had `launcher_agent` and four other
`ui/*.py` services running at the time, confirmed via `ps`/`ss` and left
untouched, no gateway processes were running so nothing else needed
avoiding): a throwaway Qt driver script (Xvfb + `xcb`, not committed)
confirmed a fresh dialog's `tick_ms`/`tick_us` fields read `"1"`/`""`
and `result_payload()` returned `{"tick_ms": 1, "tick_us": None}`;
created a real instance through the isolated agent with that payload and
confirmed the stored instance came back with `tick_ms: 1, tick_us:
None`; reopened it in Edit mode and confirmed the dialog correctly
pre-filled `"1"`/`""` from the *saved* instance (not just the
constructor default); changed `tick_us` to `500` and confirmed the real
`update_instance()` call persisted `tick_us: 500`; pasted a line
containing `BOAT_NODE_TICK_MS=5` into a fresh dialog and confirmed
**Parse && Fill** populated `tick_ms_edit` with `"5"` (previously this
field didn't exist at all, so this exact input silently did nothing). A
screenshot confirmed the fields and note render correctly, and was also
used to regenerate the stale `admin_gui/docs/new_instance_dialog.png`.
Test instance and all test artifacts cleaned up afterward; every one of
the user's own live processes confirmed running under the same PIDs
afterward.

## Next steps (not started)

- Decide instance persistence approach once the "agent restart loses
  everything" gap actually costs someone time.
- Possible future extension: an "Adopt" action turning a discovered
  `external:<pid>` row into a real tracked `GatewayInstance` (the agent
  already recovers enough from `/proc/<pid>/environ` to build one) --  not
  requested yet, noted here since the discovery groundwork already exists.

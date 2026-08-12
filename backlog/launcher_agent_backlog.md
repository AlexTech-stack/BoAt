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
- **No interface creation.** `GET /api/host/info` lists existing interfaces
  (read-only) for populating a client's dropdowns; creating vcan/veth pairs
  is still only in `ui/launcher.py`. Deliberately not duplicated yet —
  revisit if the admin tool needs to be a one-stop shop rather than assuming
  interfaces already exist.
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

## Next steps (not started)

- Interface-creation UI / agent endpoints (still deliberately deferred, see
  above).
- Decide instance persistence approach once the "agent restart loses
  everything" gap actually costs someone time.

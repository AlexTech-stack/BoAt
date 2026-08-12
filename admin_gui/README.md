# BoAt Admin

A PySide6 desktop client for one or more `ui/launcher_agent.py` instances.
Add a host per machine that runs gateways; the app polls each host's REST API
and shows one aggregated table of every gateway instance across all of them.

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
   it. Plugins additionally take an optional JSON config
   (`{"iface": "vcan0"}`) alongside the path. **Remove selected** drops an
   already-added entry. Also: an optional explicit gRPC port (blank = auto-
   allocated by that host's agent), and an optional gateway binary override.
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

## What's not here yet

- No interface-creation UI (create vcan/veth from this app) — the agent
  doesn't expose that yet either; use `ui/launcher.py`'s browser UI for
  interface setup on a given host for now.
- No persistence for *instance definitions* on the agent side — an agent
  restart forgets stopped instances (see the backlog doc). This app doesn't
  work around that; it just reflects whatever the agent currently reports.
- No auth — same trust model as every other `ui/*.py`/`tools/*.py` service
  in this repo today (assumes a trusted lab network).

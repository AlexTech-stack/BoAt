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

## Usage

1. **Add Host** — display name + agent URL (e.g. `agn-testcomputer:8090`;
   `http://` is added automatically if omitted). Each host needs
   `ui/launcher_agent.py` already running there (`python3 ui/launcher_agent.py`,
   default port 8090). Hosts persist across runs in `~/.boat/admin_hosts.json`.
2. The instance table aggregates every instance from every added host,
   refreshing every 2 seconds. A host's dot in the host list is filled (●)
   when reachable, hollow (○) when not.
3. **New Instance…** picks a host, then defines CAN/Eth interfaces
   (comma-separated), node plugins (one per line: a `.so` path, optionally
   followed by a space and a JSON config object), an optional explicit gRPC
   port (blank = auto-allocated by that host's agent), and an optional
   gateway binary override.
4. Select a row, then **Start** / **Stop** / **Delete** act on it (Delete is
   refused by the agent while the instance is running). The log panel below
   shows that instance's stdout/stderr, refreshing on the same 2s cadence.

## What's not here yet

- No interface-creation UI (create vcan/veth from this app) — the agent
  doesn't expose that yet either; use `ui/launcher.py`'s browser UI for
  interface setup on a given host for now.
- No persistence for *instance definitions* on the agent side — an agent
  restart forgets stopped instances (see the backlog doc). This app doesn't
  work around that; it just reflects whatever the agent currently reports.
- No auth — same trust model as every other `ui/*.py`/`tools/*.py` service
  in this repo today (assumes a trusted lab network).

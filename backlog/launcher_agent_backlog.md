# Launcher Agent / Admin Tool Backlog

Tracking the gateway-administration effort: a per-host **launcher agent**
(`ui/launcher_agent.py`) exposing a multi-instance gateway lifecycle REST API,
eventually driven by a **PySide6 admin app** that talks to one or more agents
over the network. See `AGENTS.md`'s "Launcher Agent" section for the API and
the federated architecture rationale.

Status: agent v1 and a PySide6 admin client both exist, hardware-verified
headlessly. Not yet visually verified with a real display.

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
- **Admin client exists but is headless-tested only** (see "PySide6 admin
  client" below) — real layout/rendering not yet checked on an actual
  screen.
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

Not yet visually verified with a real display/window manager — only the
logic paths (construction, polling, and every button-triggered action
method, including the ones gated behind a modal confirmation) were
exercised.

## Next steps (not started)

- Visual verification with a real display (the offscreen smoke test above
  proves the logic paths work, not that the layout looks right).
- Interface-creation UI / agent endpoints (still deliberately deferred, see
  above).
- Decide instance persistence approach once the "agent restart loses
  everything" gap actually costs someone time.

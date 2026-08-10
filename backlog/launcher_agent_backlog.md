# Launcher Agent / Admin Tool Backlog

Tracking the gateway-administration effort: a per-host **launcher agent**
(`ui/launcher_agent.py`) exposing a multi-instance gateway lifecycle REST API,
eventually driven by a **PySide6 admin app** that talks to one or more agents
over the network. See `AGENTS.md`'s "Launcher Agent" section for the API and
the federated architecture rationale.

Status: agent v1 exists and is hardware-verified. No client yet.

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
- **No admin client yet.** The agent is only exercised via `curl` so far.
  The planned PySide6 app (host list → aggregated instance table → per-host
  REST calls) doesn't exist yet.
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

## Next steps (not started)

- PySide6 admin app: host list, aggregated instance table, create/start/stop
  forms driving the REST API above.
- Decide instance persistence approach once the "agent restart loses
  everything" gap actually costs someone time.

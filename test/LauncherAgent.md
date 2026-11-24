# TestSet: LauncherAgent

System-level tests for `ui/launcher_agent.py` — the per-host, multi-instance
gateway lifecycle REST API underlying the planned admin tool. See
`backlog/launcher_agent_backlog.md` for scope/known gaps and `AGENTS.md`'s
"Launcher Agent" section for the API surface.

Common precondition: agent running (`python3 ui/launcher_agent.py`,
default port 8090); `boat_gateway` built; at least one CAN interface
(e.g. `vcan0`) available.

---

### TC_LauncherAgent_001_create_and_auto_allocate_port

**TestSets:** [LauncherAgent]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. `POST /api/instances` with no `grpc_port` given, twice in a row
2. Inspect the `grpc_port` field of each response

**Expected:**
- First instance gets the base port (50051 by default); second gets the
  next free one (50052) — reserved by the first instance's *definition*,
  not just while it's running

---

### TC_LauncherAgent_002_start_independent_instances

**TestSets:** [LauncherAgent]

**Preconditions:**
- Two instances defined per TC_LauncherAgent_001, both referencing `vcan0`

**TestSteps:**
1. `POST /api/instances/{id}/start` for both
2. `boat --host localhost:<port>` against each port independently
3. `GET /api/instances/{id}/log` for both

**Expected:**
- Both start with distinct PIDs, both log `[Gateway] gRPC server listening
  on 0.0.0.0:<their own port>`
- Both independently reachable via the CLI at their respective ports

---

### TC_LauncherAgent_003_rejects_duplicate_explicit_port

**TestSets:** [LauncherAgent], [Error]

**Preconditions:**
- An instance already defined with `grpc_port: 50051` (running or not)

**TestSteps:**
1. `POST /api/instances` with `grpc_port: 50051` explicitly

**Expected:**
- Rejected with HTTP 400 and a message naming the port conflict; the
  existing instance is unaffected

---

### TC_LauncherAgent_004_delete_refused_while_running

**TestSets:** [LauncherAgent], [Error]

**Preconditions:**
- A running instance

**TestSteps:**
1. `DELETE /api/instances/{id}` while it is running
2. `POST /api/instances/{id}/stop`, then repeat the delete

**Expected:**
- Step 1 refused with HTTP 409 naming the instance as still running
- Step 2's stop succeeds (`exit_code: 0`); the delete then succeeds

---

### TC_LauncherAgent_005_host_info

**TestSets:** [LauncherAgent]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. `GET /api/host/info`

**Expected:**
- Returns the host's hostname, its system CAN/Ethernet interfaces
  (read-only listing), discovered `boat_gateway` binaries under
  `build/{debug,release}`, and discovered plugin `.so` files

---

### TC_LauncherAgent_006_update_edit_in_place

**TestSets:** [LauncherAgent]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. Create an instance; `PUT /api/instances/{id}` while stopped with a
   changed name, an added interface, and the *same* `grpc_port` it already
   has
2. `POST .../start`, then `PUT /api/instances/{id}` again with a different
   name while it's running

**Expected:**
- Step 1 applies cleanly (same id, updated fields) -- submitting the
  instance's own current port back is not mistaken for a port conflict
  with itself
- Step 2 is refused with 409 (`"... is running; stop it first"`), matching
  `DELETE`'s running-refusal; the instance keeps running unaffected

---

### TC_LauncherAgent_007_invocation_independent_paths

**TestSets:** [LauncherAgent]

**Preconditions:**
- Agent NOT already running

**TestSteps:**
1. Start the agent via a relative path that itself contains `..`, from a
   sibling directory: `cd admin_gui && python3 ../ui/launcher_agent.py`
2. `GET /api/host/info`; inspect `gateway_bins`

**Expected:**
- Discovered gateway binary path is clean (`.../boat-platform/build/...`),
  not `.../admin_gui/../boat-platform/build/...` -- the agent's own path
  resolution must not depend on which directory or which relative path it
  was launched from

---

### TC_LauncherAgent_008_discovers_external_gateways

**TestSets:** [LauncherAgent]

**Preconditions:**
- Agent running; a `boat_gateway` started **manually** (not via this
  agent), e.g. `BOAT_CAN_INTERFACES=vcan0 BOAT_GRPC_PORT=50077
  ./boat_gateway &`

**TestSteps:**
1. `GET /api/instances`
2. `POST /api/instances/external:<pid>/start`, `/delete`, and
   `GET /api/instances/external:<pid>` (each should be refused)
3. `GET /api/instances/external:<pid>/log`
4. `POST /api/instances/external:<pid>/stop`

**Expected:**
- Step 1 includes an entry with `id: "external:<pid>"`, `managed: false`,
  and correct `can_ifaces`/`eth_ifaces`/`grpc_port`/`node_plugins`
  (including per-plugin config) recovered from the process's own
  environment -- alongside any agent-managed instances (`managed: true`)
- Step 2's three calls are each refused with HTTP 400 and a clear message
  ("... isn't managed by this agent ... only Stop is supported")
- Step 3 returns a fixed explanatory log entry instead of erroring
- Step 4 actually terminates the process (`SIGTERM`); it no longer appears
  in a subsequent `GET /api/instances`

---

### TC_LauncherAgent_009_node_lifecycle

**TestSets:** [LauncherAgent]

**Preconditions:**
- Common preconditions of this TestSet, plus at least one script under
  `boat-platform/nodes/` and a running `boat_gateway`

**TestSteps:**
1. `GET /api/node-scripts`
2. `POST /api/nodes` with a script, `target_host` pointing at the running
   gateway, and `extra_args`; `POST .../start`
3. `PUT`/`DELETE /api/nodes/<id>` while running (expect 409 each)
4. `GET /api/nodes/<id>/log`
5. `POST .../stop`; `PUT` while stopped (rename); `DELETE`

**Expected:**
- Step 1 lists discovered scripts with name/path/docstring/interactive
- Step 2's node genuinely functions (its BOAT_HOST-driven behavior is
  observable on the target gateway's bus), not just "a process exists"
- Step 3 both refused with `409` and a message naming the running node
- Step 5's edit-while-stopped applies cleanly; delete then succeeds

---

### TC_LauncherAgent_010_loopback_bind_by_default

**TestSets:** [LauncherAgent], [Error]

**Preconditions:**
- No agent on port 8095

**TestSteps:**
1. `BOAT_AGENT_PORT=8095 python3 ui/launcher_agent.py`; `ss -ltn | grep 8095`
2. Repeat with `BOAT_AGENT_BIND=` (set but empty)
3. Repeat with `BOAT_AGENT_BIND=0.0.0.0`
4. From another host, `curl http://<this-host>:8095/api/host/info` in each configuration

**Expected:**
- Steps 1 and 2: listens on `127.0.0.1:8095` only -- an empty value means loopback, not every
  interface (this is a **BREAKING** change in v0.2.0)
- Step 3: listens on `0.0.0.0:8095` (the explicit opt-in for federated `admin_gui` use)
- Step 4: refused in steps 1-2, answered in step 3

---

### TC_LauncherAgent_011_adopt_external_gateway

**TestSets:** [LauncherAgent]

**Preconditions:**
- A gateway started by hand (not by the agent), e.g. on port 50061 with vcan0 and plugins
- Agent running on 8095

**TestSteps:**
1. `curl -s localhost:8095/api/instances` -- find the `external:<pid>` row
2. `curl -s -X POST localhost:8095/api/instances/external:<pid>/adopt -H 'content-type: application/json' -d '{"name":"adopted-gw"}'`
3. `curl -s localhost:8095/api/instances`
4. Stop the adopted instance through the agent; then Start it

**Expected:**
- Step 1: row with `"managed": false`, `"status": "running"`, the gateway's real port,
  interfaces and plugin configs (recovered from `/proc/<pid>/environ`)
- Step 2: returns a new short id, `"name": "adopted-gw"`, `"managed": true`, same pid --
  the gateway is **not** restarted
- Step 3: the `external:` row is gone, replaced by the managed one
- Step 4: Stop signals the adopted pid and the gateway exits; Start spawns a fresh,
  normally-managed gateway with the same config (#40)

---

### TC_LauncherAgent_012_stopped_definitions_persist

**TestSets:** [LauncherAgent]

**Preconditions:**
- Agent running with `BOAT_AGENT_STATE_FILE=/tmp/agent_state.json`

**TestSteps:**
1. `curl -s -X POST localhost:8095/api/instances -H 'content-type: application/json' -d '{"name":"persist-me","can_ifaces":["vcan0"],"grpc_port":50071}'`
2. `ls -l /tmp/agent_state.json`; inspect its JSON
3. Restart the agent with the same state file; `curl -s localhost:8095/api/instances`

**Expected:**
- Step 2: mode `-rw-------` (0600); contains `persist-me` with `status` absent (definitions
  only); a *running* adopted/managed gateway is deliberately **not** in the file
- Step 3: `persist-me` is back as `stopped`, `managed: true`; a still-running hand-started
  gateway reappears as `external:<pid>`, not duplicated

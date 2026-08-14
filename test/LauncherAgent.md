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

**Verdict:** OK

**Result:**
Verified on real hardware (`agn-testcomputer`): two instances created via
curl with no `grpc_port` got 50051 and 50052 respectively, neither started
yet at the time of the second create call.

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

**Verdict:** OK

**Result:**
Verified on real hardware: distinct PIDs, each instance's log showed its own
port in the `[Gateway] gRPC server listening on 0.0.0.0:<port>` line, and
`boat --host localhost:50051 frame list-ifaces` /
`boat --host localhost:50052 frame list-ifaces` each independently listed
`vcan0`.

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

**Verdict:** OK

**Result:**
Verified on real hardware: `{"detail":"port 50051 is already assigned to
another tracked instance"}`, HTTP 400.

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

**Verdict:** OK

**Result:**
Verified on real hardware: delete-while-running gave
`{"detail":"instance 'dbfe7584' is running; stop it first"}` (409); after
`stop` (status `stopped`, `exit_code: 0`), the delete returned `{"ok":true}`
and the instance no longer appeared in `GET /api/instances`.

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

**Verdict:** OK

**Result:**
Verified on real hardware: response included `hostname: "agn-testcomputer"`
and `vcan0` among the listed interfaces.

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

**Verdict:** OK

**Result:**
Verified on real hardware: created an instance on port 50051 with
`can_ifaces: ["vcan0"]`; `PUT` while stopped with
`{"name": "edit-test-renamed", "can_ifaces": ["vcan0", "vcan1"],
"eth_ifaces": ["veth0"], "grpc_port": 50051}` applied cleanly (same id,
all fields updated, no port-conflict error despite resubmitting its own
port). Started it, then `PUT` with a different name returned
`{"detail":"instance '...' is running; stop it first"}` (409) as expected.

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

**Verdict:** OK

**Result:**
Verified on real hardware: before the fix, launching via `cd admin_gui &&
python3 ../ui/launcher_agent.py` (exactly reproducing a real user's
invocation) produced `gateway_bins: [".../admin_gui/../boat-platform/
build/debug/.../boat_gateway"]` -- confirmed against the user's own live
agent process (`GET /api/instances` showed the same mangled path on a real
running instance). Root cause: `Path(__file__).parent.parent` without
`.resolve()`; Python absolutizes a relative `__file__` by prepending the
CWD without collapsing `..`, so `.parent.parent`'s lexical (non-normalizing)
stripping carried the `admin_gui/..` straight through. Fixed by adding
`.resolve()` (already the convention used everywhere else in the repo,
e.g. `tools/pdu_editor.py`). Re-verified with the same invocation after the
fix (on a scratch port, so as not to disturb the user's own still-running
session): `gateway_bins: [".../boat-platform/build/debug/.../boat_gateway"]`,
clean.

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

**Verdict:** OK

**Result:**
Verified on real hardware: started a `boat_gateway` by hand with
`BOAT_CAN_INTERFACES=vcan0,vcan1 BOAT_GRPC_PORT=50077
BOAT_NODE_PLUGINS=.../can_tp.so?{"iface":"vcan0"}` (properly quoted via a
shell script -- a first attempt lost the JSON's quotes to shell-escaping
across the SSH/bash layering, which the parser correctly treated as
invalid JSON and fell back to `{}` for rather than crashing, exactly as
designed). `GET /api/instances` returned it with `id: "external:2343356"`,
`managed: false`, `can_ifaces: ["vcan0","vcan1"]`, `grpc_port: 50077`, and
the plugin's config correctly as `{"iface": "vcan0"}`. `start`/`delete`/
single-`GET` each returned the expected 400; `log` returned the fixed
"not captured" message; `stop` sent `SIGTERM` and the process was
confirmed gone via `ps` and absent from the next `GET /api/instances`.

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

**Verdict:** OK

**Result:**
Verified on real hardware: `GET /api/node-scripts` listed
`cyclic_can_sender`/`can_request_responder` with correct docstrings.
Created a `can_request_responder` node with `target_host: "localhost:50056"`
and matching `extra_args`; after `start`, sending a CAN request directly to
that gateway produced the correct reply on the wire (`candump`:
`0x7E0` → `0x7E8` with the configured payload) -- confirming the node's
`BOAT_HOST` was genuinely set by the agent's own subprocess env, no
`--address` flag involved anywhere in this path. `PUT`/`DELETE` while
running both returned `409` naming the node id. `GET .../log` showed the
`[agent] started PID ... (BOAT_HOST=localhost:50056)` line. After `stop`
(`exit_code: 0`), `PUT` renamed it cleanly, and `DELETE` succeeded.

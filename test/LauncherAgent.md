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

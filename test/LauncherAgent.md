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

# TestSet: AdminGui

System-level tests for `admin_gui/` — the PySide6 desktop client for one or
more `ui/launcher_agent.py` instances. See `backlog/launcher_agent_backlog.md`
for status/known gaps and `admin_gui/README.md` for usage.

Common precondition: `pip install -r admin_gui/requirements.txt`; at least
one `ui/launcher_agent.py` reachable.

---

### TC_AdminGui_001_add_host_and_poll

**TestSets:** [AdminGui]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. Launch `python3 admin_gui/main.py`
2. **Add Host** with a reachable agent's name/URL
3. Observe the host list dot and the instance table over a few poll cycles

**Expected:**
- Host list shows a filled dot (●) once the agent responds; the instance
  table reflects that agent's `GET /api/instances` (empty table if none
  defined yet), refreshing roughly every 2s
- An unreachable host shows a hollow dot (○) instead of crashing the app

**Verdict:** OK

**Result:**
Verified headlessly on real hardware (`agn-testcomputer`,
`QT_QPA_PLATFORM=offscreen`, logic paths only — no real display/window
manager exercised): `MainWindow` constructed, its background `PollWorker`
pulled a real snapshot from a live agent within one poll cycle.

---

### TC_AdminGui_002_create_start_stop_via_ui_actions

**TestSets:** [AdminGui]

**Preconditions:**
- A host added and reachable, with a CAN interface (e.g. `vcan0`) available
  on it

**TestSteps:**
1. **New Instance…**, fill in name + CAN interfaces, leave gRPC port blank
   (auto), submit
2. Select the new row, click **Start**
3. Click **Stop**

**Expected:**
- Instance appears in the table with an auto-allocated port; after Start its
  status becomes `running` with a real PID; after Stop it returns to
  `stopped` with `exit_code: 0`
- Log panel shows the gateway's stdout/stderr while running, including the
  `[Gateway] gRPC server listening on 0.0.0.0:<port>` line

**Verdict:** OK

**Result:**
Verified headlessly on real hardware: drove the exact code paths a real
user's clicks trigger — `AgentClient.create_instance()` (as the New Instance
dialog's accept handler calls it) followed by the actual
`MainWindow.stop_selected()` method against a table selection set to the new
row. Instance transitioned stopped→running (real PID)→stopped
(`exit_code: 0`). Log content itself not checked in this pass (covered
already at the agent level by TC_LauncherAgent_002); UI's log-panel
rendering not yet visually verified.

---

### TC_AdminGui_003_delete_refused_while_running

**TestSets:** [AdminGui], [Error]

**Preconditions:**
- A running instance selected in the table

**TestSteps:**
1. Click **Delete** while the selected instance is running

**Expected:**
- A warning dialog surfaces the agent's 409 rejection (`"... is running;
  stop it first"`); the instance keeps running

**Verdict:** OK

**Result:**
Verified headlessly on real hardware, with a caveat worth recording: the
first pass at this test auto-dismissed the confirmation `QMessageBox` via
`.accept()`, which turned out to be a false positive -- `.accept()` sets the
dialog's result code but not `clickedButton()`, so `QMessageBox.question()`
read back `NoButton` (not `Yes`), and `delete_selected()` took its "not
confirmed" early-return without ever calling `delete_instance()`. Fixed by
clicking the dialog's actual `Yes` button object instead. Re-run with that
fix: a repeating watchdog timer observed **two** modals in sequence during
one `delete_selected()` call -- the confirmation ("Delete this instance
definition?"), then, after it proceeded to the real `DELETE` call and got
the agent's 409, a second dialog titled "Delete failed" with the exact
agent message (`"... is running; stop it first"`). The instance was
confirmed still `running` afterward via `GET /api/instances/{id}`.

---

### TC_AdminGui_004_multi_host_aggregation

**TestSets:** [AdminGui]

**Preconditions:**
- Two reachable agents on different hosts (or two agent processes on
  different ports on one host), each with at least one instance defined

**TestSteps:**
1. Add both hosts
2. Observe the aggregated instance table

**Expected:**
- Table shows instances from both hosts together, each row's Host column
  identifying which; Start/Stop/Delete on a row act against the correct
  host's agent

**Verdict:** OK

**Result:**
Verified headlessly on real hardware: ran two agents on the same host at
different ports (8098, 8099) as a stand-in for two separate machines, added
both, created one instance on each. The aggregated snapshot contained
instances from both host URLs, and `win.rebuild_table()` produced exactly 2
rows with distinct Host-column values (`host-a`, `host-b`) matching each
instance's actual origin.

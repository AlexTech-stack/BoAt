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

---

### TC_AdminGui_005_interfaces_and_plugins_columns

**TestSets:** [AdminGui]

**Preconditions:**
- A reachable agent; at least one CAN and one Eth interface available on it

**TestSteps:**
1. **New Instance…** with multiple CAN interfaces, an Eth interface, and
   two node plugins — one with an `{"iface": ...}` config, one without
2. Observe the row's Interfaces and Plugins columns

**Expected:**
- Interfaces column lists CAN + Eth interfaces together, comma-separated
- Plugins column lists each plugin's `.so` basename; the one with an
  `iface` config shows it in brackets (`can_tp.so [vcan0]`), the one
  without shows just the basename (`pdu_router.so`)
- Columns aren't truncated -- sized to their actual content

**Verdict:** OK

**Result:**
Verified with a real screenshot on real hardware (`agn-testcomputer`, Xvfb +
`QWidget.grab()`, not headless-only): created an instance with
`can_ifaces=["vcan0","vcan1"]`, `eth_ifaces=["veth0"]`, and
`node_plugins=[pdu_router.so (no config), can_tp.so ({"iface":"vcan0"})]`.
Row rendered `Interfaces: vcan0, vcan1, veth0` and
`Plugins: pdu_router.so, can_tp.so [vcan0]`, both fully visible after adding
`resizeColumnsToContents()` (the initial pass truncated both columns under
the default even-width split).

Building this screenshot also incidentally caught a real, previously-latent
bug unrelated to these columns: `AgentClient`'s default 5s timeout could be
shorter than a stop call's worst-case server-side duration (SIGTERM + up to
a 5s wait + SIGKILL fallback), so a real Stop click could read back a false
timeout error even though the gateway did stop. Fixed by giving
`start_instance`/`stop_instance` a dedicated 15s timeout — see
`backlog/launcher_agent_backlog.md` for the full account.

---

### TC_AdminGui_006_new_instance_dropdown_pickers

**TestSets:** [AdminGui]

**Preconditions:**
- A reachable agent with at least one CAN interface and one discoverable
  plugin `.so`

**TestSteps:**
1. Open **New Instance…**; inspect the CAN/Eth/Plugin combo boxes
2. Pick an existing interface from the CAN dropdown, click **+ Add**
3. Type an interface name that doesn't exist yet into the same combo,
   click **+ Add**
4. Pick a plugin path from its dropdown, enter `{"iface": "vcan0"}` in its
   config field, click **+ Add**; pick another plugin with no config,
   click **+ Add**
5. Try adding a plugin with invalid JSON in the config field
6. Submit and inspect `result_payload()` / the created instance

**Expected:**
- Combos are pre-populated from that host's real `GET /api/host/info`
  (interfaces, plugin `.so` paths) but remain editable for manual entry
- Both the dropdown-picked and manually-typed CAN interface end up in the
  accumulated list
- Both plugin entries store correctly as structured `{path, config}` --
  the one with a config carries it, the one without gets `{}`
- Invalid JSON is rejected with a warning dialog and adds nothing
- `result_payload()`'s `can_ifaces`/`node_plugins` match exactly what was
  added via the pickers

**Verdict:** OK

**Result:**
Verified with real screenshots on real hardware (`agn-testcomputer`,
Xvfb): dropdowns were populated from a live `host_info()` call and included
real interfaces (`vcan0`, `vcan1`, `can0`, `can1`, plus PDU-DB-imported
ones) and real plugin paths (`can_tp.so`, `pdu_router.so`, etc.). Added
`vcan0` (picked) and `vcan-manual-entry` (typed) to the CAN list -- both
present. Added `can_tp.so` with `{"iface": "vcan0"}` and `pdu_router.so`
with no config -- `plugin_picker.values()` returned exactly
`[{"path": ".../can_tp.so", "config": {"iface": "vcan0"}}, {"path":
".../pdu_router.so", "config": {}}]`. Invalid JSON (`{not valid json`)
correctly showed a warning dialog and left the list unchanged.
`result_payload()` matched the picker state exactly.

The first screenshot attempt also caught a real layout bug: the plugin
path combo, config field, and Add button crammed into one row left the
config field showing only `"d json"` of its placeholder — fixed by
stacking the path row and the config field onto separate lines; a second
screenshot confirmed the fix (full paths and full placeholder text both
visible).

---

### TC_AdminGui_007_edit_instance

**TestSets:** [AdminGui]

**Preconditions:**
- A reachable agent with an existing stopped instance selected in the table

**TestSteps:**
1. Click **Edit…**; inspect the dialog's pre-filled fields and the Host
   combo
2. Add an Eth interface, change the name, submit
3. Inspect the table row and the "Equivalent command line" panel afterward

**Expected:**
- Dialog opens titled "Edit Gateway Instance", Host combo shows the
  instance's actual host and is disabled (can't reassign an instance to a
  different agent), and CAN interfaces/plugins (with their configs)/gRPC
  port are all pre-filled from the instance's current definition
- Submitting calls the agent's update (not create) endpoint -- same
  instance id afterward, not a duplicate
- The table row and the command-line panel both reflect the new name and
  the added interface immediately after

**Verdict:** OK

**Result:**
Verified end-to-end on real hardware (`agn-testcomputer`, with a real
screenshot): built the Edit dialog exactly as `edit_selected()` does for a
real instance (CAN iface `vcan0` + a `can_tp.so` plugin with `{"iface":
"vcan0"}`) and asserted every field pre-filled correctly -- host combo
`isEnabled() == False`, name/CAN-picker/plugin-picker/port all matching
the existing instance exactly. Added an Eth interface and a new name,
submitted via `update_instance()` (the same call `edit_selected()` makes),
then confirmed via `GET /api/instances/{id}` that the *same id* now carried
the new name and eth interface while keeping its original CAN interface.
Screenshot confirmed the table row showed `edited-name` with
`vcan0, veth0` in Interfaces.

---

### TC_AdminGui_008_equivalent_command_line

**TestSets:** [AdminGui]

**Preconditions:**
- An instance with CAN interfaces, a plugin with an `iface` config, and a
  non-default-looking `grpc_port` selected in the table

**TestSteps:**
1. Select the instance; read the "Equivalent command line" field
2. Click **Copy**; paste elsewhere to confirm clipboard content
3. Edit the instance (e.g. add an interface); reselect/observe the panel

**Expected:**
- Shown command line matches the
  `BOAT_CAN_INTERFACES=... BOAT_NODE_PLUGINS=<path>?<json> ... <gateway_bin>`
  form documented in `README.md`/`AGENTS.md`, using this instance's actual
  `can_ifaces`/`eth_ifaces`/`node_plugins`/`grpc_port`/`gateway_bin`
- Copy places exactly that text on the clipboard
- Panel updates to reflect the edited config without needing to reselect

**Verdict:** OK

**Result:**
Verified on real hardware: for an instance with `can_ifaces=["vcan0"]` and
one plugin (`can_tp.so`, config `{"iface": "vcan0"}`) on port 50051, the
panel read `BOAT_CAN_INTERFACES=vcan0 BOAT_GRPC_PORT=50051
BOAT_NODE_PLUGINS=<path>/can_tp.so?{"iface":"vcan0"} <path>/boat_gateway` --
asserted programmatically (not just visually) before and after an edit
that added `eth_ifaces: ["veth0"]`; the post-edit panel text included
`BOAT_ETH_INTERFACES=veth0` without any manual reselection needed (driven
by `rebuild_table()` calling the same recompute on every poll refresh).
Clipboard content itself not separately asserted (`_copy_command_line()`
is a one-line `QApplication.clipboard().setText()` call on the same text
already verified correct).

---

### TC_AdminGui_009_paste_command_line_to_fill

**TestSets:** [AdminGui]

**Preconditions:**
- New Instance dialog open

**TestSteps:**
1. Paste a full `BOAT_CAN_INTERFACES=... BOAT_NODE_PLUGINS=...
   ./boat_gateway` line (two interfaces, a port, two plugins -- one with a
   config, one without) into **From command line**, click **Parse && Fill**
2. Submit; inspect the created instance

**Expected:**
- Every field (CAN interfaces, port, both plugins with correct configs,
  gateway binary) matches the pasted line exactly
- The created instance's own "Equivalent command line" matches what was
  originally pasted (paste → parse → create → format round-trips)

**Verdict:** OK

**Result:**
Verified on real hardware: pasted a line with `vcan0,vcan1`, port `50078`,
and two plugins; `can_picker.values()`, `port_edit.text()`, and
`plugin_picker.values()` all matched exactly after **Parse && Fill**.
Created the instance from the parsed payload and confirmed
`_format_command_line()` on the server's response still contained
`BOAT_CAN_INTERFACES=vcan0,vcan1` and `BOAT_GRPC_PORT=50078` -- full
round trip.

---

### TC_AdminGui_010_managed_column_and_external_guard

**TestSets:** [AdminGui]

**Preconditions:**
- An agent-managed instance running, and a `boat_gateway` started manually
  (not via the agent) on the same host

**TestSteps:**
1. Observe the table's **Managed** column for both rows
2. Select the externally-started row; click **Edit…**, then **Start**,
   then **Delete**
3. With the same row still selected, click **Stop**

**Expected:**
- Agent-managed row shows `Managed: Yes`; the externally-started row shows
  `Managed: No` with its real port/interfaces/plugins still populated
  correctly
- Step 2's three actions each short-circuit client-side with a clear
  message (no network round trip needed) -- "wasn't started by this
  agent ... Stop still works"
- Step 3 actually stops the manually-started process

**Verdict:** OK

**Result:**
Verified end-to-end on real hardware with a screenshot: table showed
`managed-two` (`Managed: Yes`, `vcan1`) and an externally-started gateway
(`Managed: No`, `id: external:<pid>`, real `can0, vcan0` interfaces) side
by side. Selecting the external row and calling
`edit_selected()`/`start_selected()`/`delete_selected()` each produced the
guard message (captured via a monkey-patched `QMessageBox.information`,
three calls, all containing "wasn't started by this agent") without any
of them reaching the network. Calling `stop_selected()` on that same row
genuinely terminated the manually-started process, confirmed via
`Popen.wait()` returning within the timeout. Log panel for the selected
external row also correctly showed the server's friendly
"log not captured" message rather than an error.

---

### TC_AdminGui_011_selection_cleared_when_selected_row_vanishes

**TestSets:** [AdminGui], [Error]

**Preconditions:**
- Two rows in the table, e.g. one agent-managed (A) and one external (B)

**TestSteps:**
1. Select B (a real click, i.e. `table.selectRow()` with signals live)
2. Cause B to disappear from the next snapshot (stop it, or otherwise have
   it drop out) without changing the table selection in between
3. Trigger a table rebuild (next poll cycle); inspect `_selected` and
   `table.selectedItems()`
4. Click the remaining row (A); check `_selected`, then trigger an action
   (e.g. Stop) and confirm it targets A's real id

**Expected:**
- After step 3: `_selected` is `None` and no row is shown visually
  selected -- a vanished selection must never silently persist as a stale
  id, and the UI must not show a row highlighted that doesn't correspond
  to a tracked selection
- After step 4: `_selected` matches A's actual id, and any action reads
  that real id -- never B's

**Verdict:** OK

**Result:**
Found by the user's own manual testing, not a scripted check: create+start
a managed instance, start an external one, stop the external one via the
GUI (worked), then select what looked like the remaining row and click
Stop -- got a "Stop failed" error naming the *previous* (already-stopped)
external instance's pid, not the one actually selected on screen.

Root cause: `rebuild_table()` repopulates rows inside
`table.blockSignals(True)`. When the previously-selected id isn't in the
new snapshot, the old code left `self._selected` untouched and never told
Qt's selection model anything changed -- whatever row *index* was
previously highlighted stayed highlighted with new data now underneath it,
while `_selected` (what every action button actually reads) kept pointing
at the vanished id. A real hazard, not just cosmetic: in a bigger table
that stale id could coincidentally still resolve to a *different, still-
live* instance, and an action would silently hit the wrong one with no
error.

Fixed by explicitly clearing both `table.clearSelection()` and
`self._selected` when the previously-selected row isn't found during a
rebuild. Verified on real hardware two ways: (1) reproduced the exact bug
by temporarily reverting just this fix and re-running the deterministic
test below -- it correctly *failed* (`_selected` stayed stale, a row
stayed visually highlighted) -- then restored the fix and the same test
passed; (2) the passing deterministic test itself: hand-crafted snapshots
fed straight into `rebuild_table()` (no real subprocess/network timing
involved -- a live-process version of this test hit repeated flakiness
from SSH/agent round-trip latency in this environment) confirmed all four
steps above.

---

### TC_AdminGui_012_save_and_load_session

**TestSets:** [AdminGui]

**Preconditions:**
- Two agent-managed instances running (one with a plugin config, an eth
  interface, and an explicit gRPC port) plus one externally-started
  process on the same host; one node defined, targeting one of the
  instances; one test run defined (see the 2026-08-18 update below)

**TestSteps:**
1. **Save Session…** to a file; inspect its contents
2. Stop+delete both managed instances, the node, the test run, and kill
   the external process (wipe the host clean)
3. In a **fresh** app instance with no hosts configured, **Load
   Session…** that file
4. Inspect the resulting hosts, instances, nodes, and test runs

**Expected:**
- Step 1's file contains exactly the two agent-managed instances with
  every field (interfaces, plugin path+config, port, gateway binary)
  matching what was actually running, the node with every field (script
  path, target host, extra args) matching, and the test run with every
  field (manifest path, environment config path, extra args) matching;
  the external process is absent
- Step 3/4: the host is added, and both instances plus the node and the
  test run are defined again with fields matching the saved definitions
  exactly, under **new** ids -- but left **stopped** (unlike
  `docker-compose up`, Load Session does not start anything automatically
  -- confirmed per user request after the first pass of this feature
  auto-started them)

**Verdict:** OK

**Result:**
Verified end-to-end on real hardware (`agn-testcomputer`) with a full
round trip: created `session-inst-1` (`can_ifaces: [vcan0]`,
`eth_ifaces: [veth0]`, `can_tp.so` with `{"iface": "vcan0"}`,
`grpc_port: 50061`) and `session-inst-2` (`can_ifaces: [vcan1]`,
`grpc_port: 50062`), plus an external process. `save_session()`'s output
YAML contained exactly those two instances with every field matching, and
no trace of the external one. Wiped the agent completely (stop+delete
both, kill the external process, confirmed `GET /api/instances` empty).
Constructed a **brand new** `MainWindow`/`HostStore` with zero hosts
(simulating a different machine) and called `load_session()` on the saved
file: zero errors, host added correctly, both instances recreated with
`can_ifaces`/`eth_ifaces`/`node_plugins`/`grpc_port` all matching the
original definitions exactly under **different** ids than the originals
(recreated, not resumed) -- and, after the auto-start behavior was
explicitly changed per user request, re-verified separately that a loaded
instance's `status` is `"stopped"` with `pid: null` immediately after
`load_session()` returns, not automatically running.

**Update (2026-08-17):** extended to cover the Nodes tab per user request
("include the nodes in the save/load session"). Verified two ways on real
hardware (`agn-testcomputer`), the user's own live session confirmed
untouched throughout: (1) headless, calling `session.py` directly against
a live agent -- created one instance and one node
(`script_path=can_tp_trigger_sender.py`, `target_host=localhost:50051`,
`extra_args=[--iface, vcan0]`), saved, deleted both originals, reloaded --
both recreated with every field matching exactly, `status: "stopped"`; (2)
through real Qt code (`QT_QPA_PLATFORM=offscreen`) driving
`MainWindow.save_session()`/`load_session()` with `QFileDialog`'s static
methods monkeypatched to a fixed path so the actual button-click call
path runs unattended -- identical result, plus confirmed the confirmation
dialog reports both counts ("1 instance(s) and 1 node(s) created"). A
leftover duplicate `HostStore` entry from earlier testing this session
(two names pointing at the same physical agent) caused a confusing first
pass with doubled counts on both instances and nodes -- not a bug in this
feature, correct behavior for two distinct host entries that both happen
to alias the same agent; cleaned up and re-verified cleanly with one host
entry.

**Update (2026-08-18):** extended to cover the Test Runs tab per user
request ("Now wire it in so the tests can also be saved and loaded").
`session.py`'s `load_session()` return tuple grew a `test_runs_created`
count (a breaking signature change, its one call site in
`MainWindow.load_session()` updated to match). Verified two ways on real
hardware (`agn-testcomputer`), on an **isolated test agent (port 8097)**
-- deliberately not the usual 8090, since the user's own live agent was
already running there; confirmed via `ps`/`ss` before starting anything,
and confirmed untouched throughout: (1) headless, calling `session.py`
directly -- created one instance, one node, and one test run
(`manifest_path=config/tests/manifest_can_loopback.json`,
`env_config_path=config/tests/env_can_loopback.json`,
`extra_args=[--verbose]`), saved, wiped all three, reloaded -- all three
recreated with every field matching exactly, `test_runs_created == 1`,
`status: "stopped"`; (2) through real Qt code (`QT_QPA_PLATFORM=offscreen`)
driving the real `MainWindow.save_session()`/`load_session()` handlers
(`QFileDialog` statics monkeypatched to a fixed path). The real,
unmodified save correctly also captured the user's own live host's own
real definitions (including their own pre-existing test run) in the same
file, since `save_session()` saves every configured host and that step is
read-only; before exercising the real `load_session()`, the saved YAML
was trimmed to just this test's isolated host entry first (so the load
call -- itself untouched -- couldn't try to recreate the user's own
managed definitions a second time against their live agent). Confirmed
the captured info-dialog text read exactly "Session loaded: 0 new
host(s) added, 1 instance(s), 1 node(s), and 1 test run(s) created", all
three recreated under fresh ids, and the reloaded run visible in the real
`test_run_table` widget. One real hang caught and fixed in the driver
script (not a product bug): `save_session()`/`load_session()` end with a
modal `QMessageBox.information()`/`.warning()` whose `.exec()` genuinely
blocks the calling thread until dismissed -- true even under
`QT_QPA_PLATFORM=offscreen` -- so with no user to click OK the first
attempt hung indefinitely; fixed by monkeypatching `QMessageBox.
information`/`.warning` to capture the message text instead of showing
it. All test artifacts (instances, nodes, test runs, the isolated agent
process, its host-store entry, session files) cleaned up afterward. Full
account: `backlog/test_runner_backlog.md`'s "wired test runs into
Save/Load Session" entry.

---

### TC_AdminGui_013_nodes_tab

**TestSets:** [AdminGui]

**Preconditions:**
- A reachable agent with discoverable node scripts and a running gateway
  to target

**TestSteps:**
1. Switch to the **Nodes** tab; open **New Node…**, inspect the Script
   dropdown and its docstring label
2. Fill Name/Target host/Extra args, submit; select the created row
3. Inspect the "Equivalent command line" panel; **Start**
4. Confirm the node is genuinely functioning (not just "a process exists")
5. **Edit…** while running (expect refusal); **Stop**; **Edit…** while
   stopped (rename); confirm the change

**Expected:**
- Step 1: dropdown populated from `GET /api/node-scripts`; selecting an
  entry shows its module docstring
- Step 3: command-line panel reads `BOAT_HOST=<target> python3 <script>
  <extra args>` exactly
- Step 4: the started node has a real, observable effect on its target
  gateway's bus
- Step 5: edit-while-running refused via the agent's 409 (no client-side
  guard blocks opening the dialog for nodes, unlike external gateway rows
  -- the refusal surfaces after submit); edit-while-stopped applies
  correctly

**Verdict:** OK

**Result:**
Verified end-to-end on real hardware with real screenshots. New Node
dialog's script dropdown listed `can_request_responder`/`cyclic_can_sender`;
selecting `can_request_responder` showed its exact docstring first line.
Filled `name=gui-responder`, `target_host=localhost:50057`, `extra_args`
via the dialog's own fields, submitted through `result_payload()` →
`create_node()`. Selected the new row via a real `node_table.selectRow()`
click; `_selected_node` matched; the command-line panel read exactly
`BOAT_HOST=localhost:50057 python3 .../can_request_responder.py --iface
vcan0 --request-id 0x7E0 --response-id 0x7E8 --response-data 5001`.
`start_node_selected()` started it; sending a CAN request to the target
gateway produced the correct reply on the wire (`candump`), confirming the
node was genuinely functioning, not merely "running". Calling
`update_node()` directly while running returned 409 (mentioning "running");
`stop_node_selected()` stopped it cleanly (`exit_code: 0`); the Edit
dialog, reopened on the now-stopped node, pre-filled every field correctly
(host combo disabled, name/target host/extra args all matching), and
submitting a rename via its own `result_payload()` applied correctly.
Screenshots confirmed both the Nodes tab (rich node row: script, target
host, extra args, live log showing the actual request/reply exchange) and
the Gateways tab (rich gateway row: multiple interfaces, two plugins) with
tab switching working and no regression to the existing Gateways tab.

---

### TC_AdminGui_014_node_target_gateway_dropdown_and_paste

**TestSets:** [AdminGui]

**Preconditions:**
- A reachable agent with a running and a stopped gateway instance defined

**TestSteps:**
1. Open **New Node…**; inspect the **Target gateway** dropdown
2. Pick a gateway from the dropdown; submit and inspect the payload
3. Type a bare port number into the same field; submit and inspect
4. Type a full `host:port`; submit and inspect
5. Paste a `BOAT_HOST=... python3 <script> <args>` line into **From
   command line**, click **Parse && Fill**; submit and inspect

**Expected:**
- Step 1: dropdown lists both instances as `<name> — localhost:<port>
  (<status>)`
- Step 2: payload's `target_host` is the plain `localhost:<port>`, not the
  display label
- Step 3: normalizes to `localhost:<port>`
- Step 4: passed through unchanged (not mistaken for a bare port)
- Step 5: Target gateway/Script/Extra args all populated correctly from
  the parsed line; submitting creates a node matching it exactly

**Verdict:** OK

**Result:**
Verified on real hardware. Dropdown listed both a `running` and a
`stopped` instance with correct labels. The **first** attempt at step 2
caught a real bug: `result_payload()` read `target_host_combo.
currentText()` directly, which for a picked item is the full label
(`"main — localhost:50051 (running)"`), not the stored address -- the
payload's `target_host` came back as that whole label. Fixed by only
trusting `currentData()` when the displayed text still matches the
selected index's own label (i.e. nothing was retyped after picking);
re-verified and the payload then correctly held plain `localhost:50051`.
Bare port `"50052"` normalized to `"localhost:50052"`; explicit
`"otherhost:50099"` passed through unchanged. `_parse_node_command_line()`
correctly handled both the full form (`BOAT_HOST=...` prefix + `python3` +
a quoted `"AA BB"` arg, which correctly stayed one token) and a bare
`<script> <args>` form with neither prefix present. Pasting a real
formatted command line and clicking **Parse && Fill** populated Target
gateway/Script/Extra args exactly; creating a node from that payload via
`create_node()` matched the original definition field-for-field.

---

### TC_AdminGui_015_node_target_gateway_spans_all_hosts

**TestSets:** [AdminGui]

**Preconditions:**
- Two configured hosts (agents), each addressed by a genuinely distinct,
  non-"localhost" string (real hostname/IP -- addressing one as
  `localhost:<port>` would create a degenerate case, see Result), each
  with a running gateway instance

**TestSteps:**
1. Open **New Node…** with Host set to the first agent; inspect the
   Target gateway dropdown
2. Switch Host to the second agent; inspect the dropdown again
3. Pick the cross-host entry; inspect `result_payload()`

**Expected:**
- Step 1: first agent's own gateway appears untagged, resolving to
  `localhost:<port>`; second agent's gateway appears tagged `[<name>]`,
  resolving to that agent's own real address (not `localhost`)
- Step 2: roles flip -- now the second agent's gateway is the untagged
  `localhost` entry, the first agent's is the tagged cross-host one
- Step 3: `target_host` in the payload is the real cross-host address

**Verdict:** OK

**Result:**
Verified on real hardware (`agn-testcomputer`): two agents on one physical
box, deliberately addressed via a real IP (`10.10.7.175`) and the real
hostname (`agn-testcomputer`) rather than `localhost`, specifically so the
test could tell "genuinely resolved cross-host address" apart from "just
happened to also say localhost." With Host = agent A: agent A's own
gateway showed as `on-A — localhost:50051 (running)` (untagged); agent B's
showed as `[host-B] on-B — agn-testcomputer:50052 (running)` (tagged, real
hostname, not `localhost`). Switching Host to agent B flipped it exactly:
agent B's gateway became the untagged `localhost:50052` entry, agent A's
became `[host-A] on-A — 10.10.7.175:50051` (its real IP). Picking that
cross-host entry produced `result_payload()["target_host"] ==
"10.10.7.175:50051"` exactly. Bonus observation, not a bug: each agent's
own external-gateway discovery also picked up the *other* agent's gateway
process as an `(unmanaged)` entry (expected, since `/proc` scanning isn't
scoped per-agent) -- these appeared in the dropdown too, which is correct:
a real running gateway is a valid target regardless of which agent (if
any) manages it.

---

### TC_AdminGui_016_node_dynamic_argument_fields

**TestSets:** [AdminGui]

**Preconditions:**
- A reachable agent whose `boat-platform/nodes/` scripts follow the
  `build_parser()` convention (`cyclic_can_sender.py`,
  `can_request_responder.py`)

**TestSteps:**
1. Open **New Node…**; select `cyclic_can_sender` in Script; inspect the
   **Script arguments** group
2. Fill some of the per-argument fields and check `--fd`; inspect
   `result_payload()["extra_args"]`
3. Create a node with `extra_args` mixing recognized flags (`--iface`,
   `--cycle-ms`, `--fd`) and one flag not in the script's schema
   (`--not-a-real-flag xyz`); reopen it via **Edit…**
4. Select a script with no discoverable `build_parser()` (or none at all)

**Expected:**
- Step 1: one field per declared argument (`--iface`, `--can-id`,
  `--data`, `--cycle-ms`, `--fd`, `--brs`) -- never `--address` (that's
  the Target gateway field). Text fields show `e.g. <default>` as a
  placeholder, falling back to the argument's help text when its default
  is empty (`--data`); `--fd`/`--brs` render as checkboxes
- Step 2: filled/checked fields appear in `extra_args` as `--flag value`
  / bare `--flag` pairs, ahead of whatever's in the flat Extra args field
- Step 3: `--iface`/`--cycle-ms`/`--fd` pre-fill into their matching
  fields; Extra args shows only `--not-a-real-flag xyz`
- Step 4: the Script arguments group is empty/hidden; Extra args remains
  the only way to pass anything -- no crash, no error

**Verdict:** OK

**Result:**
Verified on real hardware (`agn-testcomputer`) via two throwaway Qt
driver scripts run under a real Xvfb + `xcb` platform (not offscreen),
screenshotted with `QWidget.grab()`. Step 1: agent's `/api/node-scripts`
correctly introspected both real node scripts' `build_parser()` output
via `importlib`, e.g. `cyclic_can_sender`'s six non-`--address` arguments
with correct types/defaults (`--cycle-ms` default `1000` as an `int`,
`--fd`/`--brs` as `is_flag: true`); the dialog rendered one field per
argument, `--data`'s placeholder correctly fell back to its help text
("Payload as hex bytes, e.g. AABBCCDD (empty = 0-byte frame)") since its
default is `""`. Steps 3: created a node via `create_node()` with
`extra_args=["--iface","vcan1","--cycle-ms","250","--fd",
"--not-a-real-flag","xyz"]`, reopened it via `NewNodeDialog(existing=...)`
-- `_arg_widgets["--iface"].text() == "vcan1"`,
`_arg_widgets["--cycle-ms"].text() == "250"`,
`_arg_widgets["--fd"].isChecked() is True`,
`_arg_widgets["--brs"].isChecked() is False` (untouched), and
`extra_args_edit.text() == "--not-a-real-flag xyz"` -- exactly the
recognized/leftover split described in `admin_gui/README.md`. Screenshots
confirmed both dialogs visually match (see
`admin_gui/docs/new_node_dialog.png` for the New Node case). Step 4 not
separately screenshotted but covered by `_rebuild_arg_fields()`'s
`specs = specs or []` guard and `_introspect_node_args()`'s broad
`except Exception: return []` on the agent side, already exercised
in practice by every script that predates this feature.

---

### TC_AdminGui_017_plugin_config_schema_fields

**TestSets:** [AdminGui]

**Preconditions:**
- A reachable agent whose plugin `.so`s (`can_tp`, `tcp`, `probe`,
  `someip`) have been built with the `<name>.schema.json` sidecar
  convention (`cmake/BoAtPlugin.cmake`)

**TestSteps:**
1. Open **New Instance…**; select `tcp.so` in the plugin picker's combo;
   inspect the **Plugin config** group
2. Select `probe.so`; inspect its fields, specifically `mode` (has an
   `enum`) and `buses` (an `array`)
3. Fill some of `tcp.so`'s fields, leave others blank, click **+ Add**;
   inspect `plugin_picker.values()`
4. Select `pdu_router.so` (no schema)

**Expected:**
- Step 1: one field per key `tcp.so`'s schema declares (`iface`,
  `retry_ms`, `max_retries`, `mss`, `time_wait_ms`, `rx_window`, `nagle`,
  `keepalive_idle_ms`, `keepalive_interval_ms`, `keepalive_retry_count`),
  each with an `e.g. <default>` placeholder; `nagle` (`bool`) renders as a
  checkbox
- Step 2: `mode` renders as a dropdown of `passive`/`active`/`both`,
  pre-selected to its default (`both`); `buses` renders as a text field
  with a comma-separated example placeholder
- Step 3: the added entry's `config` dict contains exactly the filled
  fields, correctly typed (`retry_ms` a JSON integer, not a string;
  `nagle` a JSON boolean) -- blank fields are omitted, not sent as empty
  strings
- Step 4: the Plugin config group is empty/hidden -- the flat JSON config
  field remains the only way to configure it, unregressed

**Verdict:** OK

**Result:**
Verified on real hardware (`agn-testcomputer`) via a scratch agent
instance (isolated from the reporting user's own live agent/gateway
throughout, confirmed via `ps`/`ss` before and after) and a real Qt render
(Xvfb + `xcb`, screenshotted via `QWidget.grab()`, not offscreen). Step 1:
all ten `tcp.so` fields rendered with correct placeholders. Step 2:
`mode` rendered as a real `QComboBox` pre-selected to `"both"`; `buses`
showed `e.g. can`. Step 3: filling `iface="veth0"`, `retry_ms="500"`,
unchecking `nagle`, then **+ Add** produced exactly `{"iface": "veth0",
"nagle": false, "retry_ms": 500}` -- `retry_ms` a real JSON int. A first
attempt at this step caught a real bug: `add_current()` was calling the
same field-rebuild used on combo-selection-change defensively right
before reading values, which destroyed and recreated every widget first,
silently discarding whatever was just entered (`nagle` came back `true`
-- the schema's own default -- despite explicitly unchecking it, and
`iface`/`retry_ms` were missing entirely). Fixed by removing that call;
re-running this exact step afterward produced the correct dict. Step 4:
confirmed `pdu_router.so` (and every legacy `.so` with no current CMake
target) shows an empty/hidden Plugin config group, per `/api/host/info`
returning `"config_schema": {}` for them (`_introspect_plugin_config()`'s
missing-sidecar-file path). Full account in
`backlog/launcher_agent_backlog.md`'s "plugin config schema fields"
entry.

---

### TC_AdminGui_018_test_runs_tab

**TestSets:** [AdminGui]

**Preconditions:**
- A reachable agent with `boat` installed (`pip install -e
  ./boat-platform/cli`), a discoverable manifest/environment pair under
  `boat-platform/config/tests/` (`manifest_can_loopback.json` +
  `env_can_loopback.json`), and the hardware that manifest needs
  (physical `can0`/`can1` bridged at the transceiver level)

**TestSteps:**
1. Switch to the **Test Runs** tab; inspect the table's columns
2. Open **New Test Run…**; inspect the Manifest dropdown, then select
   `can-loopback-routing-suite` and inspect the Environment dropdown
3. Fill Name + Extra args (`--verbose`), submit; select the created row
4. Click **Start**; watch the table and log viewer until the run finishes
5. Inspect the Report directory field

**Expected:**
- Step 1: columns are Host, Name, ID, Manifest, Environment, Result,
  Status, PID, Uptime
- Step 2: Manifest dropdown populated from `GET /api/test-manifests`
  (shows test count); selecting the manifest auto-pre-selects its own
  declared `environment_config` in the Environment dropdown (still
  overridable) -- mirrors `boat test run --config`'s own override
  semantics
- Step 4: `Status` goes to `running` with a real PID, then back to
  `stopped`; `Result` becomes `PASS`; the log viewer shows the real `boat
  test run` output including the `--verbose` lines and the test case's
  own pass line
- Step 5: shows the run's real `report_dir`, relative to `boat-platform/`
  on the agent's host

**Verdict:** OK

**Result:**
Verified twice on real hardware (`agn-testcomputer`), on an isolated
scratch agent (port 8090) + Xvfb (`:44`), confirmed via `ps`/`ss` not to
disturb the user's own live agent/gateway throughout. First via raw
`curl` against the agent directly (manifest/environment discovery,
create, start, watched `status`→`stopped`/`result`→`PASS`, confirmed real
`report.json`/`report.junit.xml`/`report.html`/`stdout.txt` written to
disk, `extra_args` reaching the invocation, delete) before touching Qt at
all. Then through the actual Qt code path (`QT_QPA_PLATFORM=xcb`, a
throwaway driver script, not committed): constructed a real `MainWindow`,
confirmed the Test Runs tab's 9 columns exactly. Opened `NewTestRunDialog`
non-modally -- Manifest dropdown showed `can-loopback-routing-suite  (1
test(s))`; selecting it auto-selected `can-loopback-routing
(localhost:50067)` in the Environment dropdown, `currentData()` correctly
ending in `env_can_loopback.json`. Submitted via the same
`result_payload()` → `AgentClient.create_test_run()` call path
`new_test_run()` itself makes, selected the resulting row via a real
`test_run_table.selectRow()`, and called the real
`start_test_run_selected()` method. Polled (2s cadence, same
`PollWorker`) until the table showed `Result: PASS`/`Status: stopped`
(~3s later): real HIL log content in the viewer (`[test] Gateway at
localhost:50067`, `TC_CANLOOP_001: PASS (1334ms)`, `Results: 1/1 passed,
0 failed`), report-dir field showing `reports/admin_gui/<id>`.
Screenshots confirmed both the populated dialog (manifest/environment doc
labels rendering correctly underneath each dropdown) and the passing tab
row. All test artifacts (test runs, `reports/admin_gui/`, the scratch
agent process, Xvfb, the driver script) cleaned up afterward. One test-script
bug caught and fixed along the way, not a product bug: the driver's first
pass treated the run's initial (never-started) `status: "stopped"` as
"finished," racing ahead of the real completion -- fixed by waiting for a
non-`None` `result` instead of the `"stopped"` status text, since a
freshly-created run is also reported `"stopped"` before it's ever started.
Full account: `backlog/test_runner_backlog.md`'s "admin_gui Test Runs
tab" entry.

---

### TC_AdminGui_019_test_run_report_viewer

**TestSets:** [AdminGui]

**Preconditions:**
- A reachable agent with a finished test run (`Result: PASS` or `FAIL`)
  selected in the Test Runs table

**TestSteps:**
1. Click **View Report**; inspect the dialog before the run has been
   started (report_dir empty) and again after it finishes
2. Inspect the tree's rows and the detail pane for the selected test
3. Click **Refresh**

**Expected:**
- Before start: a clear "no report directory yet" message, no crash
- After finish: summary label shows `<report_dir> — N/M passed`; one tree
  row per manifest test entry (id, verdict -- color-coded PASS/FAIL/ERROR,
  duration, summary); selecting a row shows that test's description,
  verdict, duration, and which artifact files (`report.html`/`.junit.xml`/
  stdout/stderr) exist alongside it
- Refresh re-fetches without needing to reopen the dialog

**Verdict:** OK

**Result:**
Verified on real hardware (`agn-testcomputer`), on an isolated test agent
(port 8097, the user's own live agent confirmed on 8090 and never
touched), two ways. First via `curl` directly against
`GET /api/test-runs/{id}/report`: `{"exists": false, "tests": []}` before
starting (empty `report_dir`); a real parsed `report.json` after
finishing, with the environment snapshot, execution timing, and verdict
all present and correctly shaped; a 404 for an unknown run id. Then
through the real Qt code path (Xvfb + `xcb`, a throwaway driver script,
not committed): created and started a real test run through the actual
agent, constructed the real `TestReportDialog` class against it (same
class the **View Report** button opens), and confirmed
`summary_label.text()` read `reports/admin_gui/<id> — 1/1 passed`, the
tree held exactly one row (`TC_CANLOOP_001`, `PASS`, `1361ms`), and the
detail pane's text included the real test description, `Verdict: PASS`,
and `Also on disk in this folder (agent's host): report.html,
report.junit.xml, stdout.txt`. A screenshot confirmed the tree (PASS row
rendered in green) and detail pane visually. All test artifacts (test
run, `reports/admin_gui/`, the isolated agent process, Xvfb, driver
script) cleaned up afterward. Full account: `backlog/test_runner_backlog.md`'s
"test report content viewer" entry.

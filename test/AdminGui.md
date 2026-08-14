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
  process on the same host

**TestSteps:**
1. **Save Session…** to a file; inspect its contents
2. Stop+delete both managed instances and kill the external process (wipe
   the host clean)
3. In a **fresh** app instance with no hosts configured, **Load
   Session…** that file
4. Inspect the resulting hosts and instances

**Expected:**
- Step 1's file contains exactly the two agent-managed instances with
  every field (interfaces, plugin path+config, port, gateway binary)
  matching what was actually running; the external process is absent
- Step 3/4: the host is added, and both instances are defined again with
  fields matching the saved definitions exactly, under **new** ids -- but
  left **stopped** (unlike `docker-compose up`, Load Session does not
  start anything automatically -- confirmed per user request after the
  first pass of this feature auto-started them)

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

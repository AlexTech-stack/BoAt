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

---

### TC_AdminGui_004_multi_host_aggregation

**TestSets:** [AdminGui]

**Preconditions:**
- Two reachable agents on different hosts (or two agent processes on
  different ports on one host), each with at least one instance defined
- Since v0.2.0 an agent binds `127.0.0.1` by default: an agent on **another**
  host must be started with `BOAT_AGENT_BIND=0.0.0.0` (or its LAN address), or
  the GUI cannot reach it. Two agents on one host work without it.

**TestSteps:**
1. Add both hosts
2. Observe the aggregated instance table

**Expected:**
- Table shows instances from both hosts together, each row's Host column
  identifying which; Start/Stop/Delete on a row act against the correct
  host's agent

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

---

### TC_AdminGui_020_interfaces_tab

**TestSets:** [AdminGui]

**Preconditions:**
- A reachable agent with passwordless sudo for `ip`/`modprobe` (same
  prerequisite `ui/launcher.py`'s own interface endpoints already need)

**TestSteps:**
1. Switch to the **Interfaces** tab; inspect the table's columns and
   contents (should include real physical interfaces, e.g. `can0`, not
   just virtual ones)
2. **New veth…** with a name whose auto-derived `<name>_peer` would
   exceed 15 characters; inspect the live warning and attempt to submit
3. **New vcan…** with a valid name; select it; **Down**, then **Up**
4. **Configure CAN…** on that vcan with a bitrate
5. **Delete** the vcan; select a physical interface (e.g. `can0`) and
   attempt **Delete**
6. **New veth…** with a valid name; confirm both ends appear; **Delete**
   one end
7. On a real CAN FD interface already running (e.g. `can0` at
   500000/2000000 FD): **Down**, then **Configure CAN…**; inspect the
   dialog's pre-filled values and "Current: ..." label; change the
   bitrate and uncheck CAN FD; submit; inspect the interface's real state
   and up/down state afterward

**Expected:**
- Step 1: columns are Host, Name, Type, Up, Operstate, MAC; physical
  hardware appears read-only alongside virtual interfaces
- Step 2: a red warning shows the actual peer name and character count
  before submit; submitting is rejected client-side with a clear message
  (no network round trip)
- Step 3: the vcan appears in the table (`up`); Down/Up toggle the Up
  column correctly on the next poll
- Step 4: a vcan has no real bitrate -- the kernel rejects the change and
  the dialog surfaces a clear error, not a crash
- Step 5: the vcan deletes and disappears from the table; deleting the
  physical interface is refused client-side ("only vcan/veth interfaces
  created by this tool can be deleted here"), no network round trip, and
  the interface is untouched
- Step 6: both veth ends appear as separate rows; deleting either end
  removes both
- Step 7: the dialog shows the interface's *actual* current
  bitrate/FD/data-bitrate, not fixed placeholders; after submitting,
  `ip -d -j link show` confirms the new bitrate applied, CAN FD genuinely
  disabled (no stale data-bitrate left behind), and the interface left in
  the same up/down state it was in before the dialog was opened (down, in
  this case, since Down was pressed first)

---

### TC_AdminGui_021_dark_theme_sidebar_redesign

**TestSets:** [AdminGui]

**Preconditions:**
- None specific -- a visual/layout check, works with any host configuration

**TestSteps:**
1. Launch the app; inspect the sidebar (icons, app title, selected-item
   highlight) and confirm five pages: Gateway, Nodes, Test Runs,
   Interfaces, Settings
2. Click through each page; inspect table styling (rounded card,
   alternating rows), button colors (Start/OK blue, Stop/Down/Delete red),
   and colorized status cells (Managed Yes green, running/PASS green,
   exited nonzero red)
3. Open a dialog (e.g. New Instance…) and inspect its styling
4. On Settings, confirm host management (Add/Remove Host, Save/Load
   Session) is present and functions the same as before the redesign

**Expected:**
- Sidebar renders with legible icons (no missing-glyph boxes), the
  active page highlighted with a rounded pill, dark theme consistent
  across the whole window
- Every page's table/buttons/status cells follow the dark theme and
  color conventions described above
- Dialogs match the dark theme (no jarring light-mode popup on a dark
  main window)
- Settings' host management works identically to the old top-bar version
  it replaced -- moving it didn't change its behavior, only its location

---

### TC_AdminGui_022_can_config_column

**TestSets:** [AdminGui]

**Preconditions:**
- A reachable agent with at least one vcan and, ideally, a real
  already-configured physical CAN interface

**TestSteps:**
1. Inspect the Interfaces table's CAN Config column across every
   interface type present (vcan, real CAN, veth, Ethernet, loopback)
2. Hover a real CAN interface's CAN Config cell
3. Select a vcan row and click **Configure CAN…**
4. Select a real `can`-type row and click **Configure CAN…**

**Expected:**
- Step 1: vcan rows read `virtual`; a real, already-configured CAN link
  reads `<bitrate> bps, <sample point>% SP` (plus ` / FD <data bitrate>
  bps, <sample point>% SP` if CAN FD is active); anything else
  (veth/ether/loopback, or an unconfigured CAN link) reads `—`
- Step 2: tooltip shows the fuller detail (`prop_seg`/`phase_seg1`/
  `phase_seg2`/`sjw`) for each phase
- Step 3: a clear message ("has no real bitrate or CAN FD configuration
  to set") appears instead of the bitrate-editing dialog -- no dialog
  with fake 500000/no-FD defaults opens
- Step 4: the real Configure CAN dialog opens as before, pre-filled from
  the interface's actual current state

---

### TC_AdminGui_023_node_tick_fields

**TestSets:** [AdminGui]

**Preconditions:**
- A reachable agent

**TestSteps:**
1. Open **New Instance…**; inspect the Node tick (ms)/(µs) fields and
   the note beneath them
2. Fill in a CAN interface and create the instance
3. **Edit…** the created instance; inspect the tick fields
4. Change **Node tick (µs)** and save
5. Paste a command line containing `BOAT_NODE_TICK_MS=<n>` (no `_US`)
   into **From command line** and click **Parse && Fill**

**Expected:**
- Step 1: **Node tick (ms)** starts pre-filled with `1`; **Node tick
  (µs)** is blank with a "leave blank unless you need sub-ms precision"
  placeholder; the note states `BOAT_NODE_TICK_US` overrides
  `BOAT_NODE_TICK_MS` when both are set
- Step 2: the created instance's stored `tick_ms` is `1`, `tick_us` is
  `None`
- Step 3: the dialog re-opens with `1`/blank pre-filled from the saved
  instance
- Step 4: the update persists the new `tick_us` value
- Step 5: **Node tick (ms)** updates to the pasted value, **Node tick
  (µs)** stays blank

---

### TC_AdminGui_024_adopt_external_gateway

**TestSets:** [AdminGui], [LauncherAgent]

**Preconditions:**
- An agent added as a host; a hand-started gateway visible as an unmanaged (`external:`) row

**TestSteps:**
1. Select the unmanaged row; click **Adopt…**
2. Confirm the dialog, entering a name
3. Stop, then Start the row

**Expected:**
- Step 1: the dialog appears only for an external row (Adopt is not offered for managed rows)
- Step 2: the row turns managed, keeps its pid/port, and the gateway keeps running
- Step 3: behaves as TC_LauncherAgent_011 step 4; an agent error is shown in an
  "Adopt failed" message box, not swallowed

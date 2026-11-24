# TestSet: WebUIs

System-level tests for the gateway-integrated web UIs (started by `start_ui.sh`):
Launcher (8086), Dashboard (8080), Nodes/Control Panel (8081), Commander (8082),
Recorder (8083), plus the on-demand Debug inspector (8084).
Recorder-specific recording cases live in [Recording].

Common precondition: gateway running with `BOAT_CAN_INTERFACES=vcan0`; UIs started
via `./start_ui.sh`.

---

### TC_WebUIs_001_all_uis_reachable

**TestSets:** [WebUIs]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. Open ports 8086, 8080, 8081, 8082, 8083 in a browser (or `curl -s` each)

**Expected:**
- Every UI serves its page (HTTP 200, BoAt-styled page with the ⛵ header)

---

### TC_WebUIs_002_shared_navigation

**TestSets:** [WebUIs]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. On each gateway UI, inspect the top navigation bar
2. Click through the links

**Expected:**
- The nav lists exactly the five gateway UIs (Launcher, Dashboard, Nodes, Commander,
  Recorder) — standalone tools are NOT mixed in; the current page is highlighted;
  links resolve using the browser's current hostname

---

### TC_WebUIs_003_launcher_interface_creation

**TestSets:** [WebUIs], [Gateway]

**Preconditions:**
- Passwordless sudo for `modprobe`/`ip link` configured; `vcan7` does not exist

**TestSteps:**
1. In the Launcher (8086), create a new vcan interface `vcan7`
2. `ip link show vcan7` in a shell

**Expected:**
- The interface exists and is up; the Launcher lists it

---

### TC_WebUIs_004_launcher_gateway_lifecycle

**TestSets:** [WebUIs], [Gateway]

**Preconditions:**
- No gateway currently running; gateway binary built

**TestSteps:**
1. In the Launcher, start the gateway with `vcan0` selected
2. Observe the live log pane; run `boat frame list-ifaces`
3. Stop the gateway from the Launcher

**Expected:**
- Gateway starts, log lines stream into the UI, gRPC answers; stop terminates the
  process and the UI reflects the stopped state with exit code

---

### TC_WebUIs_005_dashboard_live_frames

**TestSets:** [WebUIs], [CAN]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. Open the Dashboard (8080)
2. `cansend vcan0 123#AABBCCDD`

**Expected:**
- The frame appears in the live CAN trace within ~1 s with correct ID/data; event
  log and bus-signal panes update when corresponding traffic exists

---

### TC_WebUIs_006_nodes_start_stop

**TestSets:** [WebUIs]

**Preconditions:**
- At least one non-interactive node script under `boat-platform/nodes/`
  (`cyclic_can_sender.py` and `can_request_responder.py`, added specifically
  to satisfy this precondition -- see `backlog/nodes_backlog.md`)

**TestSteps:**
1. Open Nodes (8081); start a node; observe its rolling log
2. Stop the node

**Expected:**
- Node subprocess starts (traffic/log visible), log streams into the UI, stop
  terminates it and shows the exit code; interactive nodes are marked not runnable

---

### TC_WebUIs_007_commander_raw_send

**TestSets:** [WebUIs], [CAN]

**Preconditions:**
- `candump vcan0` running

**TestSteps:**
1. In the Commander (8082), compose and send a raw CAN frame (ID 0x321, data 0102)

**Expected:**
- The frame appears on the bus exactly as composed

---

### TC_WebUIs_008_commander_pdu_composed_send

**TestSets:** [WebUIs], [PDU]

**Preconditions:**
- A PDU database with scaled signals loaded in the Commander

**TestSteps:**
1. Select a message, set signal values in engineering units, send
2. Decode the on-bus frame against the database definition

**Expected:**
- Signal packing (start bit, length, byte order, factor/offset) matches the database
  — same packing rules as TC_PDU_006

---

### TC_WebUIs_009_debug_grpc_inspector

**TestSets:** [WebUIs]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. Start `python3 ui/debug.py`, open port 8084
2. Run any CLI command (e.g. `boat frame list-ifaces`)

**Expected:**
- The inspector shows the RPC (method name, caller ip:port, lifecycle events,
  message sizes, duration, status code) in near-real time

---

### TC_WebUIs_010_ui_behavior_gateway_down

**TestSets:** [WebUIs], [Error]

**Preconditions:**
- UIs running; gateway stopped

**TestSteps:**
1. Open the Dashboard and Commander; attempt an action that needs the gateway

**Expected:**
- The UIs stay up and clearly indicate the gateway is unreachable (status badge /
  error toast) — no unhandled exceptions, no blank pages; they recover automatically
  once the gateway is back

---

### TC_WebUIs_011_node_gateway_restart_resilience

**TestSets:** [WebUIs], [Error]

**Preconditions:**
- A gateway running with `BOAT_CAN_INTERFACES=vcan0`; both `cyclic_can_sender.py`
  and `can_request_responder.py` started against it (`FrameNode`-based nodes)

**TestSteps:**
1. Confirm both nodes are genuinely functioning (cyclic traffic on the wire;
   `cansend` a request, confirm the responder's reply) via `candump`
2. Stop the gateway; observe both node processes and their logs
3. Restart the gateway (same port); without touching the node processes, repeat
   step 1's checks

**Expected:**
- Step 2: neither node process crashes or exits; each logs its own failure
  (`send failed ... will retry next cycle` / `subscribe stream failed ...
  reconnecting in Ns...`) instead of going silent or exiting
- Step 3: both nodes resume working entirely on their own — no manual
  stop/start of either node process — within a few seconds of the gateway
  becoming reachable again

---

### TC_WebUIs_012_plugin_based_nodes

**TestSets:** [WebUIs], [PDU], [CanTp]

**Preconditions:**
- A gateway running with `BOAT_CAN_INTERFACES=vcan0` and
  `BOAT_NODE_PLUGINS=<path>/pdu_router.so,<path>/can_tp.so?{"iface":"vcan0"}`

**TestSteps:**
1. Start `pdu_cyclic_publisher.py` against it; observe the wire via `candump`
2. Start `can_tp_trigger_sender.py` against it; send a short trigger frame
   (payload byte <= 7) via `cansend`
3. Send a trigger requesting > 7 bytes, then hand-send a Flow Control frame
   via `cansend` on the node's `--target-addr`, forcing real
   First-Frame/Flow-Control/Consecutive-Frame segmentation
4. Send an empty-payload trigger frame; send a trigger with payload byte `00`
5. Start both nodes against a gateway *without* either plugin loaded
6. Kill and restart the gateway (plugins loaded again), then send a single
   trigger with no prior "warm-up" trigger

**Expected:**
- Step 1: the configured PDU ID appears on the wire as a CAN frame with the
  configured payload, on the configured cycle
- Step 2: a correctly-formed Single Frame goes out on `--source-addr` with
  the requested number of incrementing bytes (`00 01 02 ...`)
- Step 3: a First Frame goes out with the correct total length; after Flow
  Control, the correct number of Consecutive Frames follow with the
  incrementing sequence continuing correctly across frame boundaries, no
  gaps/duplicates
- Step 4: an empty payload falls back to `--default-length`; a `00` payload
  byte sends nothing and logs a clear "0 bytes -- nothing to send" line
  instead of attempting a degenerate send
- Step 5: `configure()`/`configure_route()` fails cleanly with a clear
  "is the plugin loaded?" message -- no crash, no silent no-op
- Step 6: the very first trigger after the gateway comes back still
  succeeds -- the node detects the stale "configured" state from the
  `FAILED_PRECONDITION` the plugin returns (its N-SDU session was wiped by
  the restart), reconfigures, and retries that same trigger once, rather
  than silently dropping it

---

### TC_WebUIs_013_loopback_bind_by_default

**TestSets:** [WebUIs], [Error]

**Preconditions:**
- None of the UI ports in use

**TestSteps:**
1. `./start_ui.sh` and `./start_tools.sh`; `ss -ltn` for ports 8080-8089
2. Restart one service with its bind override, e.g. `BOAT_DASH_BIND=0.0.0.0 python3 ui/dashboard.py`
3. Request a path outside the allowed roots, e.g. `../../etc/passwd` and `/etc/passwd`, in:
   the PDU Editor's load and save, the Trace Editor's load/save/export, a launcher or commander
   PDU-database import, and a Recorder download (also try a sibling such as `traces_evil/x`)

**Expected:**
- Step 1: every service listens on `127.0.0.1` only (#96)
- Step 2: that service listens on `0.0.0.0`; the others stay on loopback
- Step 3: each is rejected with a 4xx and a readable message; nothing outside the config or
  trace root is read or written. (The Trace and Eth Analyzers are not confined -- they only
  read, and only files with a trace suffix; they rely on the loopback bind.)

---

### TC_WebUIs_014_dashboard_metrics_panel

**TestSets:** [WebUIs]

**Preconditions:**
- Gateway with vcan0; dashboard running

**TestSteps:**
1. Open the dashboard; find the metrics panel
2. Run `cangen vcan0 -g 1 -n 2000` (or a replay) and watch the panel
3. Stop the gateway briefly and start it again

**Expected:**
- Step 1: the latest value per series from `MetricsService.StreamMetrics` -- `can.vcan0.*`,
  `grpc.*.dropped`, `event_bus.dropped`, `event_store.dropped`/`write_errors` -- with the CAN
  series highlighted (#28)
- Any non-zero drop counter is visibly highlighted
- Step 3: the panel reconnects without a page reload

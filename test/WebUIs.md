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

**Verdict:** NOT_TESTED

**Result:**

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

**Verdict:** NOT_TESTED

**Result:**

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

**Verdict:** NOT_TESTED

**Result:**

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

**Verdict:** NOT_TESTED

**Result:**

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

**Verdict:** NOT_TESTED

**Result:**

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

**Verdict:** OK

**Result:**
Verified on real hardware (`agn-testcomputer`) via the actual API calls the
web UI's own JS makes (equivalent to using the page): `GET /api/nodes`
correctly discovered both `cyclic_can_sender` and `can_request_responder`
(neither interactive -- no `input()` in either), with their module
docstrings shown. `POST /api/nodes/can_request_responder/start?address=
localhost:50055` started it; confirmed it was genuinely functioning (not
just "a process exists") by sending a real CAN request and capturing the
reply on the wire via `candump`: `0x7E0 [22 F1 90]` → `0x7E8 [50 01]`,
<1ms turnaround. `POST .../stop` cleanly terminated it; a subsequent
`GET /api/nodes` showed `status: "stopped"`. `GET /api/nodes/.../log`
showed the `[control-panel] started PID ...` line as expected.

**Update (2026-08-13):** user reported `cyclic_can_sender.py` running
consistently 3-4ms late per cycle (a 300ms-configured cycle measuring
303-304ms via `candump -t d`). Root cause: the script's deadline was
computed *after* `send_can()` (a synchronous gRPC call, a few ms even over
loopback) returned, silently adding that call's own duration on top of
every cycle -- the same class of bug the CanTp plugin's TX thread had
before an earlier fix in this session moved its deadline computation to
before the send. Fixed the same way here (deadline relative to cycle
start, not post-send), plus tightened the wait loop to sleep the precise
remaining time near the deadline instead of always rounding up to a full
50ms poll chunk. Re-verified on real hardware: before the fix, 12
consecutive cycles measured 302.96-304.49ms (mean 303.94ms) against a
300ms-configured cycle; after, 11 steady-state cycles measured
299.98-300.33ms (mean ~300.14ms, within 0.05% of the target -- the one
low outlier, 292ms on the very first cycle, is one-time gRPC channel/stub
warm-up, not a steady-state issue). Not marking this TestCase's own
verdict down -- the start/stop/discovery behavior it actually covers was
never wrong -- see `backlog/nodes_backlog.md` for the full account.

---

### TC_WebUIs_007_commander_raw_send

**TestSets:** [WebUIs], [CAN]

**Preconditions:**
- `candump vcan0` running

**TestSteps:**
1. In the Commander (8082), compose and send a raw CAN frame (ID 0x321, data 0102)

**Expected:**
- The frame appears on the bus exactly as composed

**Verdict:** NOT_TESTED

**Result:**

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

**Verdict:** NOT_TESTED

**Result:**

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

**Verdict:** NOT_TESTED

**Result:**

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

**Verdict:** NOT_TESTED

**Result:**

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

**Verdict:** OK

**Result:**
Verified on real hardware (`agn-testcomputer`), on an isolated test gateway/
`vcan0` pair (the user's own live 50056/`vcan1` session was confirmed
untouched throughout via `ps`/`ss`). This TestCase exists because of a real
bug found by the user running exactly this scenario through admin_gui: on
stopping the gateway, `cyclic_can_sender.py` crashed outright (uncaught
exception from `send_can()`) while `can_request_responder.py` stayed alive
but went silently idle forever (a bare `except: pass` around
`FrameNode.subscribe()`'s stream loop swallowed the disconnect, and nothing
ever told the main thread the subscription had died) — needing a manual
stop+start to work again after the gateway came back. Root-caused and fixed
in the SDK (`sdk/python/boat/frame_node.py`): `subscribe()` now retries with
capped exponential backoff instead of dying silently, `send()`/`send_can()`
raise instead of hanging (matched by a `try/except` added to
`cyclic_can_sender.py`'s send loop so it retries next cycle instead of
crashing), and — found only by actually testing recovery time, not just
"does it crash" — `FrameNode._reconnect()` discards and recreates the gRPC
channel on every retry, since retrying against the *same* channel left both
nodes stuck 90+ seconds after the gateway was already confirmed reachable
again (grpc-python's own per-channel reconnect backoff, independent of
anything this SDK's retry loop does). With all three pieces in place: killed
the test gateway, confirmed via log tail that neither node crashed and both
logged their own retry/backoff messages; restarted the gateway on the same
port (after an unrelated `TIME-WAIT` delay — see
`backlog/gateway_backlog.md`'s "Restarting a gateway on the same port" entry,
a separate finding, not this bug); `candump` showed the cyclic sender's
300ms-cycle traffic resume within seconds with zero manual action, and a
fresh `cansend vcan0 7E0#22F19000` got the expected `7E8 [50 01]` reply back
in ~3.6ms from the still-running responder process. Full account in
`backlog/nodes_backlog.md`'s "gateway restart left nodes stuck" entry.

---

### TC_WebUIs_012_plugin_based_nodes

**TestSets:** [WebUIs], [PDU], [CanTp]

**Preconditions:**
- A gateway running with `BOAT_CAN_INTERFACES=vcan0` and
  `BOAT_NODE_PLUGINS=<path>/pdu_router.so,<path>/can_tp.so?{"iface":"vcan0"}`

**TestSteps:**
1. Start `pdu_cyclic_publisher.py` against it; observe the wire via `candump`
2. Start `can_tp_echo_responder.py` against it; send a short (<=7 byte)
   single-frame ISO-TP request via `cansend`, observe the reply
3. Send a >7 byte request via `isotpsend -D <len>` (or equivalent), forcing
   real First-Frame/Flow-Control/Consecutive-Frame segmentation, and observe
   the plugin's reassembly and re-segmented echo
4. Start both nodes against a gateway *without* either plugin loaded

**Expected:**
- Step 1: the configured PDU ID appears on the wire as a CAN frame with the
  configured payload, on the configured cycle
- Step 2: the request is echoed back correctly on the reverse addressing
- Step 3: the request reassembles correctly server-side (First Frame length
  matches, Consecutive Frame data matches byte-for-byte), and the echo starts
  going back out correctly segmented (matching First Frame)
- Step 4: `configure()`/`configure_route()` fails cleanly with a clear
  "is the plugin loaded?" message -- no crash, no silent no-op

**Verdict:** OK

**Result:**
Verified on real hardware (`agn-testcomputer`) on an isolated test
gateway/`vcan0` pair with both plugins loaded, the user's own live session
confirmed untouched throughout. Step 1: `candump` showed CAN ID `0x100`
with payload `01 02 03 04 05 06 07 08` on a 300ms cycle as configured.
Step 2: `cansend vcan0 7E0#03112233` came back as
`7E8#03112233CCCCCCCC` (`CC` = default pad byte) in ~4ms. Step 3:
`isotpsend -s 7E0 -d 7E8 -D 20 vcan0` produced the full expected wire
exchange -- First Frame (`10 14 01 02 03 04 05 06`, length 0x14=20),
Flow Control (`30 00 00 ...`), two Consecutive Frames reconstructing the
full 20-byte payload -- and the plugin's echo correctly began with a
matching First Frame (`10 14 01 02 03 04 05 06`) before the capture
window ended. Step 4 verified both nodes against a gateway with neither plugin loaded:
`pdu_cyclic_publisher.py`'s `configure_route()` (`PduNode`, which catches
`grpc.RpcError` internally) returned `False` cleanly, printed the expected
hint, and kept retrying. `can_tp_echo_responder.py`'s `configure()`
(`CanTpHandle`, which does **not** catch `grpc.RpcError` -- see this
node's writeup in `backlog/nodes_backlog.md`) raised instead, caught by
the node's own `try/except`; the exception's message turned out to be
more specific than a generic "unreachable" guess would have been --
`NOT_FOUND: "no CanTp plugin loaded for iface 'vcan0'"`, distinguishing
"plugin not loaded" from "gateway unreachable" -- so the node's error
message was rewritten to print that detail verbatim rather than assume a
cause, confirmed by re-running this exact scenario after the change.

# TestSet: PDU

System-level tests for PDU routing, transmission schedules, I-PDU groups, and PDU
database inspection. All routing behavior is provided by the `pdu_router.so` plugin.

Common precondition: gateway running with `BOAT_CAN_INTERFACES=vcan0` and
`BOAT_NODE_PLUGINS=<path>/pdu_router.so`; CLI installed.

---

### TC_PDU_001_route_and_send

**TestSets:** [PDU], [CLI]

**Preconditions:**
- `candump vcan0` running

**TestSteps:**
1. `boat pdu route --id 0x100 --transport can --iface vcan0`
2. `boat pdu send --id 0x100 --data AABBCCDD`
3. `boat pdu list-routes`

**Expected:**
- The PDU is emitted on `vcan0` as a CAN frame; the route appears in `list-routes`

---

### TC_PDU_002_cyclic_transmission_schedule

**TestSets:** [PDU]

**Preconditions:**
- `candump -t d vcan0` running (delta timestamps)

**TestSteps:**
1. `boat pdu route --id 0x100 --transport can --iface vcan0 --send-type cyclic --cycle-ms 100`
2. Observe the bus for ≥ 3 seconds

**Expected:**
- The PDU's frame appears every ~100 ms (within tick resolution), continuously,
  without any simulation running (the always-on node tick drives it)

---

### TC_PDU_003_remove_route_stops_transmission

**TestSets:** [PDU]

**Preconditions:**
- A cyclic route active (TC_PDU_002)

**TestSteps:**
1. `boat pdu remove-route --id 0x100`
2. Observe `candump vcan0` for 2 s; run `boat pdu list-routes`

**Expected:**
- Cyclic transmission stops; route no longer listed

---

### TC_PDU_004_ipdu_group_enable_disable

**TestSets:** [PDU], [CLI]

**Preconditions:**
- Cyclic routes for PDUs 0x100 and 0x200 configured

**TestSteps:**
1. `boat pdu group --id 1 --name "Safety" --pdu 0x100 --pdu 0x200 --disabled`
2. Observe the bus; then `boat pdu enable-group --id 1`; observe again
3. `boat pdu list-groups`

**Expected:**
- While the group is disabled neither PDU transmits; after enable both resume
- `list-groups` shows the group with its members and current state

---

### TC_PDU_005_subscribe

**TestSets:** [PDU], [CLI]

**Preconditions:**
- A cyclic route active

**TestSteps:**
1. `boat pdu subscribe` in a second shell

**Expected:**
- Routed PDUs stream to the subscriber with PDU id and payload

---

### TC_PDU_006_signal_packing_from_database

**TestSets:** [PDU], [WebUIs]

**Preconditions:**
- A PDU database with a message containing Intel and Motorola signals with
  factor/offset scaling; Commander UI running (port 8082)

**TestSteps:**
1. In the Commander, load the database, set signal values (e.g. MotorSpeed = 1000),
   and send the message
2. Decode the on-bus frame manually (or with a DBC-aware tool)

**Expected:**
- Bit positions, byte order, and scaling of every signal match the database
  definition — raw = (phys − offset) / factor at the defined start bit and length

---

### TC_PDU_007_db_inspection_cli

**TestSets:** [PDU], [CLI]

**Preconditions:**
- At least one PDU database JSON in the config directory

**TestSteps:**
1. `boat db list --db boat-platform/config/pdu_db_example.json`
2. `boat db show --db boat-platform/config/pdu_db_example.json --msg Motor_1`
3. `boat db signal-routes --db boat-platform/config/pdu_db_example.json`

**Expected:**
- `list` shows every message in the database; `show` renders the named message with
  all its signals; `signal-routes` lists the database's signal routing rules
  (source `DbId.SigId` → destination `DbId.SigId`)

---

### TC_PDU_008_grpc_delegation_to_plugin

**TestSets:** [PDU], [Plugins], [Error]

**Preconditions:**
- Gateway started WITHOUT the pdu_router plugin

**TestSteps:**
1. `boat pdu list-routes`

**Expected:**
- A clear error indicating the PDU router plugin is not loaded (gRPC PDU calls are
  delegated to the plugin) — not a crash, not an empty success

---

### TC_PDU_009_frame_send_pdu_dispatch

**TestSets:** [PDU], [Frame]

**Preconditions:**
- pdu_router loaded; a route for PDU 0x100 exists

**TestSteps:**
1. Send a PDU bus-type frame via the unified service:
   `boat frame send --bus-type pdu ...` (or `FrameService.SendFrame` from the SDK)

**Expected:**
- The frame is dispatched to the pdu_router plugin (not written to a wire directly)
  and the routed result appears on the target bus

---

### TC_PDU_010_e2e_metadata_roundtrip

**TestSets:** [PDU]

**Preconditions:**
- `boat-platform/config/pdu_db_example.json` (its `Motor_1_GW` entry has `isE2E: 4`)

**TestSteps:**
1. `boat db show --db boat-platform/config/pdu_db_example.json --msg Motor_1_GW --pack`
2. Inspect the `isE2E` field and the signal layout in the printed table

**Expected:**
- `isE2E` is shown as `4`; the message and its signals (start bit, length, byte order,
  scaling) and the packed InitValue bytes are rendered as defined by the database
- **Runtime E2E protection is not implemented**: `isE2E` is import/analysis metadata, and
  nothing recomputes a rolling counter or CRC per transmitted frame. This case covers the
  metadata round-trip only; the runtime feature is tracked in `backlog/pdu_e2e.md`

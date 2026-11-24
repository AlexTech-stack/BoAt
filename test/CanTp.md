# TestSet: CanTp

System-level tests for the CAN Transport Protocol plugin (ISO 15765-2): session
configuration, segmentation, flow control, timeouts, addressing modes, CAN FD,
padding, error/event reporting, and always-on behavior.

Common precondition: gateway running with `BOAT_CAN_INTERFACES=vcan0` and
`BOAT_NODE_PLUGINS=<path>/can_tp.so?{"iface":"vcan0"}`. `--nsdu-id` must be
numeric (hex or decimal) -- the CLI parses it via `int(x, 0)`, so a
non-numeric identifier like the literal string `diag` is rejected, not
accepted as an opaque label.

Several cases below need two independently-addressable connections talking to
each other (a "tester" and an "ECU") to observe real protocol behavior rather
than injecting raw frames by hand. Two ways to get that:
- Two CanTp instances in one gateway, one per interface: `BOAT_NODE_PLUGINS=
  <path>/can_tp.so?{"iface":"vcan0"},<path>/can_tp.so?{"iface":"vcan1"}`
  (or two real interfaces on one physical bus, e.g. `can0`/`can1` on a
  dual-channel PCAN adapter) -- `--iface` picks which instance a command
  targets.
- A single instance plus `cansend`/`candump` to play the other side by hand.

---

### TC_CanTp_001_configure_session

**TestSets:** [CanTp], [CLI]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. `boat can-tp configure --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8`

**Expected:**
- Configuration is accepted; subsequent `send`/`remove`/`subscribe` calls with
  `--nsdu-id 0x7E0` use these addresses without needing them again

---

### TC_CanTp_002_single_frame_send

**TestSets:** [CanTp], [CAN]

**Preconditions:**
- Session configured (TC_CanTp_001); `candump vcan0` running

**TestSteps:**
1. `boat can-tp send --nsdu-id 0x7E0 --data 0102030405` (≤ 7 bytes, no
   addresses on `send` -- they come from `configure`)

**Expected:**
- Exactly one CAN frame with ID 0x7E0: a Single Frame (PCI 0x05) carrying the
  payload, padded to the connection's `can_dlc` with the configured pad byte
  (default `0xCC`)

---

### TC_CanTp_003_multi_frame_segmentation

**TestSets:** [CanTp], [CAN]

**Preconditions:**
- Two connections configured as a tester/ECU pair (see top-of-file note);
  `candump` running on the bus

**TestSteps:**
1. Send a payload larger than one frame (e.g. 20 bytes) via `boat can-tp send`

**Expected:**
- A First Frame (PCI `0x1x`) with the total length, followed -- after the
  peer's Flow Control -- by Consecutive Frames (PCI `0x2x`) with incrementing
  sequence numbers until the payload is complete; the peer's reassembled data
  matches the input exactly

---

### TC_CanTp_004_always_on_reception

**TestSets:** [CanTp], [Plugins]

**Preconditions:**
- NO simulation running (node manager only)

**TestSteps:**
1. Inject a Single Frame diagnostic request onto `vcan0` addressed to the
   configured target (e.g. `cansend vcan0 7E0#0102030405`)

**Expected:**
- The CanTp plugin reacts (visible via `boat can-tp subscribe` or the gateway
  log) even though no simulation is active -- always-on node plugins are
  ticked independently of any running simulation

---

### TC_CanTp_005_send_without_configuration

**TestSets:** [CanTp], [Error]

**Preconditions:**
- Freshly started gateway; `nsdu-id` `0x999` never configured

**TestSteps:**
1. `boat can-tp send --nsdu-id 0x999 --data 01`

**Expected:**
- A clear error (`FAILED_PRECONDITION`, "no N-SDU connection configured") --
  no partial transmission

---

### TC_CanTp_006_n_bs_timeout

**TestSets:** [CanTp], [Timeout]

**Preconditions:**
- Session configured as a sender with a short `--n-bs-ms` (e.g. `1500`) and a
  `--target-addr` nobody is configured to respond on

**TestSteps:**
1. `boat can-tp send --nsdu-id ... --data <21+ bytes>` (forces multi-frame)
2. Poll `boat can-tp list-sessions` for `tx_state`

**Expected:**
- `tx_state` shows `WAIT_FC` immediately after the send, then reverts to
  `IDLE` once `--n-bs-ms` elapses -- the session self-recovers instead of
  hanging forever

---

### TC_CanTp_007_n_cr_timeout

**TestSets:** [CanTp], [Timeout]

**Preconditions:**
- Session configured as a receiver with a short `--n-cr-ms` (e.g. `1500`)

**TestSteps:**
1. Inject a First Frame via `cansend` addressed to the configured session,
   then send no Consecutive Frames at all
2. Poll `boat can-tp list-sessions` for `rx_state`

**Expected:**
- `rx_state` shows `WAIT_CF` after the FF, then reverts to `IDLE` once
  `--n-cr-ms` elapses

---

### TC_CanTp_008_extended_addressing

**TestSets:** [CanTp], [CAN]

**Preconditions:**
- Two connections configured with `--addressing-mode extended`

**TestSteps:**
1. Send a multi-frame payload from one to the other

**Expected:**
- Every SF/FF/CF/FC carries the address byte (N_TA) as its first payload
  byte; the receiver reassembles the payload correctly

---

### TC_CanTp_009_mixed_addressing_disambiguation

**TestSets:** [CanTp], [CAN]

**Preconditions:**
- Two connections configured with `--addressing-mode mixed`, the **same**
  `--target-addr`, and distinct `--address-byte` values

**TestSteps:**
1. Send different payloads to each of the two `nsdu_id`s sharing the
   `target_addr`
2. Subscribe to both receiving connections

**Expected:**
- Each receiver gets only the payload addressed to it, disambiguated by the
  address byte -- this is the actual functional point of extended/mixed
  addressing (multiplexing several logical connections onto one CAN ID)

---

### TC_CanTp_010_configure_rejects_ambiguous_target_addr

**TestSets:** [CanTp], [Error]

**Preconditions:**
- A connection already configured with `--target-addr 0x7D0` and
  `--addressing-mode normal` (no address byte)

**TestSteps:**
1. `boat can-tp configure --nsdu-id <different> --target-addr 0x7D0 ...`
   (same `target_addr`, different `nsdu_id`)

**Expected:**
- Rejected with `FAILED_PRECONDITION` -- sharing a `target_addr` is only
  permitted when every sharer has a distinct address byte (`extended`/
  `mixed`); anything else has no way to be told apart on RX

---

### TC_CanTp_011_configure_rejects_busy_session

**TestSets:** [CanTp], [Error]

**Preconditions:**
- A session mid-multi-frame-transfer (`tx_state` = `WAIT_FC` or `SEND_CF`)

**TestSteps:**
1. `boat can-tp configure --nsdu-id <same id> ...` while the transfer is
   in flight

**Expected:**
- Rejected with `FAILED_PRECONDITION` rather than silently discarding the
  in-flight transfer (AUTOSAR CanTp123); succeeds once the transfer settles
  (or times out) or after `remove`

---

### TC_CanTp_012_29bit_normal_fixed_addressing

**TestSets:** [CanTp], [CAN]

**Preconditions:**
- Two connections configured with 29-bit `--source-addr`/`--target-addr`
  values in the conventional `0x18DA<TA><SA>`/`0x18DB<TA><SA>` ("Normal
  Fixed") form, e.g. `--source-addr 0x18DAF110 --target-addr 0x18DA10F1`

**TestSteps:**
1. Send a payload from one to the other

**Expected:**
- The frame goes out with a 29-bit (extended-format) CAN ID; the peer
  receives and reassembles it correctly

---

### TC_CanTp_013_can_fd_brs

**TestSets:** [CanTp], [CAN]

**Preconditions:**
- Bus/interfaces configured for CAN FD with a distinct data-phase bit rate
  (e.g. `ip link set can0 type can bitrate 500000 dbitrate 2000000 fd on`)
- Connection configured with `--dlc 64 --brs`

**TestSteps:**
1. Send a payload; capture with `candump -x` (shows the BRS flag)

**Expected:**
- Frames go out with the BRS flag set; an otherwise-identical connection
  without `--brs` sends the same frames without it

---

### TC_CanTp_014_pad_byte_configurable

**TestSets:** [CanTp], [CAN]

**Preconditions:**
- Connection configured with a custom `--pad-byte` (hex or decimal, e.g.
  `--pad-byte 0xAB`)

**TestSteps:**
1. Send a short payload (triggers padding); capture with `candump`

**Expected:**
- Unused trailing bytes are filled with the configured pad byte, not the
  ISO/AUTOSAR default `0xCC`

---

### TC_CanTp_015_subscribe_errors

**TestSets:** [CanTp], [Error]

**Preconditions:**
- `boat can-tp subscribe-errors` running against the target instance

**TestSteps:**
1. Trigger each detectable N_Result condition in turn:
   a. N_Bs timeout (TC_CanTp_006's setup)
   b. N_Cr timeout (TC_CanTp_007's setup)
   c. Wrong CF sequence number (inject a CF with an out-of-order sequence
      number after a valid FF)
   d. Local buffer overflow (`FF_DL` > configured `--rx-buffer`)
   e. Peer-signaled overflow (inject an FC with the Overflow flag,
      `cansend ... <id>#32...`, while a send is in `WAIT_FC`)

**Expected:**
- Each scenario produces exactly one error event with the correct
  `CanTpResult` (`TIMEOUT_BS`/`TIMEOUT_CR`/`WRONG_SN`/`BUFFER_OVFLW`
  ×2) and a specific, correct human-readable message

---

### TC_CanTp_016_ff_dl_4095_limit

**TestSets:** [CanTp], [Error]

**Preconditions:**
- Gateway with `can_tp.so?{"iface":"vcan0"}`; `candump -L vcan0` running
- `boat can-tp configure --nsdu-id 0x10 --source-addr 0x7E0 --target-addr 0x7E8 --n-bs-ms 300`

**TestSteps:**
1. `boat can-tp send --nsdu-id 0x10 --data $(python3 -c "print('AB'*4096)"); echo rc=$?`
2. `boat can-tp send --nsdu-id 0x10 --data $(python3 -c "print('AB'*4095)"); echo rc=$?`

**Expected:**
- Step 1: `RPC error [INVALID_ARGUMENT]: payload exceeds the 4095-byte ISO 15765-2 FF_DL limit;
  the 32-bit length escape is not implemented`, `rc=1`, nothing on the bus (#71)
- Step 2: `multi-frame initiated`, `rc=0`; `candump` shows a First Frame `7E0#1FFF...`
  (FF_DL = 0xFFF); with no peer it ends in an N_Bs timeout

---

### TC_CanTp_017_malformed_flow_control

**TestSets:** [CanTp], [Error], [Timeout]

**Preconditions:**
- As TC_CanTp_016 (session 0x10, N_Bs 300 ms)
- `PYTHONUNBUFFERED=1 boat can-tp subscribe-errors` running in a second terminal
  (unbuffered, or redirected output is lost when the subscriber is stopped)

**TestSteps:**
1. Send a 20-byte payload (`--data $(python3 -c "print('11'*20)")`), then immediately
   `cansend vcan0 7E8#33000000` (Flow Status 3 is invalid)
2. Send again, then `cansend vcan0 7E8#30` (FC missing its BS/STmin bytes)
3. Send again, then six times `cansend vcan0 7E8#31000000` (FC Wait) in quick succession

**Expected:**
- Step 1: error `INVALID_FS` -- `peer sent invalid Flow Status 3`
- Step 2: the short FC is ignored; ~300 ms later `TIMEOUT_BS` -- `N_Bs expired after 300ms
  waiting for Flow Control`
- Step 3: `WFT_OVRN` -- `N_WFTmax exceeded (4 consecutive FC(Wait) frames)`
- Each error carries `nsdu_id 0x10` and `iface vcan0`; the gateway stays up (#72, #73)

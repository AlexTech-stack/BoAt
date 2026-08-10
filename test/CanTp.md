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

**Verdict:** OK

**Result:**
Verified on real PCAN hardware (can0/can1) throughout this session's work on
backlog items #1, #3, #6, #9, #10, #11, #15 -- dozens of `configure` calls
across every addressing mode and config combination, all accepted and
reflected correctly in `list-sessions`.

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

**Verdict:** OK

**Result:**
`candump` capture: `7A0 [8] 02 AA BB CC CC CC CC CC` for a 2-byte payload --
PCI `0x02`, payload, padded with the ISO/AUTOSAR default `0xCC` (was
hardcoded `0x55` before backlog #6's fix). Confirmed on real hardware.

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

**Verdict:** OK

**Result:**
Verified repeatedly on real PCAN hardware (can0 tester -> can1 ECU) as the
baseline regression check for every other change this session. Bus capture
for a 20-byte payload: FF (`10 14 ...`, 6 bytes) -> FC (Continue) -> CF ×2
(7 bytes each), reassembled payload byte-exact via `subscribe`. Also
confirmed with CAN FD (`--dlc 64`) using the CAN-FD SF/escape-format work
from backlog #4.

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

**Verdict:** OK

**Result:**
Every hardware test this session ran against a bare `BOAT_NODE_PLUGINS`-loaded
gateway with no simulation running, confirming always-on reception
throughout (not a dedicated one-off check, but continuously exercised).

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

**Verdict:** OK

**Result:**
`CanTpServiceImpl::Send` checks `HasConnection()` before calling into the
plugin and returns `FAILED_PRECONDITION` with a message naming the
unconfigured `nsdu_id`; confirmed via the CLI's `_rpc_error` formatting.

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

**Verdict:** OK

**Result:**
Verified on real hardware with precise wall-clock timestamps: `WAIT_FC` held
steady through checks before the deadline, flipped to `IDLE` in the check
immediately after it. Bus capture confirmed only the FF was ever sent (no FC
arrived from anywhere), proving the reset came from the timeout, not a
masked success. See backlog item #1.

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

**Verdict:** OK

**Result:**
Same methodology and outcome as TC_CanTp_006, mirrored for the RX side.
Verified on real hardware. See backlog item #1.

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

**Verdict:** OK

**Result:**
Found and fixed a fundamental bug while verifying this: the RX path read the
PCI byte from `payload[0]` unconditionally, but under extended addressing
that's the address byte, not the PCI byte -- extended-addressing RX never
worked at all before this fix. Verified end-to-end afterward: a 30-byte
transfer round-trips byte-exact; bus capture shows the correct address byte
on every frame in both directions (including FC, which previously hardcoded
`0x00` instead of the peer's address). See backlog items #3/#5.

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

**Verdict:** OK

**Result:**
Verified on real hardware: two receivers sharing `target_addr=0x7B0` with
`address_byte` 0x01/0x02 each received exactly their own payload
(`aaaaaa`/`bbbbbb`), no cross-talk. `find_by_target()` was reworked to
collect all `target_addr` matches and disambiguate by address byte when
there's more than one. See backlog item #15.

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

**Verdict:** OK

**Result:**
Verified on real hardware, both sub-cases: a `normal`-mode duplicate and a
`mixed`-mode duplicate with a *colliding* address byte were both rejected
with a clear `FAILED_PRECONDITION` message naming the conflicting
`target_addr`. Re-configuring a connection with the `target_addr` it already
owns (not a new conflict) still succeeds. See backlog item #3.

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

**Verdict:** OK

**Result:**
Verified on real hardware: reconfiguring nsdu_id 0x7F0 while `tx_state=
WAIT_FC` was rejected with a message naming the busy `nsdu_id`; the same
call succeeded once the N_Bs deadline (TC_CanTp_006) cleared it back to
`IDLE`. See backlog item #9.

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

**Verdict:** OK

**Result:**
Found and fixed a core (non-CanTp) bug while verifying this: `SocketCanDriver
::ReadFrame()` didn't mask `CAN_EFF_FLAG` off received CAN IDs, so every
received 29-bit frame's ID silently carried that flag bit while everything
else (including CanTp's `target_addr` comparison) treats CAN IDs as plain
values -- a 29-bit frame could never match on RX anywhere in the system, not
just CanTp. Fixed in `socket_can_driver.cpp`; verified afterward with a
5-byte payload round-tripping byte-exact over `0x18DAF110`/`0x18DA10F1`. See
backlog item #15.

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

**Verdict:** OK

**Result:**
Verified on real hardware with the PCAN interfaces explicitly reconfigured
for CAN FD + a data-phase bit rate: `candump -x` showed the `B` marker
present with `--brs`, absent without it, on otherwise-identical frames. See
backlog item #10.

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

**Verdict:** OK

**Result:**
Verified on real hardware: `--pad-byte 171` (0xAB) produced `02 AA BB AB AB
AB AB AB` on the wire. Also caught and fixed a CLI bug in the same pass:
`--pad-byte`/`--address-byte` were plain-decimal `int` options despite their
own `--help` text showing hex examples (`--address-byte 0x99` failed with
"not a valid integer") -- switched both to the same hex-or-decimal parsing
`--nsdu-id`/the address flags already use.

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

**Verdict:** OK

**Result:**
All five scenarios verified on real hardware via both the Python SDK
(`subscribe_errors()`) and the CLI (`subscribe-errors`), each producing
exactly the expected result code and a specific message (e.g. "N_Bs expired
after 1000ms waiting for Flow Control", "expected CF seq 1, got 2"). See
backlog item #11.

---

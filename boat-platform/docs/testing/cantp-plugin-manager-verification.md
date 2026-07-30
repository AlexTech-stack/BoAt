# Verifying the CanTp / PluginManager changes

Manual + automated verification steps for: the `CanTpService` gRPC bridge,
CanTp frame padding, the SF extended-addressing fix, multi-instance CanTp
(`--iface`), `NodePluginService` / merged `boat plugin list`, CanTp's
generic PDU-bus dispatch, and (added in a later pass) nsdu_id-only session
identity, `RemoveSession`, and `Subscribe`. Written down here because all of
this was originally verified ad hoc over SSH against a remote Linux test box
and never captured anywhere reusable.

**Session-identity regression, fixed in the later pass**: `boat can-tp send`
used to silently re-`Configure` before every `Send`. With no
`--source-addr`/`--target-addr` passed, that re-`Configure` used to fall
back to `source_addr = target_addr = nsdu_id`, and since the plugin's
connection map used to be keyed by `source_addr` (not `nsdu_id`), this
created a second, bogus session that could shadow the real one and cause
`send` to transmit on the wrong CAN ID entirely. Fixed by: `send` no longer
calls `Configure` at all (addressing comes only from the prior `configure`
call, looked up by `--nsdu-id` alone); the connection map is now keyed by
`nsdu_id`; and the single-ID auto-fallback is gone (`--source-addr`/
`--target-addr` are required, non-zero, on every `configure` call). Section
3 below reproduces the exact original bug report and confirms the fix.

**Why remote**: `PluginManager::Load()` throws on `_WIN32` (dlopen/dlsym
aren't available), so none of this is runnable on a Windows dev machine —
every step below needs a real Linux box with `vcan` support.

## 1. Automated tests (run these first)

```bash
cd boat-platform
cmake --build --preset debug --target can_tp pdu_router boat_gateway \
  boat_unit_plugin_manager boat_integration_gateway

./build/debug/src/tests/boat_unit_plugin_manager --success
./build/debug/src/tests/boat_integration_gateway --success

pip install -e ./sdk/python -e ./cli   # if not already installed
pytest cli/tests/test_cli_commands.py sdk/python/tests/test_stub_imports.py -v
```

Expect: `boat_unit_plugin_manager` — the original 8 cases (the `Unload()`
compare-and-erase fix, the `so_path+iface` composite key, CanTp's PDU-bus
dispatch) plus, from the later session-identity pass: `Configure` rejects
zero `source_addr`/`target_addr`, `Send` resolves the right connection even
when one connection's `nsdu_id` collides with another's `source_addr` (the
exact regression shape from section 3 below), and `Remove` deletes an idle
connection but refuses one with a multi-frame transfer in flight.
`boat_integration_gateway` — includes the `PluginService`/`NodePluginService`
disjoint-scope RPC test (needs `PDU_ROUTER_SO`, i.e. `pdu_router` must have
been built in the same tree) and a `CanTpService.RemoveSession`/`Subscribe`
round-trip test.

## 2. Manual: gateway setup

```bash
sudo modprobe vcan
sudo ip link add vcan0 type vcan 2>/dev/null; sudo ip link set vcan0 up
sudo ip link add vcan1 type vcan 2>/dev/null; sudo ip link set vcan1 up

cd boat-platform
BOAT_CAN_INTERFACES=vcan0,vcan1 \
BOAT_NODE_PLUGINS='./build/debug/src/plugins/can_tp/can_tp.so?{"iface":"vcan0"},./build/debug/src/plugins/can_tp/can_tp.so?{"iface":"vcan1"},./build/debug/src/plugins/pdu_router/pdu_router.so' \
  ./build/debug/src/gateway/grpc_gateway/boat_gateway &

candump vcan0 -x -t d &   # separate terminal/log per bus
candump vcan1 -x -t d &
```

Expect startup log: `[Gateway] Loaded plugin ... can_tp.so` (x2, one per
iface) and `... pdu_router.so`.

## 3. Single-frame send + padding (the original bug report)

```bash
boat can-tp configure --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8 --iface vcan0
boat can-tp send --nsdu-id 0x7E0 --data 0123 --iface vcan0
```

Expect on `candump vcan0`: exactly one frame, `7E0#02.01.23.55.55.55.55.55`
(PCI `0x02` = SF len 2, payload, then `0x55` padding out to the full DLC).

**Session-identity regression check** (the exact shape of the bug this
session's later pass found and fixed): reconfigure with a small `nsdu_id`
that could collide with another connection's `source_addr`, then send
repeatedly with no addresses, and confirm nothing ever lands on the wrong
CAN ID:

```bash
boat can-tp configure --nsdu-id 0x1 --source-addr 0x7E0 --target-addr 0x7E8 --iface vcan0
boat can-tp send --nsdu-id 0x1 --data 0102030405 --iface vcan0
boat can-tp send --nsdu-id 0x1 --data 0102030405 --iface vcan0
boat can-tp list-sessions --iface vcan0
```

Expect on `candump vcan0`: two frames, both on `7E0`, never on `001`.
`list-sessions` must show exactly **one** session for `nsdu_id=0x1` -- if a
second session appears (e.g. `nsdu_id=0x1 source_addr=0x1 target_addr=0x1`),
the regression is back.

## 4. Multi-frame (FF/FC/CF) + extended-addressing SF threshold

```bash
# Multi-frame: needs a peer to answer Flow Control -- hand-craft one.
# (Connection must already be configured -- reuse the one from section 3,
# or `boat can-tp configure --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8 --iface vcan0` first.)
boat can-tp send --nsdu-id 0x7E0 --iface vcan0 \
  --data 000102030405060708090A0B0C0D0E0F10111213 &
sleep 0.3 && cansend vcan0 7E8#300000   # FC: continue, BS=0, STmin=0
```

Expect: FF (`10 14 ...`) then two CFs, all padded to 8 bytes, no errors.

Extended addressing (only reachable via direct gRPC today, no CLI flag):

```python
import grpc
from boat.v1 import can_tp_pb2, can_tp_pb2_grpc
ch = grpc.insecure_channel("localhost:50051")
stub = can_tp_pb2_grpc.CanTpServiceStub(ch)
cfg = can_tp_pb2.CanTpConfig(nsdu_id=0x500, source_addr=0x500, target_addr=0x600,
                              can_dlc=8, extended_addressing=True)
stub.Configure(can_tp_pb2.ConfigureRequest(config=cfg, iface="vcan0"))
print(stub.Send(can_tp_pb2.SendRequest(nsdu_id=0x500, data=bytes(6), iface="vcan0")).result)  # SINGLE_FRAME
print(stub.Send(can_tp_pb2.SendRequest(nsdu_id=0x500, data=bytes(7), iface="vcan0")).result)  # MULTI_FRAME_INITIATED
```

6 bytes must stay `SEND_RESULT_SINGLE_FRAME`; 7 bytes must flip to
`SEND_RESULT_MULTI_FRAME_INITIATED` (was incorrectly SF pre-fix).

## 5. Multi-instance selection (`--iface`)

```bash
boat can-tp configure --nsdu-id 0x100 --source-addr 0x100 --target-addr 0x200  # no --iface, 2 loaded
```

Expect: `FAILED_PRECONDITION: multiple CanTp instances loaded (vcan0, vcan1); specify --iface`.

```bash
boat can-tp configure --nsdu-id 0x100 --source-addr 0x100 --target-addr 0x200 --iface vcan0
boat can-tp send --nsdu-id 0x100 --data AABB --iface vcan0
boat can-tp configure --nsdu-id 0x300 --source-addr 0x300 --target-addr 0x400 --iface vcan1
boat can-tp send --nsdu-id 0x300 --data CCDD --iface vcan1
```

Expect: `AABB`'s SF lands only on `candump vcan0`; `CCDD`'s only on `vcan1` —
each instance stays isolated to its own bus.

```bash
boat can-tp list-sessions               # both sessions, tagged with their iface
boat can-tp list-sessions --iface vcan0  # just the 0x100 session
```

Expect: the unscoped call shows both `nsdu_id=0x100 iface=vcan0` and
`nsdu_id=0x300 iface=vcan1`, each with `rx_state=IDLE tx_state=IDLE` (no
transfer in flight); the scoped call shows only the `vcan0` row.

## 6. Node-plugin visibility (`boat plugin list`)

```bash
boat plugin list
```

Expect: one row per loaded node plugin (`can_tp` x2, `pdu_router`), `scope=node`,
`config_json` showing each CanTp instance's `iface` — this is the direct
answer to "can I see what CanTp interfaces are running."

```bash
PLUGIN_ID="./build/debug/src/plugins/can_tp/can_tp.so?iface=vcan0"
boat plugin unload "$PLUGIN_ID" --scope node          # refused: needs --yes
boat plugin unload "$PLUGIN_ID" --scope node --yes     # succeeds
boat plugin list                                       # that row is gone; vcan1 + pdu_router remain
```

## 7. PDU-bus dispatch (generic trigger, not `CanTpService`)

```bash
boat can-tp configure --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8 --iface vcan0
boat frame send --bus-type pdu --iface vcan0 --pdu-id 0x7E0 --data 0123
```

Expect on `candump vcan0`: `7E0#02.01.23.55.55.55.55.55` — same SF as
section 3, but triggered through `FrameService.SendFrame` instead of
`CanTpService.Send`.

**Self-echo guard** (the important negative test): complete an RX
reassembly and confirm it does *not* spuriously re-transmit itself —

```bash
cansend vcan0 7E8#100D53EFCDAB8967   # FF, declares 13-byte payload
sleep 0.3
cansend vcan0 7E8#2145230111223344   # CF, completes the 13 bytes
```

Expect on `candump vcan0`: FF, our FC, the CF — and **nothing after**. If a
4th frame appears, the self-echo guard (`tp_on_frame`'s PDU-bus branch
requiring a real, matching `iface`) has regressed and reassembly is
re-triggering a send of the payload it just received.

## 8. Configure requires both addresses (single-ID fallback is gone)

```bash
boat can-tp configure --nsdu-id 0x999 --iface vcan0
```

Expect: `INVALID_ARGUMENT: source_addr and target_addr are both required and
must be non-zero...`. A single-ID session (one CAN ID for both directions)
must be expressed explicitly:

```bash
boat can-tp configure --nsdu-id 0x123 --source-addr 0x123 --target-addr 0x123 --iface vcan0
boat can-tp send --nsdu-id 0x123 --data AA --iface vcan0
```

Expect on `candump vcan0`: one SF on `123`.

## 9. Remove

```bash
boat can-tp remove --nsdu-id 0xDEAD --iface vcan0
```

Expect: `NOT_FOUND` (nothing configured for that nsdu_id).

```bash
boat can-tp configure --nsdu-id 0x123 --source-addr 0x123 --target-addr 0x123 --iface vcan0
boat can-tp remove --nsdu-id 0x123 --iface vcan0
boat can-tp list-sessions --iface vcan0
boat can-tp send --nsdu-id 0x123 --data AA --iface vcan0
```

Expect: `remove` reports `removed=True`; `list-sessions` no longer shows
`0x123`; the subsequent `send` fails with `FAILED_PRECONDITION` (no
connection configured), same as sending to any never-configured `nsdu_id`.

**Busy refusal** -- start a multi-frame transfer, then try to remove it
before Flow Control arrives:

```bash
boat can-tp configure --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8 --iface vcan0
boat can-tp send --nsdu-id 0x7E0 --iface vcan0 --data 000102030405060708090A0B0C0D0E0F10111213 &
sleep 0.2
boat can-tp remove --nsdu-id 0x7E0 --iface vcan0
```

Expect: `FAILED_PRECONDITION: ...has a multi-frame transfer in progress...`.
Answer the Flow Control (`cansend vcan0 7E8#300000`, as in section 4) to let
the transfer finish, then `remove` should succeed.

## 10. Subscribe

```bash
boat can-tp configure --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8 --iface vcan0
boat can-tp subscribe --nsdu-id 0x7E0 --iface vcan0   # separate terminal, leave running
```

In another terminal:

```bash
boat can-tp send --nsdu-id 0x7E0 --data AABBCC --iface vcan0
```

Expect: the `subscribe` terminal prints one row (`nsdu_id=0x7E0
data=AABBCC iface=vcan0 timestamp_ns=...`) shortly after the send. Repeat
with a multi-frame payload (as in section 4, answering the FC) and confirm
`subscribe` reports the full reassembled payload once, not once per CF.
`Ctrl-C` the subscriber and confirm it exits cleanly (no hang, no orphaned
gateway-side subscription -- a second `subscribe` run afterward should not
see stale duplicate events).

## Cleanup

```bash
kill %1 %2 %3   # or: pkill -f boat_gateway; pkill -f 'candump vcan'
```

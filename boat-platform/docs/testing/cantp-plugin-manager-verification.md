# Verifying the CanTp / PluginManager changes

Manual + automated verification steps for: the `CanTpService` gRPC bridge,
CanTp frame padding, the SF extended-addressing fix, multi-instance CanTp
(`--iface`), `NodePluginService` / merged `boat plugin list`, and CanTp's
generic PDU-bus dispatch. Written down here because all of this was
originally verified ad hoc over SSH against a remote Linux test box and
never captured anywhere reusable.

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

Expect: `boat_unit_plugin_manager` — 8 test cases covering the `Unload()`
compare-and-erase fix, the `so_path+iface` composite key (collision and
no-collision cases), and CanTp's PDU-bus dispatch (match + iface-mismatch/
no-iface no-op). `boat_integration_gateway` — includes the
`PluginService`/`NodePluginService` disjoint-scope RPC test (needs
`PDU_ROUTER_SO`, i.e. `pdu_router` must have been built in the same tree).

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
boat can-tp send --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8 --data 0123 --iface vcan0
```

Expect on `candump vcan0`: exactly one frame, `7E0#02.01.23.55.55.55.55.55`
(PCI `0x02` = SF len 2, payload, then `0x55` padding out to the full DLC).

## 4. Multi-frame (FF/FC/CF) + extended-addressing SF threshold

```bash
# Multi-frame: needs a peer to answer Flow Control -- hand-craft one
boat can-tp send --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8 --iface vcan0 \
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
boat can-tp send --nsdu-id 0x100 --source-addr 0x100 --target-addr 0x200 --data AABB --iface vcan0
boat can-tp configure --nsdu-id 0x300 --source-addr 0x300 --target-addr 0x400 --iface vcan1
boat can-tp send --nsdu-id 0x300 --source-addr 0x300 --target-addr 0x400 --data CCDD --iface vcan1
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

## Cleanup

```bash
kill %1 %2 %3   # or: pkill -f boat_gateway; pkill -f 'candump vcan'
```

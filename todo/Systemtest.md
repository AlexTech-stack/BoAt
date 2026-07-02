# BoAt Platform — System Test Cases

## Prerequisites

All tests assume the following environment:
- BoAt platform built with `cmake --preset debug && cmake --build --preset debug`
- Working directory: `boat-platform/`
- `sudo` access for creating virtual CAN/Ethernet interfaces
- `vcan0` and `vcan1` created: `sudo ip link add vcan0 type vcan && sudo ip link set vcan0 up && sudo ip link add vcan1 type vcan && sudo ip link set vcan1 up`
- Gateway binary: `build/debug/src/gateway/grpc_gateway/boat_gateway`
- Python SDK installed: `pip install -e ./sdk/python -e ./cli`
- Plugin directory: `build/debug/src/plugins/`

---

## 1. Gateway Startup Tests

---

**TestcaseNr:** 1
**Scope:** Gateway startup with no plugins
**Precondition:** vcan0 is up. No BOAT_NODE_PLUGINS set.
**Step 1:** Start gateway: `BOAT_CAN_INTERFACES=vcan0 ./build/debug/src/gateway/grpc_gateway/boat_gateway`
**Step 2:** Check stderr output
**Verification point:** Observe gateway startup messages on stderr. Verify gRPC server listening on 0.0.0.0:50051. Verify vcan0 registered. Verify PduRouter plugin NOT loaded (".so not found" or "not available" message).
**Expected Behaviour:** Gateway starts without crash. `vcan0` is listed in the CAN interface logs. gRPC server starts on port 50051. PduRouter auto-load attempt logs a warning but gateway continues.

---

**TestcaseNr:** 2
**Scope:** Gateway startup with PduRouter plugin
**Precondition:** vcan0 is up. pdu_router.so exists in build directory.
**Step 1:** Start gateway: `BOAT_CAN_INTERFACES=vcan0 BOAT_NODE_PLUGINS=./build/debug/src/plugins/pdu_router/pdu_router.so ./build/debug/src/gateway/grpc_gateway/boat_gateway`
**Step 2:** Check stderr output
**Verification point:** Observe "PduRouter plugin loaded, PDU routing available" in stderr output.
**Expected Behaviour:** PduRouter plugin loads successfully. PDU gRPC commands become available.

---

**TestcaseNr:** 3
**Scope:** Gateway startup with all major plugins
**Precondition:** vcan0 is up. All plugin .so files exist.
**Step 1:** Start gateway with all plugins:
```
BOAT_CAN_INTERFACES=vcan0 \
  BOAT_NODE_PLUGINS=./build/debug/src/plugins/pdu_router/pdu_router.so,\
./build/debug/src/plugins/can_responder/can_responder.so,\
./build/debug/src/plugins/vehicle_dynamics/vehicle_dynamics.so,\
./build/debug/src/plugins/someip/someip.so \
  ./build/debug/src/gateway/grpc_gateway/boat_gateway
```
**Step 2:** Check stderr for each plugin load message
**Verification point:** All plugins listed with "Loaded plugin" messages. No crash.
**Expected Behaviour:** Gateway starts with 4 plugins loaded. All plugin load messages appear without errors.

---

**TestcaseNr:** 4
**Scope:** Gateway startup without CAN interfaces
**Precondition:** No BOAT_CAN_INTERFACES set.
**Step 1:** Start gateway: `./build/debug/src/gateway/grpc_gateway/boat_gateway`
**Verification point:** Gateway falls back to default `vcan0`. If vcan0 doesn't exist, logs "Failed to open CAN interface 'vcan0'" but continues.
**Expected Behaviour:** Gateway starts and gRPC server listens. If vcan0 doesn't exist, the error is logged but the gateway doesn't crash.

---

**TestcaseNr:** 5
**Scope:** Gateway startup with Ethernet interface
**Precondition:** veth0 virtual Ethernet pair exists.
**Step 1:** Create veth pair: `sudo ip link add veth0 type veth peer name veth1 && sudo ip link set veth0 up && sudo ip link set veth1 up`
**Step 2:** Start gateway: `BOAT_ETH_INTERFACES=veth0 BOAT_CAN_INTERFACES=vcan0 ./build/debug/src/gateway/grpc_gateway/boat_gateway`
**Step 3:** Run `boat eth list-ifaces`
**Verification point:** `veth0` appears in the Ethernet interface list.
**Expected Behaviour:** Ethernet interface registered and listed via `boat eth list-ifaces`.

---

## 2. CAN Communication Tests

---

**TestcaseNr:** 10
**Scope:** Send CAN frame via CLI (deprecated path)
**Precondition:** Gateway running with vcan0.
**Step 1:** Send CAN frame: `boat can send --id 0x300 --data DEADBEEF --bus vcan0`
**Verification point:** Use `candump vcan0` in another terminal. Observe CAN frame with ID 0x300 and 4-byte payload DE:AD:BE:EF.
**Expected Behaviour:** CAN frame 0x300 appears on vcan0 with correct payload. CLI returns accepted=true.

---

**TestcaseNr:** 11
**Scope:** Send CAN frame via unified FrameService (recommended)
**Precondition:** Gateway running with vcan0.
**Step 1:** Send CAN frame: `boat frame send --bus-type CAN --can-id 0x100 --data AABBCCDD --iface vcan0`
**Verification point:** Use `candump vcan0`. Observe CAN frame with ID 0x100 and payload AA:BB:CC:DD.
**Expected Behaviour:** CAN frame 0x100 appears on vcan0. CLI prints "Frame sent: bus_type=CAN iface=vcan0".

---

**TestcaseNr:** 12
**Scope:** Subscribe to CAN frames via FrameService
**Precondition:** Gateway running with vcan0.
**Step 1:** Open terminal A: `boat frame subscribe --bus-types CAN`
**Step 2:** Open terminal B: `boat frame send --bus-type CAN --can-id 0x200 --data CAFE --iface vcan0`
**Verification point:** Terminal A displays frame with `[CAN] vcan0 can_id=0x200  cafe`.
**Expected Behaviour:** Subscriber sees the sent CAN frame with correct can_id and payload.

---

**TestcaseNr:** 13
**Scope:** List CAN interfaces
**Precondition:** Gateway running with vcan0 and vcan1.
**Step 1:** Run `boat can list-buses`
**Verification point:** Table or JSON output lists `vcan0` and `vcan1` with driver name (vcan), state (up), and FD support flag.
**Expected Behaviour:** All registered CAN interfaces appear with metadata.

---

**TestcaseNr:** 14
**Scope:** Detect CAN hardware (no gateway required)
**Precondition:** Any CAN interfaces exist in /sys/class/net/.
**Step 1:** Run `boat can detect`
**Verification point:** Lists CAN interfaces found via sysfs, including type (virtual/physical), driver, FD support.
**Expected Behaviour:** Virtual and physical CAN interfaces listed correctly. Does not require gateway.

---

**TestcaseNr:** 15
**Scope:** Send CAN FD frame
**Precondition:** Gateway running with an FD-capable CAN interface.
**Step 1:** Send FD frame: `boat frame send --bus-type CAN --can-id 0x500 --data AABBCCDDEEFFGGHH --iface can0 --fd`
**Verification point:** Use `candump can0` with FD support. Observe CAN FD frame with DLC corresponding to 8 bytes and FDF flag set.
**Expected Behaviour:** CAN FD frame transmitted with FDF flag.

---

## 3. Ethernet Communication Tests

---

**TestcaseNr:** 20
**Scope:** Send Ethernet frame via CLI (deprecated)
**Precondition:** Gateway running with `BOAT_ETH_INTERFACES=veth0`. veth pair created.
**Step 1:** Send Ethernet frame: `boat eth send --iface veth0 --dst FF:FF:FF:FF:FF:FF --payload DEADBEEF --ethertype 0x0800`
**Verification point:** Use tcpdump on veth1: `sudo tcpdump -i veth1 -X`. Observe Ethernet frame with ethertype 0x0800 and payload DE:AD:BE:EF.
**Expected Behaviour:** Ethernet frame appears on veth1 with correct payload.

---

**TestcaseNr:** 21
**Scope:** Send Ethernet frame via FrameService (recommended)
**Precondition:** Gateway running with veth0.
**Step 1:** Send frame: `boat frame send --bus-type ETHERNET --ethertype 0x0800 --dst-mac FF:FF:FF:FF:FF:FF --data AABB`
**Verification point:** tcpdump on veth1 shows frame with ethertype 0x0800 and payload AA:BB.
**Expected Behaviour:** Frame appears on Ethernet bus.

---

**TestcaseNr:** 22
**Scope:** Subscribe to Ethernet frames via FrameService
**Precondition:** Gateway running with veth0.
**Step 1:** Open terminal A: `boat frame subscribe --bus-types ETHERNET`
**Step 2:** Open terminal B: `boat frame send --bus-type ETHERNET --ethertype 0x88B5 --data ABCD`
**Verification point:** Terminal A displays frame with `[ETHERNET]`.
**Expected Behaviour:** Subscriber sees Ethernet frame.

---

## 4. Plugin Tests

---

**TestcaseNr:** 30
**Scope:** Load plugin at runtime via gRPC RegisterPlugin
**Precondition:** Gateway running with vcan0.
**Step 1:** Register plugin: `boat plugin register --path ./build/debug/src/plugins/vehicle_dynamics/vehicle_dynamics.so --config '{"initial_speed_kmh":100}'`
**Step 2:** `boat plugin list`
**Verification point:** Plugin appears in list with loaded=true.
**Expected Behaviour:** Plugin loads and appears in plugin list. Vehicle dynamics starts publishing CAN frames 0x100 and 0x101.

---

**TestcaseNr:** 31
**Scope:** Unload plugin at runtime
**Precondition:** Vehicle dynamics plugin loaded from TC30.
**Step 1:** `boat plugin list` to get plugin_id (the .so path)
**Step 2:** `boat plugin unload <plugin_id>`
**Step 3:** Verify CAN frames 0x100/0x101 stop: `boat frame subscribe --bus-types CAN --count 20`
**Verification point:** After unload, no more CAN frames 0x100 or 0x101 appear.
**Expected Behaviour:** Plugin unloaded cleanly. CAN traffic from that plugin stops.

---

**TestcaseNr:** 32
**Scope:** can_responder plugin — trigger and response
**Precondition:** Gateway running with can_responder plugin loaded via BOAT_NODE_PLUGINS. vcan0 and vcan1 exist.
**Step 1:** In terminal A: `boat frame subscribe --bus-types CAN`
**Step 2:** In terminal B: `boat frame send --bus-type CAN --can-id 0x123 --data 0000000000000000 --iface vcan1`
**Verification point:** Terminal A shows an incoming CAN frame with can_id=0x123, and shortly after a CAN frame with can_id=0x234 and payload 11:22:33:44:55:66:77:88.
**Expected Behaviour:** CAN ID 0x123 on vcan1 triggers automatic response 0x234 with fixed payload. Sending 0x123 on vcan0 produces NO response (plugin only listens on vcan1).

---

**TestcaseNr:** 33
**Scope:** can_responder — no response on wrong interface
**Precondition:** Same as TC32.
**Step 1:** Send CAN frame on vcan0: `boat frame send --bus-type CAN --can-id 0x123 --data 0000000000000000 --iface vcan0`
**Verification point:** No response frame 0x234 appears.
**Expected Behaviour:** can_responder only responds on vcan1, not vcan0.

---

**TestcaseNr:** 34
**Scope:** Vehicle dynamics plugin — publishes CAN frames
**Precondition:** Gateway running with vehicle_dynamics plugin loaded and vcan0.
**Step 1:** `boat frame subscribe --bus-types CAN --count 20`
**Verification point:** Multiple CAN frames observed: 0x100 (4-byte speed value, varies) and 0x101 (4-byte RPM value, varies).
**Expected Behaviour:** Both CAN IDs appear repeatedly on every tick (default 1ms). Values change with random walk.

---

**TestcaseNr:** 35
**Scope:** Vehicle dynamics — Ethernet output
**Precondition:** Gateway running with vehicle_dynamics and veth0.
**Step 1:** `boat frame subscribe --bus-types ETHERNET`
**Verification point:** Ethernet frames with ethertype 0x0800, 8-byte payload containing speed and RPM values.
**Expected Behaviour:** Ethernet frames published on every tick with 8 bytes (4B speed + 4B RPM) in LE format.

---

**TestcaseNr:** 36
**Scope:** Vehicle dynamics — signal publishing
**Precondition:** Vehicle dynamics loaded.
**Step 1:** Subscribe to signals: `boat signal subscribe --names speed,rpm` (or use Python SDK)
**Verification point:** Named signals "speed" and "rpm" stream with changing values.
**Expected Behaviour:** Signals published on every tick.

---

**TestcaseNr:** 37
**Scope:** Network sim plugin — stderr output
**Precondition:** Gateway running with network_sim plugin loaded.
**Step 1:** Check gateway stderr output.
**Verification point:** Observe `[network_sim] frame_count=... simulated_load=...` messages with increasing frame_count.
**Expected Behaviour:** frame_count increments on every tick. simulated_load = frame_count * bus_load_percent / 100.

---

**TestcaseNr:** 38
**Scope:** Sensor model plugin — stderr output
**Precondition:** Gateway running with sensor_model plugin loaded.
**Step 1:** Check gateway stderr output.
**Verification point:** Observe `[sensor_model] tick=... sensor_type=LIDAR` messages.
**Expected Behaviour:** sensor_type from config displayed on each tick.

---

**TestcaseNr:** 39
**Scope:** Plugin ABI version rejection
**Precondition:** A v7 or mismatched plugin .so exists (or create one with different BOAT_PLUGIN_ABI_VERSION).
**Step 1:** Attempt to load: `boat plugin register --path ./old_plugin.so`
**Verification point:** Error returned: "Plugin ABI version mismatch (7 != 8)".
**Expected Behaviour:** Plugin rejected with clear ABI version error. Gateway continues.

---

## 5. PDU Routing Tests (PduRouter Plugin Required)

---

**TestcaseNr:** 40
**Scope:** PDU gRPC unavailable without PduRouter plugin
**Precondition:** Gateway started WITHOUT pdu_router.so.
**Step 1:** Run `boat pdu route --id 0x100 --transport can --iface vcan0`
**Verification point:** Error: "PduRouter plugin not loaded" (gRPC status NOT_FOUND).
**Expected Behaviour:** PDU commands return NOT_FOUND when plugin not loaded. Gateway doesn't crash.

---

**TestcaseNr:** 41
**Scope:** Configure PDU route over CAN
**Precondition:** Gateway with PduRouter loaded, vcan0 registered.
**Step 1:** `boat pdu route --id 0x300 --transport can --iface vcan0`
**Step 2:** `boat pdu list-routes`
**Verification point:** Route with pdu_id=0x300, transport=CAN, iface=vcan0 appears.
**Expected Behaviour:** Route configured and listed.

---

**TestcaseNr:** 42
**Scope:** Send PDU over CAN route
**Precondition:** PDU route 0x300 configured on vcan0 (TC41).
**Step 1:** `boat pdu send --id 0x300 --data AABBCCDD`
**Verification point:** `candump vcan0` shows CAN frame with can_id=0x300 and payload AA:BB:CC:DD.
**Expected Behaviour:** PDU payload routed as CAN frame with matching CAN ID.

---

**TestcaseNr:** 43
**Scope:** Subscribe to PDUs via gRPC
**Precondition:** PDU route configured.
**Step 1:** Terminal A: `boat pdu subscribe`
**Step 2:** Terminal B: `boat pdu send --id 0x300 --data CAFE`
**Verification point:** Terminal A shows PduFrame with pdu_id=0x300 and payload CAFE.
**Expected Behaviour:** PDU subscriber receives outgoing PDUs.

---

**TestcaseNr:** 44
**Scope:** Remove PDU route
**Precondition:** PDU route 0x300 configured.
**Step 1:** `boat pdu remove-route --id 0x300`
**Step 2:** `boat pdu send --id 0x300 --data DEAD`
**Verification point:** Send returns error (route not found). No CAN frame appears.
**Expected Behaviour:** Route removed. Sending returns false.

---

**TestcaseNr:** 45
**Scope:** Configure PDU route with cyclic transmission
**Precondition:** Gateway with PduRouter, vcan0.
**Step 1:** `boat pdu route --id 0x400 --transport can --iface vcan0 --send-type cyclic --cycle-ms 100 --can-id 0x500`
**Step 2:** `boat pdu send --id 0x400 --data 01020304` (triggers first send)
**Step 3:** `candump vcan0`
**Verification point:** CAN frame with can_id=0x500 and payload 01:02:03:04 appears every ~100ms.
**Expected Behaviour:** Cyclic transmission triggers on configured interval. can_id=0x500 overrides default (0x400).

---

**TestcaseNr:** 46
**Scope:** PDU route over Ethernet (sim-only path)
**Precondition:** Gateway with PduRouter, veth0.
**Step 1:** `boat pdu route --id 0x600 --transport eth --iface veth0 --ethertype 0x88B5`
**Step 2:** `boat pdu send --id 0x600 --data AB`
**Verification point:** `tcpdump -i veth1` shows Ethernet frame with ethertype 0x88B5. Payload = 4 bytes PDU ID (0x00000600 big-endian) + AB.
**Expected Behaviour:** Ethernet frame with 4-byte PDU ID header + payload.

---

**TestcaseNr:** 47
**Scope:** PDU route over Ethernet with IP/UDP/IpduM
**Precondition:** Gateway with PduRouter, veth0.
**Step 1:** `boat pdu route --id 0x700 --transport eth --iface veth0 --dst-ip 10.0.0.2 --src-ip 10.0.0.1 --dst-port 9999 --src-port 8888`
**Step 2:** `boat pdu send --id 0x700 --data FFFF`
**Verification point:** `tcpdump -i veth1 -X` shows IPv4/UDP datagram on port 9999 with IpduM LONG header [PDU ID 4B BE][DLC 4B BE][payload FFFF].
**Expected Behaviour:** Full UDP/IP/IpduM frame with correct addressing.

---

## 6. I-PDU Groups Tests (PduRouter Plugin Required)

---

**TestcaseNr:** 50
**Scope:** Create I-PDU group
**Precondition:** Gateway with PduRouter.
**Step 1:** `boat pdu group --id 1 --name "Safety" --pdu 0x100 --pdu 0x200 --enabled`
**Step 2:** `boat pdu list-groups`
**Verification point:** Group 1 "Safety" appears with pdu_ids [0x100, 0x200] and enabled=true.
**Expected Behaviour:** Group created and listed.

---

**TestcaseNr:** 51
**Scope:** Disable I-PDU group — traffic blocked
**Precondition:** Group 1 "Safety" with PDU 0x100 enabled. PDU route 0x100 configured on vcan0.
**Step 1:** `boat pdu disable-group --id 1`
**Step 2:** `boat pdu send --id 0x100 --data DEAD`
**Verification point:** Send returns false (gated). No CAN frame on candump.
**Expected Behaviour:** PDU in disabled group is silently dropped.

---

**TestcaseNr:** 52
**Scope:** Enable I-PDU group — traffic resumes
**Precondition:** Group 1 disabled (TC51).
**Step 1:** `boat pdu enable-group --id 1`
**Step 2:** `boat pdu send --id 0x100 --data BEEF`
**Verification point:** CAN frame appears on vcan0 with payload BE:EF.
**Expected Behaviour:** Re-enabled group allows PDU traffic.

---

**TestcaseNr:** 53
**Scope:** Group with multiple PDUs — disable blocks all
**Precondition:** Group 1 with PDUs [0x100, 0x200]. Routes configured for both.
**Step 1:** `boat pdu disable-group --id 1`
**Step 2:** Send both PDUs. Verify both are blocked.
**Step 3:** `boat pdu enable-group --id 1`
**Step 4:** Send both PDUs. Verify both go through.
**Expected Behaviour:** Group enable/disable affects all member PDUs atomically.

---

## 7. CanTp Tests (ISO 15765-2)

---

**TestcaseNr:** 60
**Scope:** CanTp single-frame send
**Precondition:** Gateway with CanTp and PduRouter plugins loaded. vcan0.
**Step 1:** `boat can-tp configure --nsdu-id test-sf --source-addr 0x7E0 --target-addr 0x7E8 --dlc 8`
**Step 2:** `candump vcan0 &`
**Step 3:** `boat can-tp send --nsdu-id test-sf --source-addr 0x7E0 --target-addr 0x7E8 --data 0102030405 --dlc 8`
**Verification point:** candump shows CAN frame on 0x7E0. First byte is PCI = 0x05 (Single Frame, length 5). Remaining bytes are 01 02 03 04 05.
**Expected Behaviour:** 5-byte payload sent as Single Frame on source_addr 0x7E0.

---

**TestcaseNr:** 61
**Scope:** CanTp multi-frame send (simulated FC)
**Precondition:** CanTp configured with FC receiver (or simulate peer FC on vcan0).
**Step 1:** `boat can-tp configure --nsdu-id test-mf --source-addr 0x7E0 --target-addr 0x7E8 --dlc 8 --bs 0 --stmin 0`
**Step 2:** Send 20 bytes: `boat can-tp send --nsdu-id test-mf --source-addr 0x7E0 --target-addr 0x7E8 --data 0102030405060708090A0B0C0D0E0F1011121314 --dlc 8`
**Step 3:** Verify candump shows: FF (First Frame on 0x7E0 with total length 20), then wait for FC response (simulated peer, or manual).
**Verification point:** First Frame has PCI=0x10 + length_hi (0x0014 = 20 bytes). Consecutive Frames have PCI=0x2X.
**Expected Behaviour:** Multi-frame PDU segmented per ISO 15765-2.

---

## 8. PDU Transmission Schedule Tests

---

**TestcaseNr:** 70
**Scope:** Cyclic transmission — fixed interval
**Precondition:** Gateway with PduRouter, vcan0.
**Step 1:** `boat pdu route --id 0x800 --transport can --iface vcan0 --send-type cyclic --cycle-ms 100`
**Step 2:** `boat pdu send --id 0x800 --data 01`
**Step 3:** Monitor candump for ~1 second
**Verification point:** CAN frame 0x800 appears at ~100ms intervals.
**Expected Behaviour:** Cyclic transmission fires every cycle_ms after first send triggers the schedule.

---

**TestcaseNr:** 71
**Scope:** OnChange transmission — triggers on payload change
**Precondition:** Gateway with PduRouter, vcan0.
**Step 1:** `boat pdu route --id 0x900 --transport can --iface vcan0 --send-type onchange --fast-ms 10 --reps 3`
**Step 2:** `boat pdu send --id 0x900 --data AA` → triggers immediate send
**Step 3:** `boat pdu send --id 0x900 --data BB` → triggers send + 3 fast repetitions at 10ms
**Verification point:** First send (unique payload) triggers 1 send. Second send (different payload) triggers 1 send + 3 fast repetitions.
**Expected Behaviour:** OnChange triggers immediate send + N fast repetitions at fast_ms interval.

---

**TestcaseNr:** 72
**Scope:** Mixed transmission — cyclic + OnChange
**Precondition:** Gateway with PduRouter, vcan0.
**Step 1:** `boat pdu route --id 0xA00 --transport can --iface vcan0 --send-type mixed --cycle-ms 200 --fast-ms 20 --reps 2`
**Step 2:** Send initial payload. Observe cyclic sends at ~200ms.
**Step 3:** Change payload: `boat pdu send --id 0xA00 --data NEW`. Verify immediate send + 2 fast repetitions.
**Verification point:** Background cyclic at 200ms. On payload change, additional fast repetitions fire at 20ms.
**Expected Behaviour:** Mixed mode provides both background cyclic and OnChange acceleration.

---

## 9. Replay Tests

---

**TestcaseNr:** 80
**Scope:** Replay from recorded trace
**Precondition:** A trace file exists (e.g., from `boat trace start`).
**Step 1:** `boat replay start --trace /path/to/trace.blf --speed real-time`
**Step 2:** `boat replay stream --replay-id <returned_id>`
**Verification point:** Replayed events stream from the server. Each event has a tick and payload.
**Expected Behaviour:** Trace events replayed at recorded timestamps.

---

**TestcaseNr:** 81
**Scope:** Replay seek
**Precondition:** Active replay session from TC80.
**Step 1:** `boat replay seek --tick 5000 --replay-id <id>`
**Verification point:** Replay position jumps to tick 5000. Subsequent events come from that point.
**Expected Behaviour:** Seek works and replay continues from new position.

---

**TestcaseNr:** 82
**Scope:** Replay pause and resume
**Precondition:** Active replay.
**Step 1:** `boat replay pause --replay-id <id>`
**Step 2:** Verify no more events stream for 2 seconds
**Step 3:** `boat replay resume --replay-id <id>`
**Verification point:** Events stop after pause, resume after resume.
**Expected Behaviour:** Pause/resume cycle works.

---

## 10. FrameService (v8 Unified) Tests

---

**TestcaseNr:** 90
**Scope:** FrameService send CAN with metadata
**Precondition:** Gateway running with vcan0.
**Step 1:** `boat frame send --bus-type CAN --can-id 0x123 --data AABB --iface vcan0`
**Verification point:** `candump vcan0` shows CAN frame 0x123 with payload AA:BB.
**Expected Behaviour:** CAN frame sent and observable on bus.

---

**TestcaseNr:** 91
**Scope:** FrameService subscribe with bus type filter
**Precondition:** Gateway with vcan0.
**Step 1:** Terminal A: `boat frame subscribe --bus-types CAN`
**Step 2:** Terminal B: send CAN and Ethernet frames
**Verification point:** Terminal A only shows CAN frames, not Ethernet frames.
**Expected Behaviour:** Bus type filter works correctly.

---

**TestcaseNr:** 92
**Scope:** FrameService send TCP frame
**Precondition:** Gateway with TCP plugin loaded.
**Step 1:** `boat frame send --bus-type TCP --dst-ip 10.0.0.1 --dst-port 8080 --data hellohex`
**Verification point:** Frame accepted (response) or specific TCP status returned.
**Expected Behaviour:** TCP frame processed by TCP plugin.

---

**TestcaseNr:** 93
**Scope:** FrameService send PDU frame
**Precondition:** Gateway with PduRouter plugin.
**Step 1:** `boat frame send --bus-type PDU --pdu-id 0x300 --data ABCD`
**Verification point:** PDU frame dispatched to PduRouter for routing.
**Expected Behaviour:** PDU frame routed through PduRouter plugin to configured transport.

---

## 11. Error Handling Tests

---

**TestcaseNr:** 100
**Scope:** Send CAN on non-existent interface
**Precondition:** Gateway running with vcan0 only.
**Step 1:** `boat frame send --bus-type CAN --can-id 0x100 --data AA --iface nonexisent`
**Verification point:** CLI returns error: "CAN interface not found" or "Frame not accepted".
**Expected Behaviour:** Graceful error without crash.

---

**TestcaseNr:** 101
**Scope:** PDU commands without PduRouter plugin
**Precondition:** Gateway started without pdu_router.so.
**Step 1:** `boat pdu route --id 0x100 --transport can --iface vcan0`
**Verification point:** Error: NOT_FOUND. "PduRouter plugin not loaded".
**Expected Behaviour:** Graceful error when PduRouter not loaded.

---

**TestcaseNr:** 102
**Scope:** Invalid JSON config for plugin
**Precondition:** Gateway running.
**Step 1:** `boat plugin register --path ./vehicle_dynamics.so --config '{invalid json}'`
**Verification point:** Plugin load fails with parse error. Gateway continues running.
**Expected Behaviour:** Error reported. Other plugins unaffected.

---

**TestcaseNr:** 103
**Scope:** Plugin .so file not found
**Precondition:** Gateway running.
**Step 1:** `boat plugin register --path /nonexistent/path.so`
**Verification point:** Error: "cannot open shared object file" or similar.
**Expected Behaviour:** Error returned. Gateway continues.

---

## 12. Signal Bus Tests

---

**TestcaseNr:** 110
**Scope:** Bus signal publish and subscribe
**Precondition:** Gateway running with vehicle_dynamics loaded (publishes bus signals).
**Step 1:** Subscribe to bus signals via Python or gRPC: `boat bus subscribe --names vehicle.speed,vehicle.rpm`
**Verification point:** Signal values stream with changing speed and RPM values.
**Expected Behaviour:** Bus signals published and received correctly.

---

## 13. Multi-Bus Parallel Tests

---

**TestcaseNr:** 120
**Scope:** Concurrent CAN and Ethernet traffic
**Precondition:** Gateway with vcan0 and veth0, PduRouter plugin loaded.
**Step 1:** Terminal A: `boat frame subscribe --bus-types CAN,ETHERNET`
**Step 2:** Terminal B: Send CAN frames at 10ms intervals in a loop
**Step 3:** Terminal C: Send Ethernet frames at 20ms intervals in a loop
**Verification point:** Terminal A shows interleaved CAN and Ethernet frames. No dropped frames. No crashes.
**Expected Behaviour:** Both buses handled concurrently without interference.

---

**TestcaseNr:** 121
**Scope:** Multiple concurrent subscribers
**Precondition:** Gateway with vcan0.
**Step 1:** Terminal A: `boat frame subscribe --bus-types CAN`
**Step 2:** Terminal B: `boat frame subscribe --bus-types CAN`
**Step 3:** Terminal C: Send CAN frames
**Verification point:** Both terminals A and B receive the same CAN frames.
**Expected Behaviour:** Multiple subscribers all receive frames.

---

## 14. Performance / Stress Tests

---

**TestcaseNr:** 130
**Scope:** High-frequency CAN frame send
**Precondition:** Gateway with vcan0.
**Step 1:** Send 1000 CAN frames rapidly in a loop (e.g., via Python script or shell loop)
**Verification point:** All frames appear on candump. No frames dropped. Gateway doesn't crash.
**Expected Behaviour:** High-frequency traffic handled without errors.

---

**TestcaseNr:** 131
**Scope:** Gateway uptime with multiple plugins
**Precondition:** Gateway with 4+ plugins loaded.
**Step 1:** Let gateway run for 5 minutes under load (cyclic PDUs, CAN/Ethernet traffic).
**Step 2:** Check for memory leaks or crashes.
**Verification point:** Gateway still running and responsive. Memory usage stable.
**Expected Behaviour:** No memory leaks, no crashes under sustained load.

---

## Test Case Index

| TC | Category | Feature |
|----|----------|---------|
| 1 | Gateway Startup | No plugins |
| 2 | Gateway Startup | PduRouter plugin |
| 3 | Gateway Startup | All major plugins |
| 4 | Gateway Startup | No CAN interfaces |
| 5 | Gateway Startup | Ethernet interface |
| 10 | CAN | Send via CLI (deprecated) |
| 11 | CAN | Send via FrameService |
| 12 | CAN | Subscribe via FrameService |
| 13 | CAN | List buses |
| 14 | CAN | Detect hardware |
| 15 | CAN | CAN FD frame |
| 20 | Ethernet | Send via CLI (deprecated) |
| 21 | Ethernet | Send via FrameService |
| 22 | Ethernet | Subscribe via FrameService |
| 30 | Plugin | Register at runtime |
| 31 | Plugin | Unload at runtime |
| 32 | Plugin | can_responder trigger |
| 33 | Plugin | can_responder wrong iface |
| 34 | Plugin | vehicle_dynamics CAN output |
| 35 | Plugin | vehicle_dynamics ETH output |
| 36 | Plugin | vehicle_dynamics signals |
| 37 | Plugin | network_sim stderr |
| 38 | Plugin | sensor_model stderr |
| 39 | Plugin | ABI version rejection |
| 40 | PDU | Unavailable without plugin |
| 41 | PDU | Configure route |
| 42 | PDU | Send PDU over CAN |
| 43 | PDU | Subscribe to PDUs |
| 44 | PDU | Remove route |
| 45 | PDU | Cyclic transmission |
| 46 | PDU | Ethernet sim-only path |
| 47 | PDU | IP/UDP/IpduM path |
| 50 | PDU Groups | Create group |
| 51 | PDU Groups | Disable blocks traffic |
| 52 | PDU Groups | Enable resumes traffic |
| 53 | PDU Groups | Multi-PDU group |
| 60 | CanTp | Single frame send |
| 61 | CanTp | Multi-frame send |
| 70 | Schedule | Cyclic |
| 71 | Schedule | OnChange |
| 72 | Schedule | Mixed |
| 80 | Replay | Start and stream |
| 81 | Replay | Seek |
| 82 | Replay | Pause and resume |
| 90 | FrameService | CAN send |
| 91 | FrameService | Bus type filter |
| 92 | FrameService | TCP frame |
| 93 | FrameService | PDU frame |
| 100 | Error | Non-existent CAN iface |
| 101 | Error | PDU without plugin |
| 102 | Error | Invalid plugin config |
| 103 | Error | Plugin .so not found |
| 110 | Signal Bus | Publish and subscribe |
| 120 | Multi-Bus | Concurrent CAN + ETH |
| 121 | Multi-Bus | Multiple subscribers |
| 130 | Stress | High-frequency sends |
| 131 | Stress | Extended uptime |

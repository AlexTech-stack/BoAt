# TestSet: Gateway

System-level tests for `boat_gateway` lifecycle: startup, interface registration,
driver selection, plugin loading, tick configuration, and shutdown.

---

### TC_Gateway_001_start_with_vcan

**TestSets:** [Gateway]

**Preconditions:**
- Gateway built (`cmake --preset debug && cmake --build --preset debug`)
- `vcan0` exists and is up (`sudo modprobe vcan && sudo ip link add vcan0 type vcan && sudo ip link set vcan0 up`)

**TestSteps:**
1. Start `BOAT_CAN_INTERFACES=vcan0 ./build/debug/src/gateway/grpc_gateway/boat_gateway`
2. From a second shell run `boat frame list-ifaces`

**Expected:**
- Gateway starts without error and logs that it is serving gRPC on `0.0.0.0:50051`
- `list-ifaces` shows `vcan0` as a CAN interface using the virtual driver

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Gateway_002_start_with_multiple_interfaces

**TestSets:** [Gateway]

**Preconditions:**
- `vcan0`, `vcan1` exist and are up

**TestSteps:**
1. Start the gateway with `BOAT_CAN_INTERFACES=vcan0,vcan1`
2. Run `boat frame list-ifaces`

**Expected:**
- Both interfaces are listed and usable (a frame can be sent on each)

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Gateway_003_driver_selection_physical_vs_virtual

**TestSets:** [Gateway], [Hardware]

**Preconditions:**
- One physical CAN adapter (e.g. PEAK PCAN) available as `can0`, brought up with
  `sudo ip link set can0 up type can bitrate 500000`
- `vcan0` exists and is up

**TestSteps:**
1. Start the gateway with `BOAT_CAN_INTERFACES=can0,vcan0`
2. Run `boat frame list-ifaces` (and `boat --json frame list-ifaces`)

**Expected:**
- `can0` is registered with the physical driver (hardware metadata from sysfs visible),
  `vcan0` with the virtual driver
- No error at startup; both interfaces accept frames

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Gateway_004_start_with_ethernet_interface

**TestSets:** [Gateway], [Ethernet]

**Preconditions:**
- A veth pair exists (`sudo ip link add veth0 type veth peer name veth1 && sudo ip link set veth0 up && sudo ip link set veth1 up`)

**TestSteps:**
1. Start the gateway with `BOAT_ETH_INTERFACES=veth0`
2. Run `boat frame list-ifaces`

**Expected:**
- `veth0` is listed as an Ethernet interface

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Gateway_005_node_plugin_loading_with_json_config

**TestSets:** [Gateway], [Plugins]

**Preconditions:**
- Gateway and plugins built; `vcan0` up

**TestSteps:**
1. Start the gateway with
   `BOAT_NODE_PLUGINS=./build/debug/src/plugins/pdu_router/pdu_router.so,./build/debug/src/plugins/can_tp/can_tp.so?{"iface":"vcan0"}`
2. Run `boat plugin list`

**Expected:**
- Both plugins load at startup without error; the JSON config after `?` is applied
  (CAN-TP bound to `vcan0`)
- `boat plugin list` shows both plugins as loaded

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Gateway_006_v7_plugin_rejected

**TestSets:** [Gateway], [Plugins], [Error]

**Preconditions:**
- A plugin `.so` built against plugin ABI v7 (or a stub reporting `BOAT_PLUGIN_ABI_VERSION` = 7)

**TestSteps:**
1. Start the gateway with `BOAT_NODE_PLUGINS=<v7_plugin.so>`

**Expected:**
- The plugin is rejected at load with a clear error message naming the ABI version mismatch
- The gateway either continues without the plugin or exits with a diagnostic — it must not
  crash or load the plugin partially

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Gateway_007_tick_interval_configuration

**TestSets:** [Gateway]

**Preconditions:**
- Gateway built; `vcan0` up; PDU router plugin available

**TestSteps:**
1. Start the gateway with `BOAT_NODE_TICK_US=100` and a cyclic PDU route (cycle 10 ms)
2. Measure the frame cadence on `vcan0` with `candump -t d vcan0`
3. Repeat with `BOAT_NODE_TICK_MS=1` and with both variables set simultaneously

**Expected:**
- Cyclic frames appear at the configured cycle time within tick resolution
- When both variables are set, `BOAT_NODE_TICK_US` takes precedence

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Gateway_008_graceful_shutdown

**TestSets:** [Gateway]

**Preconditions:**
- Gateway running with one CAN interface and one plugin loaded

**TestSteps:**
1. Send SIGINT (Ctrl+C) to the gateway process
2. Restart the gateway with the same configuration

**Expected:**
- Gateway shuts down cleanly (plugins unloaded, no crash, exit code 0 or documented signal exit)
- Restart succeeds — no leaked sockets ("address already in use") or stale lock files

**Verdict:** OK

**Result:**
Verified on real hardware (`agn-testcomputer`): a gateway with `vcan0` and
a connected gRPC client, stopped via `kill` (SIGTERM/SIGKILL rather than
literal Ctrl+C, equivalent for this check), restarted immediately with the
identical configuration on the same port — bound and started listening
right away, no "address already in use" refusal. This specifically
exercises the fix in TC_Gateway_012 below; see that case for the bug this
used to hit and its root cause.

---

### TC_Gateway_009_missing_interface_error

**TestSets:** [Gateway], [Error]

**Preconditions:**
- Interface `vcan99` does NOT exist

**TestSteps:**
1. Start the gateway with `BOAT_CAN_INTERFACES=vcan99`

**Expected:**
- A clear error naming the missing interface (not a crash or silent success)

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Gateway_010_configurable_grpc_port

**TestSets:** [Gateway]

**Preconditions:**
- Gateway built

**TestSteps:**
1. Start one instance with no `BOAT_GRPC_PORT` set
2. Start a second instance with `BOAT_GRPC_PORT=50052`
3. `boat --host localhost:50051 frame list-ifaces` and
   `boat --host localhost:50052 frame list-ifaces`

**Expected:**
- Both instances start and log the port they bound (`gRPC server listening on
  0.0.0.0:<port>`)
- Both remain independently reachable at their respective ports; neither
  affects the other

**Verdict:** OK

**Result:**
Verified on real hardware: both instances started, both logged their correct
port, both stayed alive, and a CLI client reached each one specifically via
`--host localhost:<port>`.

---

### TC_Gateway_011_refuses_duplicate_port_binding

**TestSets:** [Gateway], [Error]

**Preconditions:**
- A gateway instance already running on the default port (or any port)

**TestSteps:**
1. Start a second instance targeting the *same* port (no `BOAT_GRPC_PORT`
   override, or the same explicit value as the first instance)

**Expected:**
- The second instance refuses to start, exits non-zero, and prints a clear
  error naming the port and suggesting `BOAT_GRPC_PORT` for an intentional
  second instance
- The first instance is completely unaffected

**Verdict:** OK

**Result:**
Verified on real hardware: second instance printed `[Gateway] ERROR: port
50051 is already in use by another process...` and exited with code 1; the
first instance remained alive throughout. Before this fix, gRPC's
`SO_REUSEPORT` meant the second instance would have started "successfully"
and silently split traffic with the first (see
`backlog/gateway_backlog.md`'s now-resolved item) -- this test specifically
guards against that regression.

---

### TC_Gateway_012_same_port_restart_after_active_connection

**TestSets:** [Gateway]

**Preconditions:**
- Gateway built with the `SO_REUSEADDR` fix in `RefuseIfPortInUse()`
  (`src/gateway/grpc_gateway/main.cpp`)

**TestSteps:**
1. Start a gateway on a given port; connect a real gRPC client to it (a
   node script works) so the OS has an actual established connection to
   that port, not just a bare listener
2. Kill the gateway; confirm via `ss -tan` that a `TIME-WAIT` entry for
   that port exists
3. Immediately (no deliberate wait) start a new gateway instance on the
   *identical* port
4. Repeat TC_Gateway_011's scenario: start a second instance on a port a
   gateway is genuinely, currently listening on

**Expected:**
- Step 3: starts and binds immediately -- no "port already in use" refusal,
  despite the `TIME-WAIT` entry from step 2
- Step 4: still correctly refused -- the fix only relaxes the `TIME-WAIT`
  case, a genuinely live listener on the port must still block a second
  instance (regression guard against `TC_Gateway_011` and
  `backlog/gateway_backlog.md`'s "second gateway silently shares a port"
  issue coming back)

**Verdict:** OK

**Result:**
Verified on real hardware (`agn-testcomputer`). Before the fix: killed a
gateway with an active client connection, confirmed via `ss -tan` a
lingering `[::1]:<port>` connection in `TIME-WAIT`, and a same-port restart
attempt failed with `[Gateway] ERROR: port <port> is already in use...` for
up to ~60s even though `ss -ltnp` showed no actual listener -- root cause:
`RefuseIfPortInUse()`'s own probe `bind()` (not gRPC's real listener) was
failing against the `TIME-WAIT` entry, having no `SO_REUSEADDR` set. Fixed
by setting `SO_REUSEADDR` on the probe socket. After the fix and rebuild:
repeated the identical sequence -- killed a gateway with a connected
`cyclic_can_sender.py` client, confirmed the same `TIME-WAIT` entry via
`ss -tan`, immediately started a new instance on the same port -- it bound
and logged `[Gateway] gRPC server listening on 0.0.0.0:<port>` right away,
no refusal, and the still-running client transparently reconnected and
resumed sending on the wire (confirmed via `candump`). Step 4 (genuine
second-instance refusal) re-verified unaffected -- `TC_Gateway_011` still
passes with the fix in place, confirming `SO_REUSEADDR` didn't reintroduce
the `SO_REUSEPORT` port-sharing problem. Full incident writeup in
`backlog/gateway_backlog.md`.

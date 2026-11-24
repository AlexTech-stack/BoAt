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

---

### TC_Gateway_006_outdated_abi_plugin_rejected

**TestSets:** [Gateway], [Plugins], [Error]

**Preconditions:**
- A plugin `.so` built against the *previous* plugin ABI. `src/tests/fixtures/
  stale_abi_plugin.cpp` is exactly that fixture: it reports
  `BOAT_PLUGIN_ABI_VERSION - 1` (v8 while the host requires v9), built as
  `build/<preset>/src/tests/stale_abi_plugin.so`

**TestSteps:**
1. Start the gateway with `BOAT_NODE_PLUGINS=<build>/src/tests/stale_abi_plugin.so`

**Expected:**
- The plugin is rejected at load with a clear error message naming the ABI version mismatch
- The gateway either continues without the plugin or exits with a diagnostic — it must not
  crash or load the plugin partially

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

---

### TC_Gateway_009_missing_interface_error

**TestSets:** [Gateway], [Error]

**Preconditions:**
- Interface `vcan99` does NOT exist

**TestSteps:**
1. Start the gateway with `BOAT_CAN_INTERFACES=vcan99`

**Expected:**
- A clear error naming the missing interface (not a crash or silent success)

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

---

### TC_Gateway_013_effective_config_dump_and_show

**TestSets:** [Gateway], [CLI]

**Preconditions:**
- vcan0 up; no gateway on port 50061

**TestSteps:**
1. Start: `BOAT_GRPC_PORT=50061 BOAT_CAN_INTERFACES=vcan0 BOAT_CONFIG_DUMP=/tmp/eff1.json BOAT_NODE_PLUGINS='<build>/pdu_router.so,<build>/can_tp.so?{"iface":"vcan0"}' boat_gateway`
2. `BOAT_HOST=localhost:50061 boat config show -o /tmp/eff2.json`
3. Stop the gateway, start it again with identical settings, dump to `/tmp/eff3.json`
4. Restart once more with `BOAT_NODE_TICK_US=abc` (a typo) and dump again
5. Restart with `BOAT_CAN_INTERFACES=vcan0,vcan_missing`

**Expected:**
- Step 1: stderr ends with `1 configuration warning(s)` / `TLS not configured; gRPC is serving
  in the clear on all interfaces` and `Effective config written to '/tmp/eff1.json'`
- `/tmp/eff1.json` has `"schema": "boat.gateway.effective_config/v1"`, `"version": "0.2.0"`,
  `grpc.port` 50061, `event_store.queue_capacity` 16384, `buses.can_opened: [true]`, both
  plugins with `"loaded": true` and can_tp's verbatim `{"iface":"vcan0"}` config,
  `tick.interval_ns` 1000000 / `resolved_from: "default"`
- Step 2: `/tmp/eff2.json` is byte-identical to `/tmp/eff1.json`
- Step 3: `cmp /tmp/eff1.json /tmp/eff3.json` reports no difference (no timestamp/hostname)
- Step 4: `warnings` contains `BOAT_NODE_TICK_US='abc' is not a positive integer; ignored`
  and `tick.interval_ns` stays 1000000 -- recorded, not silent
- Step 5: `buses.can` is `["vcan0","vcan_missing"]`, `can_opened` is `[true, false]`, and
  `warnings` contains `CAN interface 'vcan_missing' failed to open`

---

### TC_Gateway_014_tls_and_mtls

**TestSets:** [Gateway], [CLI], [Error]

**Preconditions:**
- A throwaway CA, a gateway cert for `localhost` (SAN `DNS:localhost,IP:127.0.0.1`) and a
  client cert signed by the same CA, e.g.:
  `openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=boat-test-ca -keyout ca.key -out ca.pem`,
  then `openssl req -newkey ... | openssl x509 -req -CA ca.pem -CAkey ca.key ...` for `gw.pem/gw.key`
  and `cl.pem/cl.key`

**TestSteps:**
1. `BOAT_GRPC_PORT=50062 BOAT_TLS_CERT=gw.pem BOAT_TLS_KEY=gw.key BOAT_TLS_CLIENT_CA=ca.pem boat_gateway`
2. `export BOAT_HOST=localhost:50062`; `boat frame list-ifaces` (plaintext)
3. `BOAT_TLS_CA=ca.pem boat frame list-ifaces` (TLS, no client cert)
4. `BOAT_TLS_CA=ca.pem BOAT_TLS_CLIENT_CERT=cl.pem BOAT_TLS_CLIENT_KEY=cl.key boat frame list-ifaces`
5. `BOAT_TLS_CA=ca.pem BOAT_TLS_CLIENT_CERT=cl.pem boat frame list-ifaces` (key missing)
6. Start a gateway with only `BOAT_TLS_CERT` set

**Expected:**
- Step 1: starts; no "TLS not configured" warning
- Steps 2 and 3: refused (`UNAVAILABLE ... Socket closed`), non-zero exit
- Step 4: interface table printed
- Step 5: client-side `TlsConfigError` saying the client cert and key must be set together --
  no silent fallback to plaintext
- Step 6: `[tls] BOAT_TLS_CERT and BOAT_TLS_KEY must be set together; refusing to start`, exit 1

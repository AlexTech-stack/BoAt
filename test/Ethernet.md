# TestSet: Ethernet

System-level tests for Ethernet frame handling: virtual (veth) and physical (raw:)
interfaces, send/subscribe, and capability requirements.

Common precondition: gateway running with `BOAT_ETH_INTERFACES=veth0` where a veth
pair `veth0`/`veth1` exists and is up (unless stated otherwise).

---

### TC_Ethernet_001_send_frame_cli

**TestSets:** [Ethernet], [CLI]

**Preconditions:**
- `tcpdump -i veth1 -x` running (captures what leaves via veth0's peer)

**TestSteps:**
1. `boat frame send --bus-type ethernet --ethertype 0x0800 --dst-ip 10.0.0.1 --data AABB`

**Expected:**
- CLI reports success
- `tcpdump` on the peer interface shows the frame with EtherType 0x0800 and the payload

---

### TC_Ethernet_002_subscribe

**TestSets:** [Ethernet], [CLI]

**Preconditions:**
- `boat frame subscribe --bus-types ethernet` running
- Root/CAP_NET_RAW (raw `AF_PACKET` send, same privilege the replay path needs)

**TestSteps:**
1. Inject a frame from outside the gateway onto `veth1`:
   `sudo python3 test/fixtures/eth/send_raw_frame.py --iface veth1 --src 02:00:00:00:00:01 --dst 02:00:00:00:00:02 --ethertype 0x88B5 --data DEADBEEF`
   (no scapy/tcpreplay dependency; the script builds the Ethernet header and
   sends one frame on a raw socket)

**Expected:**
- The subscriber prints the frame with correct src/dst MAC, EtherType, and payload

---

### TC_Ethernet_003_mixed_subscribe_can_and_eth

**TestSets:** [Ethernet], [CAN], [CLI]

**Preconditions:**
- Gateway running with both `BOAT_CAN_INTERFACES=vcan0` and `BOAT_ETH_INTERFACES=veth0`
- `boat frame subscribe --bus-types can,ethernet` running

**TestSteps:**
1. `cansend vcan0 100#01`
2. Send an Ethernet frame via `boat frame send --bus-type ethernet ...`

**Expected:**
- The single subscriber stream shows both frames, each tagged with its bus type

---

### TC_Ethernet_004_physical_nic_requires_raw_prefix_and_cap

**TestSets:** [Ethernet], [Hardware], [Error]

**Preconditions:**
- A physical NIC `eth0` present; gateway binary WITHOUT `cap_net_raw`

**TestSteps:**
1. Start the gateway with `BOAT_ETH_INTERFACES=raw:eth0` (no setcap applied)
2. Apply `sudo setcap cap_net_raw+ep <gateway binary>` and start again

**Expected:**
- Step 1: a clear "Operation not permitted"-class error mentioning CAP_NET_RAW —
  not a silent failure
- Step 2: gateway starts and registers `eth0`

---

### TC_Ethernet_005_self_sent_flag

**TestSets:** [Ethernet], [Plugins]

**Preconditions:**
- The test metadata plugin declaring only `eth`, logging received frame flags:
  `BOAT_NODE_PLUGINS=<build>/src/tests/frame_meta_plugin.so?{"out":"/tmp/eth_meta.log","buses":"eth"}`

**TestSteps:**
1. Have the plugin publish one Ethernet frame
2. Inspect the flags of the echo the plugin receives back

**Expected:**
- The echo carries `BOAT_ETH_FLAG_SELF_SENT` (0x01)

---

### TC_Ethernet_006_tcp_send_unimplemented

**TestSets:** [Ethernet], [Frame], [Error]

**Preconditions:**
- Gateway running

**TestSteps:**
1. Attempt a raw frame send with bus type TCP via the gRPC `FrameService.SendFrame`
   (e.g. from the Python SDK)

**Expected:**
- The call returns gRPC status `UNIMPLEMENTED` — TCP is driven through the TCP
  plugin's connection API, not raw frame send

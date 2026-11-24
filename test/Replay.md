# TestSet: Replay

System-level tests for both replay paths: direct client-paced CAN replay
(`boat trace replay`) and server-side replay (`boat replay import` +
`start`/`stream`/`pause`/`resume`/`seek`/`stop`), including format support,
filtering, address rewriting, and export.

Common precondition: gateway running with `BOAT_CAN_INTERFACES=vcan0,vcan1` and
`BOAT_ETH_INTERFACES=veth0`; CLI installed; fixtures available under
`test/fixtures/` (`can/`, `eth/`, `mixed/`).

---

### TC_Replay_001_direct_can_replay_blf

**TestSets:** [Replay], [CAN], [CLI]

**Preconditions:**
- `test/fixtures/can/recording.blf` with known frame count N = 432; `candump vcan0` running

**TestSteps:**
1. `boat trace replay test/fixtures/can/recording.blf --buses vcan0`

**Expected:**
- All N CAN frames appear on `vcan0` with IDs/DLC/data matching the file
- Inter-frame timing approximates the original recording (real-time pacing)

---

### TC_Replay_002_direct_channel_mapping

**TestSets:** [Replay], [CAN]

**Preconditions:**
- A trace with frames on channels 1 and 2; `candump vcan0` and `candump vcan1` running

**TestSteps:**
1. `boat trace replay test/fixtures/can/recording.blf --buses vcan0,vcan1`

**Expected:**
- Channel-1 frames appear on `vcan0`, channel-2 frames on `vcan1` (1-based positional
  mapping); with fewer buses than channels the last bus acts as fallback

---

### TC_Replay_003_direct_channel_and_id_filter

**TestSets:** [Replay], [CAN]

**Preconditions:**
- A trace containing multiple channels and IDs including 0x040 and 0x0C0 on channel 4

**TestSteps:**
1. `boat trace replay test/fixtures/can/recording.blf --channel 4 --id 0x040,0x0C0 --buses vcan0`

**Expected:**
- Only frames from channel 4 with IDs 0x040/0x0C0 are replayed; everything else is
  filtered out

---

### TC_Replay_004_direct_speed_control

**TestSets:** [Replay], [CAN]

**Preconditions:**
- A trace spanning ~10 s of bus time

**TestSteps:**
1. Replay with `--speed 2.0`; measure wall-clock duration
2. Replay with `--speed 0.5`; measure
3. Replay with `--speed 0` (max)

**Expected:**
- Durations ≈ 5 s, ≈ 20 s, and "as fast as possible" respectively (per-frame gRPC
  round-trip is the floor for max speed)

---

### TC_Replay_005_direct_loop_mode

**TestSets:** [Replay], [CAN]

**Preconditions:**
- The ~1 s trace `test/fixtures/can/recording_short.blf` (20 frames)

**TestSteps:**
1. `boat trace replay test/fixtures/can/recording_short.blf --loop 100 --buses vcan0`, let it run ≥ 3 iterations, Ctrl+C

**Expected:**
- The file replays repeatedly with ~100 ms gap between the last frame of one run and
  the first of the next; Ctrl+C stops cleanly

---

### TC_Replay_006_direct_rejects_pcap

**TestSets:** [Replay], [Error], [CLI]

**Preconditions:**
- Any `.pcap` file

**TestSteps:**
1. `boat trace replay test/fixtures/eth/capture.pcap --buses vcan0`

**Expected:**
- Immediate rejection with an error pointing to `boat replay import` +
  `start`/`stream`; nothing is sent

---

### TC_Replay_007_direct_pcapng_can_extraction

**TestSets:** [Replay], [CAN], [PCAPNG]

**Preconditions:**
- A mixed `.pcapng` containing CAN and Ethernet records; `candump vcan0` running

**TestSteps:**
1. `boat trace replay test/fixtures/mixed/mixed.pcapng --buses vcan0`

**Expected:**
- Only the CAN/CAN-FD records are replayed; Ethernet records are skipped without error

---

### TC_Replay_008_import_blf_frame_count

**TestSets:** [Replay], [CLI]

**Preconditions:**
- `test/fixtures/can/recording.blf` with known frame count N = 432

**TestSteps:**
1. `boat replay import test/fixtures/can/recording.blf --trace-id demo`

**Expected:**
- Import is accepted; the reported frame count equals N and a size is shown
- The trace is stored on the gateway as `/tmp/demo.trace` (there is no
  list-imported-traces command; `boat trace status` reports recording sessions,
  not imported traces)

---

### TC_Replay_009_server_side_can_stream

**TestSets:** [Replay], [CAN]

**Preconditions:**
- Trace `demo` imported (TC_Replay_008); `candump vcan0` running

**TestSteps:**
1. `boat replay stream --trace demo --buses vcan0`

**Expected:**
- All frames appear on the bus, timing driven by the gateway tick timer (no per-frame
  gRPC overhead); a progress line updates during playback and a summary prints at the end

---

### TC_Replay_010_pause_resume_seek_stop

**TestSets:** [Replay]

**Preconditions:**
- A long trace imported and started (`boat replay start --trace demo --buses vcan0`)

**TestSteps:**
1. `boat replay pause --replay-id trace:demo` — observe `candump`
2. `boat replay resume --replay-id trace:demo`
3. `boat replay seek --replay-id trace:demo --tick 0`, observe
4. `boat replay stop --replay-id trace:demo`
   (`boat replay start` prints the `replay_id` to use — `trace:<trace-id>`)

**Expected:**
- Pause halts bus output; resume continues from the pause point; seek repositions
  playback (frames from the seek target replay again); stop terminates the replay

---

### TC_Replay_011_repeat_without_reimport

**TestSets:** [Replay]

**Preconditions:**
- Trace `demo` imported once

**TestSteps:**
1. `boat replay stream --trace demo --buses vcan0`, wait for completion
2. `boat replay stream --trace demo --buses vcan1`

**Expected:**
- Second replay works without re-importing, onto a different interface — interface
  targeting is a replay-time decision, not baked into the import

---

### TC_Replay_012_eth_pcap_import_global_ip_rewrite

**TestSets:** [Replay], [Ethernet]

**Preconditions:**
- `test/fixtures/eth/capture.pcap` with IPv4 UDP traffic; `tcpdump -i veth1 -n` running

**TestSteps:**
1. `boat replay import test/fixtures/eth/capture.pcap --trace-id eth1 --replay-src-ip 192.168.1.1 --replay-dst-ip 192.168.1.100`
2. `boat replay stream --trace eth1 --eth-iface veth0`

**Expected:**
- Every replayed packet carries src 192.168.1.1 / dst 192.168.1.100
- IP and UDP checksums are valid (tcpdump does not flag bad checksums)
- UDP ports and TTL are preserved from the original capture

---

### TC_Replay_013_eth_ip_map_and_filter

**TestSets:** [Replay], [Ethernet]

**Preconditions:**
- A pcap with (at least) two conversations: 10.10.10.10↔x and 10.10.10.11↔y

**TestSteps:**
1. `boat replay import test/fixtures/eth/capture.pcap --trace-id eth2 --ip-map 10.10.10.10=192.168.0.100,10.10.10.11=192.168.0.101 --ip-filter 192.168.0.100`
2. Stream and capture with tcpdump

**Expected:**
- Only packets involving the rewritten 192.168.0.100 are replayed; the mapping table
  rewrote each conversation to its target; the filter applied AFTER rewriting

---

### TC_Replay_014_eth_ethertype_protocol_port_filters

**TestSets:** [Replay], [Ethernet]

**Preconditions:**
- `test/fixtures/eth/capture_rich.pcap` (IPv4/IPv6, UDP/ICMP/TCP, several UDP ports incl. 30490)

**TestSteps:**
1. Import with `--ethertype ipv4 --protocol udp --dst-port 30490`, stream, capture

**Expected:**
- Only IPv4 UDP packets to port 30490 are replayed; filters apply in the documented
  order (EtherType → protocol → port → IP rewrite → IP filters)

---

### TC_Replay_015_eth_ipv6_and_icmpv6

**TestSets:** [Replay], [Ethernet]

**Preconditions:**
- `test/fixtures/eth/capture_rich.pcap` (IPv6 UDP, ICMPv6, and a packet with a
  Hop-by-Hop extension header)

**TestSteps:**
1. Import with `--ethertype ipv6 --ip-map <orig>=fe80::100,...`, stream, capture

**Expected:**
- IPv6 packets replay with rewritten addresses and recalculated mandatory checksums
  (UDP + ICMPv6 with pseudo-header); extension headers preserved; protocol filter
  matched the resolved L4 protocol behind extension chains

---

### TC_Replay_016_eth_mac_map_playback_time

**TestSets:** [Replay], [Ethernet]

**Preconditions:**
- Trace imported with an IP map (TC_Replay_013)

**TestSteps:**
1. `boat replay stream --trace eth2 --eth-iface veth0 --mac-map 192.168.0.100=02:de:ad:be:ef:01,192.168.0.101=02:de:ad:be:ef:02`
2. Capture with `tcpdump -e`

**Expected:**
- Frames to/from each IP carry the mapped MAC as src/dst respectively
  (direction-aware); unmapped IPs fall back to auto-detected src / broadcast dst

---

### TC_Replay_017_import_mixed_pcapng

**TestSets:** [Replay], [PCAPNG], [CAN], [Ethernet]

**Preconditions:**
- `test/fixtures/mixed/mixed.pcapng` (CAN + Ethernet, known counts: 20 CAN + 10 Ethernet)

**TestSteps:**
1. `boat replay import test/fixtures/mixed/mixed.pcapng --trace-id mix1`
2. `boat replay stream --trace mix1 --buses vcan0 --eth-iface veth0`
3. Observe `candump vcan0` and `tcpdump -i veth1` simultaneously

**Expected:**
- Import reports the full combined frame count
- Playback delivers CAN frames to the CAN bus and Ethernet frames to the Ethernet
  interface, interleaved on the original shared timeline

---

### TC_Replay_018_export_trace_to_pcapng

**TestSets:** [Replay], [PCAPNG], [CLI]

**Preconditions:**
- An imported mixed trace at `/tmp/mix1.trace`

**TestSteps:**
1. `boat replay export /tmp/mix1.trace out.pcapng`
2. Open `out.pcapng` in Wireshark/`tshark -r out.pcapng`
3. Re-import: `boat replay import out.pcapng --trace-id mix2`

**Expected:**
- Export reports CAN/Ethernet/skipped counts; Wireshark shows a SocketCAN interface
  and an Ethernet interface with correctly decoded CAN IDs
- Re-import yields the same CAN+Ethernet frame count (round trip)

---

### TC_Replay_019_export_skips_tcp_pdu

**TestSets:** [Replay], [PCAPNG], [Error]

**Preconditions:**
- A `.trace` containing TCP and/or PDU bus-type frames (e.g. created via the Trace
  Editor) alongside CAN frames

**TestSteps:**
1. `boat replay export test/fixtures/mixed/edited.trace out.pcapng`

**Expected:**
- CAN/Ethernet frames are exported; TCP/PDU frames are skipped and reported in the
  `skipped` count — no crash, no malformed output file

---

### TC_Replay_020_from_events

**TestSets:** [Replay], [Simulation]

**Preconditions:**
- A completed simulation whose events are in the event store

**TestSteps:**
1. `boat replay from-events --sim-id <id>`
2. Repeat with `--signal-id <sig> --tick-min 100 --tick-max 500`

**Expected:**
- Recorded events replay onto the bus; the filtered variant replays only the matching
  signal within the tick window

---

### TC_Replay_021_import_unsupported_format

**TestSets:** [Replay], [Error]

**Preconditions:**
- A file `test/fixtures/eth/notes.txt`

**TestSteps:**
1. `boat replay import test/fixtures/eth/notes.txt --trace-id bad`

**Expected:**
- Clear "unsupported trace format" error listing the supported suffixes
  (.pcap, .pcapng, .asc, .blf); nothing is uploaded

---

### TC_Replay_022_import_corrupt_pcapng

**TestSets:** [Replay], [PCAPNG], [Error]

**Preconditions:**
- A truncated/corrupted `.pcapng` (e.g. valid file with the last 3 bytes removed)

**TestSteps:**
1. `boat replay import test/fixtures/mixed/corrupt.pcapng --trace-id bad`

**Expected:**
- A hard, descriptive error (invalid/truncated pcapng) — not a silent partial import

---

### TC_Replay_023_import_larger_than_4mib

**TestSets:** [Replay], [CLI]

**Preconditions:**
- Gateway running with `BOAT_CAN_INTERFACES=vcan0`
- A large synthetic ASC (200,000 frames at 1 ms, ~12.8 MB), generated with
  `python3 test/fixtures/make_fixtures.py --big`
  (`test/fixtures/can/big.asc`; not committed because of its size)`

**TestSteps:**
1. `boat replay import test/fixtures/can/big.asc --trace-id big_trace`

**Expected:**
- `accepted True`, `frames 200000`, size ~6.2 MB -- above gRPC's 4 MiB unary limit, so this
  exercises the client-streaming `ImportTraceDataStream` path (#82); completes in seconds

---

### TC_Replay_024_trace_id_validation

**TestSets:** [Replay], [Error]

**Preconditions:**
- `test/fixtures/can/small.asc` (47 frames; the first 50 lines of the generated `big.asc`)

**TestSteps:**
1. `boat replay import test/fixtures/can/small.asc --trace-id ../../tmp/evil; echo rc=$?`
2. `boat replay import test/fixtures/can/small.asc --trace-id a/b; echo rc=$?`
3. `boat replay import test/fixtures/can/small.asc --trace-id ok.trace-1; echo rc=$?`
4. `ls /tmp/evil* /tmp/*.trace`

**Expected:**
- Steps 1-2: `ImportTraceDataStream failed: RPC error [INVALID_ARGUMENT]: trace_id must match
  [A-Za-z0-9._-]+ and must not be '.' or '..'`, `rc=1` -- one line, no gRPC repr dump (#62)
- Step 3: accepted, 47 frames
- Step 4: no `/tmp/evil*`; `/tmp/ok.trace-1.trace` exists

---

### TC_Replay_025_pause_resume_no_burst

**TestSets:** [Replay]

**Preconditions:**
- `big_trace` imported (TC_Replay_023); `candump -L vcan0 > dump.txt` running

**TestSteps:**
1. `boat replay start --trace big_trace --buses vcan0` (note the replay_id)
2. After ~2 s: `boat replay pause --replay-id <id>`; wait 3 s
3. `boat replay resume --replay-id <id>`; after ~2 s `boat replay stop --replay-id <id>`
4. In `dump.txt`, count frames in the pause window and the densest 10 ms window after resume

**Expected:**
- No frames during the pause (a ~3 s gap in the timestamps)
- After resume, the rate returns to ~1 frame/ms -- roughly 10-12 frames in any 10 ms window,
  never a burst of the ~3,000 records that came due during the pause (#77)

---

### TC_Replay_026_unknown_ids_clean_error

**TestSets:** [Replay], [CLI], [Error]

**Preconditions:**
- Gateway running

**TestSteps:**
1. `boat replay start --trace no_such_trace; echo rc=$?`
2. `boat replay seek --tick 5 --replay-id nope; echo rc=$?`
3. `boat replay pause --replay-id nope`, `resume`, `stop` likewise

**Expected:**
- Step 1: `RPC error [NOT_FOUND]: trace id not found`, `rc=1`
- Steps 2-3: `RPC error [NOT_FOUND]: replay not found`, `rc=1`
- No Python traceback in any step (they used to print one before v0.2.0)

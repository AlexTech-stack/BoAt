# System-test fixtures

Inputs the manual system tests in [`../`](../) refer to: CAN recordings,
Ethernet captures, mixed pcapng/trace files, PDU databases, DBCs, scenarios,
and the test-only plugins/scripts they need.

Everything here is synthetic and deterministic. Nothing is a captured
production trace; the PCAP/PDU/DBC data is generated in
[`make_fixtures.py`](make_fixtures.py), and the CAN files use python-can.

`manifest.json` is the machine-readable list: per-file frame counts and the
`TC_` ids that use it.

## Regenerate

```bash
# everything except the 12.8 MB big.asc
python3 test/fixtures/make_fixtures.py

# also generate big.asc (TC_Replay_023/025)
python3 test/fixtures/make_fixtures.py --big

# report existing CAN frame counts
python3 test/fixtures/make_fixtures.py --check
```

Requires `python-can` (already a CLI/SDK dependency). The pcapng and `.trace`
writers come from `boat-platform/sdk/python`.

## Layout

| Path | What it is |
|---|---|
| `can/recording.blf` | 432 frames, 4 channels, ~10 s; channel 4 carries `0x040`/`0x0C0`; one 29-bit and one CAN-FD frame |
| `can/recording_short.blf` | 20 frames, ~1 s (loop test) |
| `can/small.asc` | 47 frames (TC_Replay_024 trace-id validation) |
| `can/big.asc` | generated on demand, 200,000 frames (TC_Replay_023/025) |
| `eth/capture.pcap` | IPv4 UDP only, two conversations, ports incl. 30490 (global rewrite / ip-map tests) |
| `eth/capture_rich.pcap` | IPv4+IPv6, UDP/TCP/ICMPv6, VLAN, DoIP, SOME/IP, AUTOSAR PDU-mux, IPv6 extension header |
| `eth/eth_inject.pcap` | one frame for external injection |
| `eth/notes.txt` | not a trace (unsupported-suffix error case) |
| `mixed/mixed.pcapng` | 20 CAN + 10 Ethernet records, one timeline |
| `mixed/corrupt.pcapng` | `mixed.pcapng` truncated by 3 bytes |
| `mixed/edited.trace` | 3 CAN + 2 Ethernet + 1 TCP + 1 PDU native records |
| `pdu_db/pdu_db_mixed_signals.json` | one message with an Intel + a Motorola scaled signal |
| `dbc/sample.dbc` | Intel + Motorola, mux, `VAL_`, `CM_`, `GenMsgCycleTime`, scaled min/max |
| `scenarios/` | `example_b.json`, `scenario_traffic.json`, and the two invalid scenarios |
| `scripts/determinism_probe.py` | seeded signal-event determinism probe |
| `eth/send_raw_frame.py` | no-scapy raw Ethernet sender |
| `tls/gen_tls_certs.sh` | generates the throwaway CA / gateway / client certs |

## Test-only plugins

The scenarios and some cases reference plugins that must be **built** first
(they are not shipped plugins):

```bash
cd boat-platform
cmake --preset debug && cmake --build --preset debug
# -> build/debug/src/tests/traffic_plugin.so
# -> build/debug/src/tests/frame_meta_plugin.so
# -> build/debug/src/tests/stale_abi_plugin.so
```

`scenarios/scenario_traffic.json` references
`build/debug/src/tests/traffic_plugin.so` **relative to the gateway's working
directory**. Start the gateway from `boat-platform/` (as the other test steps
do), or edit that path for your build preset.

- `traffic_plugin` publishes one CAN frame every `period_ticks` (config:
  `iface`, `can_id`, `period_ticks`, `payload`, `bus_type`).
- `frame_meta_plugin` logs the iface/flags/can_id of every frame it sees;
  `"buses"` selects what it declares (`"can"`, `"eth"`, or both).
- `stale_abi_plugin` reports `BOAT_PLUGIN_ABI_VERSION - 1`.

## TLS

Certificates are generated, not committed (they expire):

```bash
./test/fixtures/tls/gen_tls_certs.sh
# -> test/fixtures/tls/{ca,gw,cl}.pem + .key
```

Generated certs/keys are gitignored.

## Intentional gaps

`TC_PDU_010` covers E2E **metadata** only: the platform does not compute a
per-frame rolling counter or CRC. See
[`backlog/pdu_e2e.md`](../../backlog/pdu_e2e.md).

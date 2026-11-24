# BoAt System TestSuite

Top-level index of the system-level TestSuite. Structure, naming, template, and
verdict semantics are defined in [Structure.md](Structure.md). All tests operate
from the **user's perspective** — CLI, gRPC via SDK, web UIs, tools, and the wire
(`candump`/`tcpdump`) — against a running platform, with no internal mocking.

## TestSets with their own file

| TestSet | File | Cases | Covers |
|---|---|---:|---|
| [Gateway] | [Gateway.md](Gateway.md) | 14 | Startup, interface registration, driver selection, plugin loading, tick config, shutdown, configurable/duplicate gRPC port, same-port restart after active connection, effective-config dump/`config show` (byte-identical across runs, recorded warnings), TLS/mTLS |
| [CAN] | [CAN.md](CAN.md) | 11 | CAN / CAN FD / extended-ID send+receive, loopback flag, bus isolation, PDU-database one-shot send, burst, TX failure reported (#66) |
| [Ethernet] | [Ethernet.md](Ethernet.md) | 6 | Ethernet send/subscribe, veth + raw: physical NICs, capabilities, TCP-send rejection |
| [Simulation] | [Simulation.md](Simulation.md) | 12 | Lifecycle (create/start/pause/step/stop/list/watch), determinism (seed reproducibility), fault inject/list + input validation, one live session (FAILED_PRECONDITION), reset rewinds the tick |
| [Scenario] | [Scenario.md](Scenario.md) | 5 | Create/get/list/delete/validate, AI-generated scenarios |
| [Replay] | [Replay.md](Replay.md) | 26 | Direct + server-side replay, all formats, filters, IP/MAC rewriting, pcapng export, from-events, >4 MiB streamed import, trace-id validation, no burst after pause/resume, clean errors for unknown ids |
| [Recording] | [Recording.md](Recording.md) | 9 | CLI + Recorder UI recording in ASC/BLF/PCAP/PCAPNG, signal sidecar, record→replay round trip |
| [PDU] | [PDU.md](PDU.md) | 10 | Routes, cyclic schedules, I-PDU groups, signal packing, E2E, db inspection, plugin delegation |
| [CanTp] | [CanTp.md](CanTp.md) | 17 | ISO 15765-2 sessions, single/multi-frame, flow control, N_Bs/N_Cr timeouts, extended/mixed/29-bit addressing, CAN FD BRS, padding, error/event reporting, always-on reception, 4095-byte FF_DL limit, malformed Flow Control (INVALID_FS / ignored short FC / WFT_OVRN) |
| [Plugins] | [Plugins.md](Plugins.md) | 7 | Register/list/info/unload, JSON config, bus-type filtering, dual managers, replay delivery |
| [WebUIs] | [WebUIs.md](WebUIs.md) | 14 | Launcher, Dashboard, Nodes, Commander, Recorder UI, Debug inspector, nav, gateway-down behavior, node gateway-restart resilience, plugin-based nodes, loopback bind by default + path confinement, dashboard metrics panel |
| [Tools] | [Tools.md](Tools.md) | 19 | PDU Editor, Trace Analyzer, Trace Editor, Eth Analyzer, dbc2boatjson, offline operation, dbc2boatjson Min/Max passed through unscaled |
| [CLI] | [CLI.md](CLI.md) | 8 | Global flags, JSON mode, help accuracy, failure behavior, test runner, AI assistants, BOAT_HOST env var |
| [LauncherAgent] | [LauncherAgent.md](LauncherAgent.md) | 12 | Multi-instance gateway lifecycle REST API: port auto-allocation, independent instances, duplicate-port rejection, delete-while-running rejection, host info, edit-in-place via PUT, invocation-independent path resolution, external (unmanaged) gateway discovery, node lifecycle, loopback bind by default (BREAKING), adopt an external gateway, stopped definitions persist (0600) |
| [AdminGui] | [AdminGui.md](AdminGui.md) | 24 | PySide6 desktop client: host polling, create/start/stop via real UI actions, delete-refused-while-running dialog flow, multi-host aggregation, Interfaces/Plugins columns, New Instance dropdown pickers, Edit instance, equivalent command-line panel, paste-to-fill, Managed column + external-gateway guard, stale-selection-cleared-on-rebuild, session save/load, Nodes tab, Nodes target-gateway dropdown + paste-to-fill, cross-host target-gateway resolution, node dynamic argument fields, plugin config schema fields, Test Runs tab, test-run report viewer, Interfaces tab, dark-theme sidebar, CAN config column, node tick fields, Adopt… for external gateways |
| [Release] | [Release.md](Release.md) | 7 | Shipped artifacts: `.deb`/`.rpm`/`.tar.gz` install and contents (amd64 + arm64), older-glibc tarball, multi-arch container image and `:latest` handling, out-of-tree plugin build against the installed header, version consistency |

**Total: 201 TestCases.**

## Cross-cutting TestSets (no own file)

These sets exist only as `[tags]` on TestCases defined in the files above:

| TestSet | Meaning | Index |
|---|---|---|
| [Error] | Error handling & edge cases | [ErrorHandling.md](ErrorHandling.md) |
| [Determinism] | Seed-reproducibility guarantees | TC_Simulation_003/006/007 |
| [PCAPNG] | Mixed CAN+Ethernet capture format | TC_Replay_007/017/018/019/022, TC_Recording_001/008, TC_Tools_009/012/016 |
| [Frame] | Unified FrameService semantics | TC_Ethernet_006, TC_PDU_009 |
| [Hardware] | Requires physical CAN/Ethernet hardware | TC_Gateway_003, TC_Ethernet_004, TC_Release_004 (aarch64 host) |
| [Performance] | Load/throughput behavior | TC_CAN_010 |
| [Timeout] | N_Bs/N_Cr watchdog behavior | TC_CanTp_006/007/017 |
| [AI] | LLM-assistant features | TC_Scenario_005, TC_CLI_007 |
| [TraceAnalysis] | Analyzer functionality | TC_Tools_005–009, TC_Tools_014–016 |
| [TraceEditor] | Trace Editor functionality | TC_Tools_010–013 |

## Recording results

A **campaign** is one run of the suite against a specific build/host, stored as
`test/campaigns/<date>-<name>.yaml`. It holds one entry per TestCase id:

```yaml
campaign: 2026-11-24-baseline
date: 2026-11-24
results:
  - id: TC_CAN_001_send_frame_cli
    verdict: OK
    notes: "Frame seen in candump; DLC 4, data AA BB CC DD."
```

- `verdict` is one of `OK` / `NOK` / `INCONCLUSIVE` / `NOT_TESTED` (see
  [Structure.md](Structure.md#verdicts)).
- `notes` is free text: observation, evidence, issue references.
- TestSet definitions never carry a verdict. Recording a run means committing a new
  campaign file, so history lives in git instead of being overwritten in place.
- `tools/test_report.py` validates that every TestCase has exactly one result and
  renders the status/sign-off summary from definitions + a campaign. Step-by-step
  workflow: [tools/TEST_REPORT_HOWTO.md](../tools/TEST_REPORT_HOWTO.md).
- `test/campaigns/2026-11-24-baseline.yaml` is the imported pre-overhaul state and is
  the reference for the file shape.

## Executing the suite

1. Work through the TestSets in the file order above — Gateway first (everything else
   depends on a healthy gateway), Tools last (they are independent of most failures).
2. Run [Release] on clean, separate hosts (VMs or containers) against the downloaded
   release artifacts, not a build tree -- it is the only set that tests what users install.
3. If a precondition cannot be met (e.g. no physical hardware for [Hardware] cases),
   set `NOT_TESTED` with the reason in `notes` — do not skip silently.

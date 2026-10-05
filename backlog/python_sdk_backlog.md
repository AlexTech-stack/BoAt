# Python SDK (`boat-py`) — remaining audit findings

From a full audit of `boat-platform/sdk/python` (2026-10-05). The verified **correctness**
bugs were fixed on `sdk_rework`; what follows is everything that audit turned up and did not
fix. Signal-packing defects live in `com_signal_backlog.md` instead.

---

## #1 — `BoAtClient` has no TLS support  🔴

The gateway supports opt-in TLS and mTLS (`BOAT_TLS_CERT` + `BOAT_TLS_KEY`, plus
`BOAT_TLS_CLIENT_CA` for client certs), but `sdk/python/boat/client.py` builds
`grpc.insecure_channel` unconditionally. **A TLS-enabled gateway is unreachable from the SDK
and therefore from the CLI** — a shipped gateway feature with no client. `trace_replay.py`
opens its own `grpc.insecure_channel` directly too, so it needs the same treatment.

## #2 — The unified-frame migration stopped at `frame_node.py`  🟠

Inside the SDK there are 13 legacy per-bus RPC call sites against 2 unified ones:

| Module | Service |
|---|---|
| `boat/frame_node.py` | `FrameService` |
| `boat/can_node.py`, `boat/ethernet_node.py` | `CanService`/`EthernetService` — deprecated, warns |
| `boat/test/bus.py` (7 sites) | `CanService`/`EthernetService` — **not** deprecated, no warning |
| `boat/cli.py:66`, `boat/cmd.py:108`, `boat/cmd.py:178` | `CanService`/`EthernetService` |

The one that matters most is `boat/test/bus.py`: `TestCanBus`/`TestEthBus` are the bus layer
the automated HIL runner (`boat test run`) drives, so the test framework exercises the path
`CanNode` is deprecated *in favour of*, not the `FrameSink` path. `nodes/` was migrated; the
harness was not.

Also unmigrated and still teaching the deprecated classes: 5 of 7 files in
`sdk/python/boat/examples/`, both `demo/` nodes, and
`scripts/listen_on_vcan0_for_id_0x300_as_soon_as_plugin.py`.

## #3 — `boat ai` cannot generate current code  🟠

`cli/boat_cli/plugin_context.py:19-80` builds the LLM system prompt listing only
`CanNode`/`BusNode`/`EthernetNode`/`PduNode` and instructs *"use ONLY what is listed here."*
`FrameNode` is absent, so every generated plugin uses a deprecated class and emits
`DeprecationWarning` at runtime. Moot if the feature is replaced by a BoAt MCP server, as
planned — recorded so the replacement does not inherit the same omission.

## #4 — `python-can` is an undeclared dependency  🟠

`boat/trace_analyzer.py:182` does a bare `import can`; only `boat-cli` declares the dep.
`pip install boat-py` followed by `TraceAnalyzer("x.blf")` raises a raw `ModuleNotFoundError`.
`boat/trace_replay.py:932` guards the same import with a clear message — do that in
`trace_analyzer` too and add a `[trace]` extra.

## #5 — `FrameNode.send_tcp` can never succeed  🟡

`boat/frame_node.py:106` builds a `bus_type=TCP` frame, but
`FrameService.SendFrame` returns `UNIMPLEMENTED` for TCP by design
(`frame_service_impl.cpp:216`, and `FrameSink::Send` returns false for `kTcp`). Zero callers
in the tree. It also discards a healthy gRPC channel via `_reconnect()` on the way out.
Remove it, or make it raise `NotImplementedError` naming the TCP plugin.

Related asymmetry: there is no `send_pdu`, even though a PDU `SendFrame` *is* valid
(dispatched to `pdu_router`). Only TCP is rejected.

## #6 — `grpcio-tools` is a runtime dependency  🟡

`sdk/python/pyproject.toml:18`. It is codegen-only (`stubs/generate_stubs.sh`) and drags a
protoc toolchain into every install. Belongs in `dev`.

## #7 — Error contracts differ across node classes  🟡

`FrameNode.send` re-raises; `CanNode`/`EthernetNode`/`BusNode`/`PduNode` swallow
`grpc.RpcError` and return `False`. Callers cannot write one handler for "send failed".

Likewise only `FrameNode` has the reconnect/backoff fix: `BusNode` and `PduNode` still do
`except grpc.RpcError: pass` in `run()` — the silent-stream-death bug whose fix is documented
at length in `frame_node.py:50-64`. `BusNode` is not deprecated, so that is a live path.

And `run()` closes the shared client in `finally` (`can_node.py:124`,
`ethernet_node.py:140`, `bus_node.py:121`, `pdu_node.py:305`), so after `run()` returns the
node's `send()` fails forever and `run()` cannot be re-entered. `FrameNode` does not do this.

## #8 — Silent failure paths in the test harness  🟡

`boat/test/harness.py` `_TraceManager` uses bare `except Exception: pass` at lines 234, 253,
289, 291. If the recorder daemon is down, no traces are recorded, the report lists none, and
nothing reaches stderr. Same family as the `BOAT_NODE_TICK_US` silent fallback that
`EffectiveConfig` was introduced to kill.

`boat/test/config.py`: `BusConfig.type` is never validated against known values, so a typo
like `"vritual"` falls through every filter — the bus never reaches `BOAT_CAN_INTERFACES` and
`check_environment` skips it.

## #9 — `TestEthBus` is not at parity with `TestCanBus`  🟡

No `.pdu`, no `send_signal`/`expect_signal`, no payload/mask matching.
`harness.can_bus()` injects `pdu`; `eth_bus()` does not.

## #10 — Packaging and typing gaps  🟡

- No `py.typed`, so the SDK's extensive annotations are invisible to consumers' type checkers.
- No `__version__`; `boat/__init__.py` is a single blank line with no SPDX header and no
  exports, so `from boat import BoAtClient` does not work. (`boat/test/__init__.py` next to
  it has all three.)
- No README under `sdk/python/`.
- `stubs/generate_stubs.sh` hardcodes 18 proto paths. In sync today — regenerating and
  diffing produced byte-identical output — but a 19th proto would be silently skipped. Glob
  it.

## #11 — No wrapper for 10 of 16 gRPC services  🟡

`simulation`, `replay`, `trace`, `debug`, `plugin`, `node_plugin`, `metrics`, `fault`,
`signal`, `scenario` are raw stubs only. The only simulation wrapper is `_SimControl` in
`harness.py` — private, test-only, and missing `Reset`/`GetState`/`Watch`/`List`. A public
`SimulationNode` is the obvious gap: `boat sim start|pause|step|stop` has no SDK equivalent.
`DebugService.GetEffectiveConfig` likewise — the CLI can diff effective config, Python cannot.

## #12 — No tests for any node class  🟡

`frame_node`, `pdu_node`, `can_tp`, `bus_node`, `pdu_message_node` and `trace_recorder` have
no test file; `boat/test/*` is fully covered. The reconnect/backoff logic in `frame_node.py`,
which carries 30 lines of rationale, is untested.

## #13 — Unused imports  🟢

`frame_node.py:32` `frame_pb2_grpc`; `cli.py:46` `can_pb2_grpc`, `pdu_pb2_grpc`;
`cmd.py` `sys`; `message.py` `math`, `Any`; `pdu_db.py` `Any`; `test/config.py:9` `datetime`,
`timezone`; `test/harness.py` `BusConfig`, `StimulusRecord`; `test/pdu.py` `Any`;
`test/runner.py:20` `MetaInfo`, `ExecutionInfo`, `PreconditionRecord`;
`trace_analyzer.py:29` `Dict`, `List`, `Optional`, `Tuple`;
`trace_reverse_engineer.py:30` `Callable`, `Optional`.

(`SearchMask` at `trace_reverse_engineer.py:35` *is* used, in a quoted annotation at :471.)

## #14 — Minor API warts  🟢

- `PduHelper.get_can_id` and `PduHelper.lookup_can_id` are duplicates; both public.
- `pdu_db.by_name()` returns `None` for a miss while `names()`/`messages()`/`signal_routes()`
  return lists.
- `CanTpHandle.configure(**kwargs)` silently drops unknown keys — a typo'd `st_min_ms=`
  vanishes.
- `CanTpHandle` is the only handle class not named `*Node` and not taking `address=`.
- `frame_node.py:36` still says "the v8 FrameService"; `ethernet_node.py` has the runtime
  deprecation warning but no `.. deprecated::` docstring marker that `can_node.py` has.
- `control.proto` generates Python stubs that nothing imports — the UDS/iceoryx2 IPC control
  transport is C++-only. Probably intentional; noted because the stubs ship regardless.

## #15 — Tests write SQLite files into the source tree  🟢

`boat_config.db`, `boat_events.db` and `boat_traces.db` appear in `sdk/python/`. All
gitignored and untracked, so not a repo problem, but they should go to `tmp_path`.

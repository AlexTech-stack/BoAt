# Python SDK (`boat-py`) — remaining audit findings

From a full audit of `boat-platform/sdk/python` (2026-10-05). The verified **correctness**
bugs were fixed on `sdk_rework`, as were #1, #4, #5 and #6 below (struck through); what follows is everything that audit turned up and did not
fix. Signal-packing defects live in `com_signal_backlog.md` instead.

---

## #1 — ~~`BoAtClient` has no TLS support~~  ✅ fixed 2026-10-05

Client-side TLS added in `boat/client.py`: `TlsConfig` + `make_channel()`, configured by
`BOAT_TLS_CA` / `BOAT_TLS` / `BOAT_TLS_CLIENT_CERT` + `BOAT_TLS_CLIENT_KEY` /
`BOAT_TLS_SERVER_NAME`. All three channel sites (`BoAtClient`, `trace_replay._get_stub`,
`harness._wait_for_ready`) route through the one helper; the `boat` CLI inherits it via
`BoAtClient(address=host)`. Misconfiguration raises `TlsConfigError` instead of degrading to
plaintext.

Verified end-to-end against a real gateway on both `BOAT_TLS_CERT`/`BOAT_TLS_KEY` and
`+BOAT_TLS_CLIENT_CA` (mTLS): CA-only reaches a TLS gateway, a client cert is accepted by an
mTLS gateway and its absence is rejected, and `BOAT_TLS=1` correctly *fails* against a
private CA rather than skipping verification.

Still open: `src/tests/hw_eth_test.py:59` builds its own `grpc.insecure_channel` — a manual
hardware test script, not SDK code, and it hardcodes its gateway address too.

## #2 — The unified-frame migration stopped at `frame_node.py`  ✅ DONE

**Closed 2026-10-06** on `frame_migration`. `CanService` and `EthernetService` no longer
exist: protos, C++ implementations, gateway registrations, the generated Python stubs, and
the deprecated `CanNode` / `EthernetNode` classes are all deleted. `proto/boat/v1/` is now
16 files declaring 14 services.

The count in the original entry (13 sites) was SDK-only and wrong by a factor of three --
the real figure was ~42, with 22 of them in the untested `ui/` services and 18 being
`ListBuses`/`ListInterfaces` calls that had no unified equivalent at all.

Two gateway-side gaps had to be closed first:

- **`FrameService.ListInterfaces`** was added, because `boat frame list-ifaces` -- a
  *unified* CLI verb -- was built on the two legacy listing RPCs. It returns one repeated
  `InterfaceInfo` with a `Frame.BusType` discriminator plus the CAN metadata `CanBusInfo`
  carried.
- **`SubscribeFrames` now validates `iface_filter`**, returning `NOT_FOUND` instead of an
  empty stream. Without that, migrating the test harness would have turned a typo'd
  interface in `config/tests/env_*.json` from a clear error into a silent timeout.

Found and fixed in passing, all pre-existing and all the same shape (an exception
swallowed by a bare `except`):

- `commander` `/api/can/buses`, `recorder` `/api/can-buses` and `system_dashboard`'s
  topology read `resp.ifaces` from a `ListBusesResponse` whose field is `buses`. All three
  had always returned an empty list; `system_dashboard`'s `connected` flag was permanently
  False.
- `debug`'s `/api/gateway/health` had a `return` inside a `finally`, which discards the
  pending success return -- it reported the gateway down even when up.
- `boat ai`'s generated-code validator called `on_frame(frame, iface)`, the old per-bus
  signature, so it rejected valid `FrameNode` code outright.

`ui/` gained its first tests (`ui/tests/`, 16 of them); 15 fail against the pre-migration
code. `CLAUDE.md`'s documented pytest command now includes them.

## #3 — `boat ai` cannot generate current code  🟠

`cli/boat_cli/plugin_context.py:19-80` builds the LLM system prompt listing only
`CanNode`/`BusNode`/`EthernetNode`/`PduNode` and instructs *"use ONLY what is listed here."*
`FrameNode` is absent, so every generated plugin uses a deprecated class and emits
`DeprecationWarning` at runtime. Moot if the feature is replaced by a BoAt MCP server, as
planned — recorded so the replacement does not inherit the same omission.

## #4 — ~~`python-can` is an undeclared dependency~~  ✅ fixed 2026-10-05

Declared as a `[trace]` extra, and `trace_analyzer._read_python_can` now guards the import
the way `trace_replay.py:932` already did, naming `pip install 'boat-py[trace]'`.

## #5 — ~~`FrameNode.send_tcp` can never succeed~~  ✅ fixed 2026-10-05

Removed, with a comment in its place pointing at the `tcp.so` plugin's connection API and at
why `FrameService.SendFrame` rejects TCP.

Still open, and deliberately not changed: there is no `send_pdu`, even though a PDU
`SendFrame` *is* valid (dispatched to `pdu_router`). Only TCP is rejected, so the asymmetry
in the helper set is now the only one left.

## #6 — ~~`grpcio-tools` is a runtime dependency~~  ✅ fixed 2026-10-05

Moved to the `dev` extra. Runtime deps are now `grpcio` + `protobuf` only.

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

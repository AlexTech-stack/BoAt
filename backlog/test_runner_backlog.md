# `boat test` (automated TestSuiteRunner) Backlog

Not to be confused with `test/*.md` -- the manual, hand-verified TestSuite
(TestSuite → TestSet → TestCase, `test/Structure.md`) tracking real-hardware
verification for releases. This file is about the *other* thing also
called "TestSuite" in this codebase: `boat test run <manifest.json>`, a
CI-style automated runner (`boat-platform/cli/boat_cli/test.py` +
`boat-platform/sdk/python/boat/test/`) that spins up a gateway, runs test
files as subprocesses against it, and generates JSON/JUnit/HTML/Allure
reports. Manual TestCase verdicts (e.g. `TC_CLI_006_system_test_runner`)
are explicitly **not** updated by anything in this file, per the user:
those are done manually, deliberately, mainly for releases.

---

## Done (2026-08-17) — first real end-to-end run; two real bugs found and fixed

User: "There is a TestSuite functionality in the Boat framework. Lets
evaluate if we can include it here [admin_gui]." Investigation found the
framework fully implemented (CLI, runner, harness, reporting) but, as far
as this repo's own records show, **never actually executed** --
`TC_CLI_006` was `NOT_TESTED`, and no example manifest existed anywhere
in the repo (only environment configs). Before designing any admin_gui
integration, decided to actually run it for real on hardware first --
building a UI around a code path nobody had confirmed worked would have
been the wrong order.

User supplied the real test, using their own hardware setup directly:
"can0 and can1 are on one bus, so we could 'test' it as it is a gateway
for can messages. With a 1-to-1 routing test for payload (shall not
change), routingtime (less than 1ms), and routingbehaviour (one message
in = one message out; no message drops or similar)." Confirmed via
`cansend`/`candump` that can0/can1 are genuinely bridged at the
transceiver level (a frame sent on one appears on the other with no
software involvement) before building anything.

### New fixtures (`boat-platform/config/tests/`)

- **`env_can_loopback.json`** -- physical `can0`/`can1` environment,
  harness-spawned gateway on `localhost:50067` (a scratch port, to avoid
  colliding with anything else running).
- **`can_loopback_routing_test.py`** -- the actual test: injects N
  sequence-tagged frames via the gateway's `FrameService.SendFrame` on
  `can0` (so the gateway's own API is genuinely exercised, not bypassed),
  and measures routing time as `TS_message_on_can1 - TS_message_on_can0`
  with **both** timestamps taken from raw SocketCAN reads (`python-can`)
  -- `can0` has `CAN_RAW_LOOPBACK` enabled (verified with a real
  `candump`/`cansend` round trip, not just assumed from the interface's
  "ECHO" flag), so the gateway's own outbound write is independently
  observable there with a genuine kernel timestamp too, deliberately
  excluding the gRPC client call's own latency from the calculation
  entirely -- see "Latency methodology, resolved" below for why. Checks
  payload equality, routing time, and exactly-once delivery (no drops).
  Exit code 0/1 matches `TestSuiteRunner`'s subprocess contract.
- **`manifest_can_loopback.json`** -- one test entry wiring the two
  together.

### Bug 1 — `check_environment()`'s physical-CAN driver check was always wrong

`_check_can_interfaces()` read `<iface>/device/driver` (a **symlink to a
directory** -- the driver's own sysfs entry, e.g.
`/sys/bus/usb/drivers/peak_usb`) via `_read_sysfs()`, which does
`open(path).read()`. `open()` on a symlink to a directory raises
`IsADirectoryError` (an `OSError` subclass), silently swallowed by
`_read_sysfs()`'s broad `except OSError: return None` -- so this check
reported "no driver detected" for **every** physical CAN interface,
unconditionally, regardless of whether a driver was genuinely bound.
Confirmed directly on real hardware (a PEAK-System USB-CAN dongle,
driver `peak_usb`, correctly bound and `up`) before fixing. Fixed with a
new `_read_driver_link()` using `os.readlink()` (correct for a symlink)
instead of `open().read()` (correct only for plain sysfs attribute files
like `operstate`, which `_read_sysfs()` still handles fine). Added two
unit tests (`test_test_check.py`) exercising the physical-driver path
specifically -- previously untested entirely, which is exactly how this
went unnoticed.

### Bug 2 — spawned gateway ignored the environment's own port and tick

`_GatewayManager.start()` (`harness.py`) never set `BOAT_GRPC_PORT` (or
`BOAT_NODE_TICK_MS`) on the spawned gateway process -- only
`BOAT_CAN_INTERFACES`/`BOAT_ETH_INTERFACES`. Every existing example
config (`env_virtual.json`, `env_physical.json`, `env_hybrid.json`) uses
`"address": "localhost:50051"`, the gateway's own hardcoded default, so
the gap was invisible until a config used a different port
(`env_can_loopback.json`'s `localhost:50067`): the harness spawned a
gateway that (correctly, given no port was ever passed to it) came up on
50051, while `_wait_for_ready()` polled 50067 and timed out after 15s
with a generic "not ready" error -- no indication anywhere that the
gateway had, in fact, started successfully, just on the wrong port.
Manually reproducing the exact spawn (same env, by hand) was the only way
to find this. Fixed by parsing the port out of `gateway.address` and
setting `BOAT_GRPC_PORT`, and applying `gateway.tick_ms` via
`BOAT_NODE_TICK_MS` (present in every environment config's schema but
previously never actually applied either).

Also improved `_wait_for_ready()`'s failure diagnostics while in there: it
now checks whether the process has already exited and, if so, raises
immediately with the exit code and a tail of its stderr, instead of
burning the rest of the timeout on a channel probe that could never
succeed and then reporting a generic "not ready" with no hint why. (This
specific improvement wouldn't have caught Bug 2 on its own -- that
gateway process was alive the whole time, just on the wrong port -- but
it directly addresses the general diagnostic gap that made Bug 2 harder
to find than it should have been.)

### Verified on real hardware (`agn-testcomputer`)

After both fixes: `boat test check-env` clean (no false driver-missing
issue); `boat test run config/tests/manifest_can_loopback.json` ran the
full pipeline for the first time ever in this repo -- preflight, gateway
spawn on the *correct* port with the *correct* tick, the test subprocess
executed with a real timeout, and all four report formats generated
(`report.json`, `report.junit.xml`, `report.html`, stdout/stderr capture)
in a per-test timestamped folder. Unit tests (`test_test_check.py`,
`test_test_runner.py`) all pass, including the two new ones.

**First result (v1 design, superseded)**: 100/100 frames received, zero
drops, every payload matched exactly -- both the "payload shall not
change" and "no message drops" requirements met cleanly from the start.
The "under 1ms" requirement was **not** met as originally measured
(`t_send = time.time()` immediately before the gRPC call, vs. the
receive-side kernel timestamp): 1.24-32.7ms per frame (mean ~2.5ms).
Isolated the cause with a follow-up diagnostic (throwaway, not
committed): calling `send_can()` alone in a tight loop, with no CAN
reception involved at all, already costs 1.2-3.6ms per call (mean
~2.9ms) -- essentially the *entire* originally-measured "routing time"
was Python/gRPC client-side call overhead on the sending side, not the
gateway's internal dispatch.

### Latency methodology, resolved

Asked the user directly rather than guessing which was intended. Answer:
"What we are testing is the routing time of our fictitious Gateway DUT,
it routes everything from can0 to can1 and vice versa... `TS_Message_on_
can1 - TS_Message_on_can0`[,] is it possible to remove the gRPC time from
this calculation... the performance (of the boat gw) is not so relevant
for this show test case[,] the latency can also be chosen bigger e.g. 5ms
as long as the approach is correct." Also asked, sharply: "our default
tick is 1ms so sub-1ms accuracy is not given here, right? Or does the
tick not affect the sending/receiving from the bus?" -- worth verifying
precisely rather than guessing, so traced the actual send/receive code
paths before answering:
- Send: `FrameService.SendFrame` → `FrameSink::Publish` →
  `CanBusRegistry::SendFrame` → `HilBridge::SendFrame` →
  `driver_->WriteFrame()` -- fully synchronous, inline in the gRPC
  handler's own thread.
- Receive: each CAN interface has its own dedicated blocking-read RX
  thread (`src/hil/hil_bridge.cpp`), dispatching to subscribers
  immediately on arrival.
- Neither path touches the tick loop at all -- `BOAT_NODE_TICK_MS`
  (confirmed default **1ms**, `main.cpp:427`, not the test-framework
  config schema's own unrelated `tick_ms: 10` default) only drives plugin
  `on_tick()` callbacks (e.g. CanTp's STmin pacing/N_Bs/N_Cr timeouts),
  never raw `FrameService` CAN send/receive. So the user's instinct that
  the tick *might* matter was reasonable to check, but for this specific
  path it doesn't -- sub-tick, sub-millisecond measurement of the
  gateway's own internal routing is architecturally sound.

Rebuilt the test per the resolved design: inject via the gateway's real
`SendFrame` (still genuinely exercising the DUT), but measure
`TS_message_on_can1 - TS_message_on_can0` with **both** timestamps from
raw SocketCAN reads -- `can0` has `CAN_RAW_LOOPBACK` enabled (verified
empirically, not assumed), so the gateway's own outbound write is
independently observable there with a real kernel timestamp, and the
gRPC call's own latency (spent before the frame ever reaches the wire)
drops out of the calculation entirely. Default bound relaxed to 5ms per
the user ("performance ... not so relevant for this show test case").

**Result after the fix**: 100/100 received, zero drops, routing time
**0.370-0.645ms** (mean 0.426ms, p99 0.645ms) -- comfortably under even
the *original* 1ms bar once gRPC overhead is correctly excluded, and
using an environment config with the test-framework's own 10ms
`tick_ms` default (not even the gateway's 1ms default), empirically
reconfirming the tick has no bearing on this path.

Also worth noting for whoever picks this up next: the test as built only
exercises *half* of the gateway's job -- accepting a `SendFrame` call and
writing it to the physical wire. Because can0/can1 are bridged at the
transceiver level, the receive side is observed via a raw SocketCAN read
that never actually goes through the gateway's own `can1` RX thread →
registry dispatch → `SubscribeFrames` path at all. A complete test would
also cover that direction (inject a frame via raw `cansend`, observe it
arrive via the gateway's own `SubscribeFrames`) -- not built yet, left as
an open question below rather than assumed in scope.

## Open questions for whoever continues this (not resolved, need the user's input)

- Should the receive-direction (wire → gateway's own `SubscribeFrames`)
  also be tested, per the gap noted above, for a genuinely complete
  round-trip routing-time picture?
- The admin_gui integration question this whole investigation was in
  service of is still open -- see the chat conversation (not restated
  here) for the three shapes considered (new "Test Runs" tab reusing the
  Nodes plumbing; deeper `TestHarness`↔`launcher_agent` integration;
  view-only report browsing). Now that the underlying framework is
  confirmed to actually work, and this specific real test passes cleanly,
  this is unblocked whenever the user wants to pick it up.

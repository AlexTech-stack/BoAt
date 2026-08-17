# FrameService / Frame Model Backlog

Gaps found while building a bidirectional CAN bridge against `FrameService`
(a phone-attached SLCAN adapter published onto a gateway vcan through
`StreamFrames`, tested on a live 500 kbit/2 Mbit bus).

Both are pre-existing, neither is a regression, and both were found by hardware
testing rather than reading the code — the first produced a silent, total
failure of one direction with no error anywhere.

---

## ✅ RESOLVED (2026-08-17) — `timestamp_ns` was always 0 for locally-sent frames

Found while building a real HIL test with the user: "boat frame subscribe
to testmsg on can0, subscribe to testmsg on can1, boat frame send testmsg
on can0 and compare the messages (including the Timestamps) from the
receiving messages" -- a simpler, better replacement for an earlier
version of the same test that read raw SocketCAN sockets instead
specifically to avoid this gap (see `backlog/test_runner_backlog.md`).

`Frame.timestamp_ns` is populated in exactly one place for CAN:
`SocketCanDriver::ReadFrame()` (`src/hil/can/socket_can_driver.cpp:125`)
calls `clock_gettime(CLOCK_REALTIME)` right after the blocking `read()`
syscall returns -- i.e. only on genuine wire RX. Nothing on the *send*
path ever set it: not `FrameService.SendFrame`, not `boat frame send`
(checked `boat_cli/frame.py` directly), not `FrameNode.send_can()` (SDK) --
all three construct a `Frame`/`SendFrameRequest` and never touch
`timestamp_ns`, so it stays at protobuf's default, 0.
`CanBusRegistry::SendFrame()`'s self-sent echo (`src/hil/
can_bus_registry.cpp` -- tags `BOAT_CAN_FLAG_SELF_SENT` and dispatches a
copy back to subscribers so a client can tell its own echo from wire RX,
see the resolved `SELF_SENT` entry above for the flag's own history) just
copied that same client-supplied `CanFrame` verbatim, `timestamp_ns` and
all -- so every self-sent/outbound-observed frame was reliably stamped 0,
permanently, regardless of when it was actually sent. `EthernetBusRegistry`
had the identical gap in its `SendFrame`/`SendFrameAll` (its RX path
already has a "stamp if zero" fallback for driver-supplied timestamps of
0 -- `ethernet_bus_registry.cpp:35-40` -- but nothing analogous existed
on the send side).

**Impact.** Any client computing an elapsed time against a locally-sent
frame's own `timestamp_ns` (exactly what the user's routing-time test
needed) got nonsense -- a delta against literal 0, not a real duration.
Anything else relying on a self-sent frame's timestamp being meaningful
(trace recording, replay, plugin logic) would have silently gotten "the
epoch" instead of "now" too, though this investigation didn't chase down
whether anything currently depends on it.

**Fixed**: `CanBusRegistry::SendFrame()`/`SendFrameAll()` and the
equivalent `EthernetBusRegistry` methods now capture a fresh timestamp
(`NowNs()`, a small helper added to each file -- `clock_gettime
(CLOCK_REALTIME)` for CAN, `std::chrono::system_clock::now()` for
Ethernet, matching each registry's own existing RX-side convention)
immediately after the write, and stamp the self-sent echo with *that*
instead of the client-supplied value. Symmetric with how RX already
works: a real wall-clock capture taken as close as practical to the
actual wire interaction, not a value passed through from somewhere else.

Also found and fixed a related, pre-existing, unrelated-to-this-session
test bug while verifying: `test_ethernet_hil.cpp`'s "frame send and
receive via UDP multicast" test called `VirtualEthernetDriver::
ReadFrame()` directly (bypassing `EthernetBusRegistry` entirely) and
asserted `timestamp_ns > 0` -- but that driver's own `ReadFrame()`
deliberately leaves it at 0 with the comment "filled by the registry",
which this test never invokes. The assertion was simply checking a
guarantee this call path never made. Fixed by asserting `== 0` instead,
matching the driver's own documented contract; the registry's actual
fill-if-zero behavior already has its own coverage in the next test case
("registry dispatches RX frame to subscriber").

Verified on real hardware (`agn-testcomputer`): a direct check (send one
frame, subscribe for its self-sent echo, confirm `timestamp_ns` now falls
strictly between wall-clock timestamps taken immediately before and after
the send call) confirmed a real, correctly-ordered capture, not garbage.
The user's own proposed test design -- `boat frame subscribe` on `can0`
and `can1`, `boat frame send` on `can0`, compare `timestamp_ns` -- now
produces a consistent, sensible routing time (0.45-0.69ms across several
runs) using nothing but the real gateway API, no raw sockets at all. Full
C++ test suite (160 tests, `ctest --test-dir build/debug`, including
`BOAT_HIL_ENABLED=1` HIL tests on real interfaces) passes cleanly except
22 pre-existing, unrelated `re2` third-party tests that don't build in
this environment ("Not Run", not a real failure) -- confirmed via a
`***Failed` grep that nothing else regressed.

---

## 🔴 `SELF_SENT` identifies the transmitting process, not the originating client

`CanBusRegistry::SendFrame` tags every locally transmitted frame with
`BOAT_CAN_FLAG_SELF_SENT` (`src/hil/can_bus_registry.cpp:44,60`) and dispatches
a tagged copy to RX subscribers, so a client can tell its own echo from wire RX.
That works for a plugin. It does not work for a bidirectional client.

The flag means **"this gateway transmitted it"**. A frame injected by a bridge
via `StreamFrames` and a frame sent by `boat frame send` travel the same
`FrameSink` → `SendFrame` path and come back tagged **identically**. A bridge
therefore cannot distinguish:

  - its own frame echoing back (must be suppressed, or it is retransmitted onto
    the physical bus and duplicated), from
  - a genuine gateway-originated frame (must be transmitted, since delivering it
    to the bus is the entire point of the bridge).

**Impact.** Filtering on the flag silently drops every gateway-originated frame:
`SendFrame` reports success, the frame reaches the vcan, subscribers see it, and
it never arrives on the wire. Not filtering duplicates every inbound frame back
onto the bus. Observed directly: with `SELF_SENT` filtering, gateway → bus was
completely dead while bus → gateway worked perfectly, with no error on either
side.

**Current workaround** (in the Android client): remember a fingerprint of every
frame published and suppress only matching echoes inside a short window. It
works, but it is guesswork — a gateway frame byte-identical to one the bridge
published moments earlier is swallowed, and there is no way for a client to
avoid that.

**Options.**
- Add an origin identifier to the frame stream: a per-call client id assigned
  when `StreamFrames` opens, echoed in frames the gateway reflects back. A
  client then suppresses exactly its own frames and nothing else. This is the
  clean fix and removes the ambiguity entirely.
- Alternatively, do not reflect a `StreamFrames` client's own ingress back to
  that same call. Cheaper, but changes `SubscribeFrames` semantics for the
  combined stream and would surprise a client that wants to see the merged bus.
- At minimum, document in `frame.proto` that `SELF_SENT` is process-scoped and
  is not a client-origin marker.

**Effort:** Small (document) to Medium (origin id through `StreamFrames`).

---

## 🟡 Extended identifiers below `0x800` cannot round-trip

`SocketCanDriver::WriteFrame` infers frame format from the identifier's value
(`src/hil/can/socket_can_driver.cpp:133`):

```cpp
const std::uint32_t ext_flag = (frame.can_id > 0x7FF) ? CAN_EFF_FLAG : 0U;
```

There is no explicit extended flag anywhere in the model: `CanMetadata` has
`can_id`, `dlc`, `flags` and `channel`, and `flags` carries only the CAN FD bits
plus `SELF_SENT`. Format is therefore a function of magnitude.

CAN itself does not work that way. The 29-bit range is `0x00000000`–`0x1FFFFFFF`
and includes small values; format is carried by the IDE bit, which is why
SocketCAN has a separate `CAN_EFF_FLAG` rather than inferring from the value. An
extended frame with identifier `0x123` and a standard frame with identifier
`0x123` are different frames on the wire and arbitrate differently.

**Impact — deliberately narrow.** On a real vehicle bus this is close to
theoretical: J1939 sets priority bits high and UDS uses `0x18DAxxxx`, so
extended identifiers in the wild are large, and the inference holds. The
concern is specific to BoAt's role as a **replay and capture** platform: a trace
recorded elsewhere that contains an extended frame below `0x800` — a bench rig,
a proprietary protocol, a conformance test probing the boundary — is imported,
replayed, and emitted as a *standard* frame. The identifier matches, the payload
matches, and the IDE bit has quietly changed. A tool whose job is faithful
reproduction losing that bit is a different class of problem from a vehicle
never exercising it.

**Options.**
- Add an explicit extended flag to `CanMetadata` (a new `flags` bit, or a
  dedicated field) and honour it in `SocketCanDriver::WriteFrame`, keeping the
  value-based inference as the fallback when unset.
- Or accept the limitation and state it in `frame.proto` next to `can_id`, so
  client authors know identifiers are the whole format signal.

**Effort:** Small either way; the choice is about how faithful replay needs to be.

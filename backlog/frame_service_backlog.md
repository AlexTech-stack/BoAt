# FrameService / Frame Model Backlog

Gaps found while building a bidirectional CAN bridge against `FrameService`
(a phone-attached SLCAN adapter published onto a gateway vcan through
`StreamFrames`, tested on a live 500 kbit/2 Mbit bus).

Both are pre-existing, neither is a regression, and both were found by hardware
testing rather than reading the code — the first produced a silent, total
failure of one direction with no error anywhere.

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

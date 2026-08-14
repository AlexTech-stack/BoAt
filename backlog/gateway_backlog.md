# Gateway Operations Backlog

Operational gaps found running the gateway against real CAN hardware alongside
an Android client. Neither is a crash or a wrong result in isolation — both
produce *misleading* behaviour, which is why they cost debugging time.

---

## ✅ RESOLVED (2026-08-10) — A second gateway binds the same port silently and steals half the traffic

gRPC's server sets `SO_REUSEPORT`, so starting a second `boat_gateway` while one
is already running does **not** fail with "address already in use". Both bind
`0.0.0.0:50051`, both report a healthy startup, and the kernel load-balances
incoming connections between them.

The result is not a clean failure. Each process has its own bus registries,
plugin instances, simulation state and `SELF_SENT` tagging, so a client's calls
land in one of two unrelated gateways at random.

**Impact — observed.** A test subscribed via `SubscribeFrames` and then sent a
frame via `SendFrame`. The two calls landed on *different* gateway processes, so
the frame reached the subscriber over the wire instead of by local dispatch and
arrived **without** `BOAT_CAN_FLAG_SELF_SENT`. The assertion failed with no
plausible explanation in the code, and the same test passed when run in
isolation. Diagnosing it required noticing two PIDs on one port.

Anything with per-process state is affected the same way: simulations created on
one instance are invisible to the other, `ListSimulations` returns different
answers on consecutive calls, and plugins load twice.

This is easy to hit unintentionally — a gateway left running in another terminal,
a stale process from an earlier session, or two people on one bench.

**Fixed**: both improvements from the original "Options" list, implemented
together rather than as alternatives —
- `BOAT_GRPC_PORT` env var (default 50051, matching the historical hardcoded
  value) lets a second instance choose a genuinely different port, so
  intentional multi-instance use (the actual motivation for this work — see
  the "administration tool to start/stop several gateways" discussion) has
  somewhere safe to go.
- `RefuseIfPortInUse()` probes the target port with a plain `bind()` (no
  `SO_REUSEPORT`) before `grpc::ServerBuilder` ever touches it, exactly the
  first option above. A plain bind fails with `EADDRINUSE` against *any*
  existing listener on the port regardless of whether it set `SO_REUSEPORT`,
  so this reliably catches the accidental case (leftover process, stale
  session, two people on one bench) that motivated this item, and exits with
  a clear message instead of starting into a silently-corrupted dual-gateway
  state.

Verified on real hardware: two instances on distinct ports (`BOAT_GRPC_PORT`
unset + `BOAT_GRPC_PORT=50052`) both start and both remain independently
reachable; a second instance targeting an already-bound port exits 1 with
the new error message, and the first instance is unaffected. A CLI client
connecting via `--host localhost:50052` reaches the second instance
correctly.

**Effort:** Small. The value is in the error message, not the mechanism.

**Related, client-side (2026-08-10):** `BOAT_GRPC_PORT`/`RefuseIfPortInUse()`
above solve the server side of multi-instance operation, but every client
(`boat` CLI, `BoAtClient`, every SDK `*Node` class) still hardcoded
`localhost:50051` as a literal default, so a node script or CLI invocation
had to be edited or given `--host`/`address=` every single time to reach a
non-default gateway. Added a `BOAT_HOST` env var, resolved in the same order
everywhere (explicit arg/flag > `BOAT_HOST` > `localhost:50051`), so a node
script/CLI command written with **no address hardcoded** is portable across
any gateway on any device — point it elsewhere by setting `BOAT_HOST` in the
environment it runs in. This is the piece a future admin tool needs to
launch node processes against the gateway instance it manages, without those
node scripts needing any awareness of which gateway they're talking to.
Verified on real hardware (`agn-testcomputer`): `BoAtClient()`/`FrameNode()`
pick up `BOAT_HOST` when set, an explicit `address=`/`--host` still wins over
it, and `boat --help` shows the resolved value in `[default: ...]`.

---

## 🟡 No bus health or error visibility from SLCAN capture

The WeAct USB2CANFDV2's SLCAN firmware implements no `F` status command
(confirmed against the device: `F` returns BEL), and its receive format carries
only data frames. Error frames, bus-off, error-passive and error-warning states
are therefore invisible to any SLCAN-based capture path.

`DLT_CAN_SOCKETCAN` can represent bus errors via `CAN_ERR_FLAG` (`0x20000000`),
so the recorded PCAPNG *format* has room for them; there is simply nothing to
put in it.

**Impact.** A capture taken during a genuinely unhealthy bus is
indistinguishable from one taken on a quiet, healthy bus — traffic simply
thins out. For field diagnosis, "the bus was in error-passive for 4 seconds" is
often the finding, and it cannot be recovered afterwards from the trace.

By contrast a SocketCAN interface reports all of this: `ip -details link show`
exposes state and error counters, and error frames arrive in-band.

**Options.**
- Prefer SocketCAN-attached hardware (`slcand`, `peak_usb`, `gs_usb`) wherever
  the capture host is Linux, and treat SLCAN-over-USB as the mobile-only path.
- Or flash the candleLight/gs_usb firmware, which does report bus state.
- Or extend the firmware (source is published) with a status command and
  periodic bus-state records.
- At minimum, record in the trace's section header that bus-state reporting was
  unavailable, so a reader knows the absence of errors is not evidence of their
  absence.

**Effort:** Small (document the limitation) to Medium (firmware).

---

## 🟡 Restarting a gateway on the same port can refuse to start for up to ~60s

Surfaced while reproducing a node-side bug (`backlog/nodes_backlog.md`'s
"gateway restart left nodes stuck" entry): killing a `boat_gateway` that
had active gRPC clients connected, then immediately starting a new one on
the *same* `BOAT_GRPC_PORT`, hits the startup port-in-use refusal (see
this file's "A second gateway binds the same port silently" entry above
for why that check exists) even though nothing is actually listening —
`ss -ltnp` shows no listener at all. `ss -tan` shows why: a lingering
`[::1]:<port>` connection from a just-killed client sits in `TIME-WAIT`
for the OS's usual ~60s, and without `SO_REUSEADDR` on the listening
socket, that alone is enough to make a fresh `bind()` to the same port
fail — the port-in-use check isn't being fooled by a phantom process, the
underlying socket genuinely can't rebind yet.

**Impact.** A gateway that's stopped and restarted on the same port
shortly after (exactly what a real gateway-restart workflow looks like,
e.g. via admin_gui's Stop then Start on the same instance) can fail to
start for up to a minute with the same "port already in use" message
used for the genuine second-instance case above — misleading, since nothing
else is actually running.

**Options.**
- Set `SO_REUSEADDR` on the gateway's own listening socket (standard,
  safe practice for TCP servers restarting on a fixed port — distinct
  from `SO_REUSEPORT`, which is what the resolved issue above was
  correctly getting rid of; `SO_REUSEADDR` does not reintroduce that
  problem, it only allows rebinding through `TIME-WAIT`).
- Or have the port-in-use check specifically distinguish "another live
  process is listening" (genuine conflict, refuse) from "the port is
  between binds because of `TIME-WAIT`" (should just wait/retry a short,
  bounded amount rather than fail outright).

**Effort:** Small (a socket option in the gateway's listen setup).

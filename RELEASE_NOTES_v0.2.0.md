**Green and honest.** Bounded, observable gRPC streams and storage; fault injection, metrics
and the remaining service wrappers in the SDK and CLI; hardened CanTp, TCP, replay and
simulation lifecycles; four security fixes; and the first **arm64** packages. This closes the
[v0.2.0 milestone](https://github.com/AlexTech-stack/BoAt/milestone/1).

The plugin ABI is unchanged (**v9**), so existing plugins load without recompiling. There is
one **breaking** change, to the launcher agent's default bind address; see *Upgrading*.

---

### Highlights

- **arm64 packages and image.** `.deb`, `.rpm` and `.tar.gz` for amd64 and aarch64, plus a
  `linux/amd64` + `linux/arm64` image at `ghcr.io/alextech-stack/boat`. Both are built natively
  on a glibc 2.28 baseline, so they install on AlmaLinux 8 and Debian 11 as well as current
  Ubuntu. v0.1.x's RPM could not be installed; this one can.
- **Nothing grows without bound, and losses are visible.** Every gRPC stream, the EventBus and
  the event store use bounded drop-oldest queues. Drops are reported as metrics
  (`grpc.<stream>.dropped`, `event_bus.dropped`, `event_store.dropped`), and event-store writes
  are off the tick thread.
- **Observability.** `boat metrics get|watch` and a dashboard metrics panel, CAN bus health
  (`can.<iface>.*`: drops, error frames, bus-off, TEC/REC, state), and `boat config show`
  for the gateway's effective configuration.
- **Fault injection from the CLI and SDK.** `boat fault inject|list` and `FaultNode` expose all
  five engine modes (`stuck`, `noise`, `dropout`, `invert`, `delay`) over an inclusive tick
  range.
- **Replay.** Imports stream past gRPC's 4 MiB limit, pause/resume no longer bursts, Start/Stop
  no longer race the tick thread, and events carry the authority tick.
- **Launcher agent.** Adopt a hand-started gateway without restarting it, and stopped
  definitions persist across agent restarts (**Adopt…** in `admin_gui`).
- **Measured performance.** `boat_benchmark` plus a committed baseline: ~0.3M frames/s on the
  real vcan0 path and ~2.1M frames/s on the synthetic core path. The ≤1 ms tick claim is now
  scoped as an idle p99 soft-real-time target (`docs/performance-baseline.md`).

### Security

Untrusted gRPC input could escape `/tmp` with a crafted trace id (#62), leak up to 191 bytes of
gateway stack to every frame subscriber through an oversized `dlc` (#61), overflow a buffer with
a large IP/UDP container (#60), or crash the gateway with deeply nested scenario JSON (#65). All
four are fixed. The gRPC API is still **unauthenticated by default**; see `SECURITY.md` and use
`BOAT_TLS_*` / mTLS on any shared network.

### Upgrading

- **BREAKING:** `ui/launcher_agent.py` binds `127.0.0.1` by default. For a remote `admin_gui`,
  start the agent with `BOAT_AGENT_BIND=0.0.0.0` (or a LAN address). The web UIs and tools
  also bind loopback unless their `BOAT_*_BIND` variable is set.
- **Plugins:** `on_frame` may run concurrently from several RX threads and alongside `on_tick`
  (now documented in `plugin.h`); a plugin with mutable state must synchronize it. The
  installed headers now include `boat/frame.h`, so out-of-tree plugins can build against an
  installed BoAt for the first time.
- **Results can change:**
  - a scenario's `seed` is now honoured (it was parsed and ignored);
  - `end_tick=0` on a fault means one tick;
  - only one simulation can be active, and `ResetSimulation` rewinds the tick to 0;
  - trace ids must match `[A-Za-z0-9._-]+`.
- **TCP plugin:** experimental. Its state machine is tested, but there is no API yet to open a
  listener or connection (#117). The `demo/tcp_plugin/` scripts are removed, and
  `TraceReplayer(tcp_plugin_path=...)` raises `NotImplementedError`.
- **SDK/CLI:** install both at 0.2.0; `boat-cli` now requires `boat-py>=0.2.0`.

---

Full list of changes: [CHANGELOG.md § 0.2.0](https://github.com/AlexTech-stack/BoAt/blob/v0.2.0/CHANGELOG.md#020--2026-mm-dd).
Compare: [v0.1.1...v0.2.0](https://github.com/AlexTech-stack/BoAt/compare/v0.1.1...v0.2.0).

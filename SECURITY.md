# Security Policy

## Reporting a vulnerability

Please **don't open a public issue** for a vulnerability.

Use GitHub's private vulnerability reporting (the **Security** tab → *Report a vulnerability*),
or email **alexander.guenther@tuta.io**. Include what you did, what you expected, what happened,
and the commit you were on. A proof-of-concept helps but isn't required.

This is a small project with a single maintainer, so please don't expect same-day replies. You
should get an acknowledgement within a week. If a report is valid, you'll be told what the fix
is and when it lands, and credited in the release notes unless you'd rather not be.

## Supported versions

BoAt is **pre-1.0** and only the latest `master` is supported. There are no backported security
fixes to older tags. If you are running BoAt anywhere that matters, track `master` or a recent
tag and read [CHANGELOG.md](CHANGELOG.md).

## What BoAt is, before you deploy it

This matters more than usual here, because BoAt's job is to put traffic on real vehicle buses.
Several of its defaults are deliberately permissive — appropriate for a lab bench, not for an
untrusted network. None of the following is a vulnerability; they are the design, and you should
know them.

**The gRPC API is unauthenticated, and plaintext unless you enable TLS.** Anything that can
reach the port can send frames, load plugins, start replays and read every bus. There is no
authentication layer at all — TLS gives you transport security and, with `BOAT_TLS_CLIENT_CA`,
client-certificate *authentication of the channel*, but there are no users, roles or
permissions. Enable TLS with `BOAT_TLS_CERT` + `BOAT_TLS_KEY` (and `BOAT_TLS_CLIENT_CA` for
mTLS), and bind the gateway where only trusted clients can reach it. The gateway warns on
startup when TLS is unconfigured; that warning is the whole protection.

**The gateway loads arbitrary native code.** `BOAT_NODE_PLUGINS` and the plugin gRPC service
`dlopen()` whatever `.so` path they are given, which then runs in-process with the gateway's
full privileges. A plugin path is as trusted as the gateway binary itself. Don't accept plugin
paths from anywhere you wouldn't accept a shell command.

**Replay and frame-send write to real hardware.** If the gateway has a physical CAN interface
open, `FrameService.SendFrame` and the replay pipeline put frames on that wire. On a bench
wired to an ECU — or a vehicle — that is a physical actuation path. Virtual (`vcan*`)
interfaces are the safe default; physical ones are chosen explicitly via
`BOAT_CAN_INTERFACES`.

**Physical Ethernet injection needs elevated capability.** Raw L2 frames require
`CAP_NET_RAW` (`sudo setcap cap_net_raw+ep <gateway binary>`). Prefer that over running the
gateway as root.

**The web UIs and tools under `ui/` and `tools/` have no authentication either**, and some
accept paths and shell out. They are development tools meant for `localhost`. Do not expose
them.

## In scope

Reports are most useful where BoAt fails to hold a boundary it claims to hold:

- Memory-safety bugs reachable from gRPC input, a trace file, or a PDU database — the C++
  parsers for trace records, CAN/Ethernet frames and scenario JSON are the interesting surface.
- A TLS or mTLS configuration that is silently weaker than requested. `TlsConfig` is meant to
  raise `TlsConfigError` rather than fall back to plaintext; a path that degrades quietly is a
  bug.
- Path traversal or injection in the trace/report/database handling, or in `ui/`/`tools/`
  endpoints beyond their intended "it runs shell commands on purpose" surface.
- A crash or hang reachable from a malformed input file — BoAt is used in CI, so a trace that
  wedges the gateway is worth reporting.
- Anything that lets a plugin escape the lifetime guarantees in `plugin.h` (use-after-free
  around `Unload()` racing the tick thread, for instance).

## Out of scope

- The permissive defaults described above, reported as if they were bugs.
- Denial of service through resource exhaustion by an *authorised* client. The API is
  unauthenticated by design; if you can reach it you can already do worse.
- Vulnerabilities in third-party dependencies with no BoAt-specific exploit path — report those
  upstream (see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).
- Findings against `test/`, `demo/` or example configurations.

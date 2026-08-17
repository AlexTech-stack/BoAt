# Nodes Backlog

Tracking the "Nodes" GUI feature — the third area (after gateway
process/instance management in `admin_gui/`) identified as worth adding a
GUI for, alongside Scenarios/Simulations/Replays. See the `admin-gui-future-scope`
memory note for the full context of how this fits the bigger picture.

Status: `boat-platform/nodes/` has real content and works end-to-end both
through the existing `ui/control_panel.py` "Nodes" web UI and, now, through
`admin_gui`'s own Nodes tab (`ui/launcher_agent.py`'s `/api/nodes`).

---

## Done (2026-08-12)

**The `boat-platform/nodes/` directory existed only as a convention** —
`ui/control_panel.py`'s "Nodes" web UI (port 8081) already had full
discovery (`GET /api/nodes`, scans `*.py` files, extracts module
docstrings, detects `input()`-using interactive scripts) and lifecycle
control (`start`/`stop`/`log`) wired up and working, but the directory
itself was empty — nothing to actually discover.

Added two general-purpose node scripts (not demo-specific like
`boat-platform/demo/`'s CAN-ID-triggered cyclic sender):

- **`cyclic_can_sender.py`** — sends one configurable CAN(FD) frame on a
  fixed interval (`--iface`, `--can-id`, `--data`, `--cycle-ms`, `--fd`,
  `--brs`). A general periodic traffic generator, not tied to a specific
  demo scenario.
- **`can_request_responder.py`** — replies to one CAN ID with a fixed
  response frame (`--iface`, `--request-id`, `--response-id`,
  `--response-data`). A minimal building block for testing anything that
  sends CAN requests (CanTp, PDU routing, diagnostic tools) without a full
  ECU simulation.

Both fix the actual "hardcoded port" problem the user flagged in the old
`boat-platform/demo/*.py` scripts: those default `--address` to the
literal string `"localhost:50051"` and always pass that (or whatever
`--address` resolved to) explicitly into `CanNode(address=...)`, so they
can never benefit from the `BOAT_HOST` env var fallback added earlier this
session — even *omitting* `--address` on their own CLI still passes a
concrete string down, never `None`. The new scripts default `--address` to
`None`, so:
- `ui/control_panel.py`'s existing mechanism (`[..., "--address", gateway]`,
  always passed explicitly from its own gateway-address UI field) keeps
  working completely unmodified.
- Anything that omits `--address` entirely and just sets `BOAT_HOST` in the
  environment (matching the launcher-agent pattern -- see
  `backlog/launcher_agent_backlog.md`) gets the portability that was the
  actual point of adding `BOAT_HOST`.

**Also found and fixed a real infrastructure problem while testing this**:
the remote hardware test box (`agn-testcomputer`) was 13 commits behind
origin -- this whole session's `git push`es had gone to GitHub, but the
box's checkout was never `git pull`ed, only individually ad-hoc-synced file
by file for whatever was being actively tested at the time. Files never
directly touched in a given turn (like `sdk/python/boat/client.py`, part of
the original `BOAT_HOST` commit) silently stayed on their pre-session
version. First symptom: `can_request_responder.py` crashed with
`AttributeError: 'NoneType' object has no attribute 'encode'` -- `BOAT_HOST`
wasn't being read at all, because the checked-out `client.py` was the
pre-`BOAT_HOST` version with a hardcoded default. Fixed with `git fetch` +
`git reset --hard origin/<branch>` (safe here specifically because the only
locally-"modified" tracked files, per `git diff`, turned out to be exactly
the ad-hoc syncs already applied to disk -- resetting just caught the ref
up to what was already there; new untracked work — the two new node
scripts — is unaffected by `--hard`, which only touches tracked files).
**Lesson for future turns on this box: `git pull`/`git reset --hard
origin/<branch>` at the start of any real verification pass, not just
ad-hoc single-file syncs, to avoid this class of false negative.**

Verified on real hardware (`agn-testcomputer`), after the git-sync fix:
- `can_request_responder.py` standalone via `BOAT_HOST` (no `--address`):
  request `0x7E0 [22 F1 90]` → reply `0x7E8 [50 01]` confirmed on the wire
  via `candump`, <1ms turnaround.
- `cyclic_can_sender.py` standalone via `BOAT_HOST`: correct payload and
  ~300ms cadence confirmed via `candump`; separately confirmed `--fd
  --brs --address <explicit>` (the `control_panel.py`-style invocation) also
  works.
- **Full integration with the existing, unmodified `ui/control_panel.py`**:
  `GET /api/nodes` discovered both scripts with correct docstrings;
  `POST /api/nodes/can_request_responder/start?address=localhost:50055`
  (exactly what the web UI's own JS calls) started it, and it responded
  correctly on the bus (`0x7E0` → `0x7E8`) with a real `candump` capture;
  `POST .../stop` cleanly stopped it, confirmed via a subsequent
  `GET /api/nodes` showing `status: "stopped"`.

## Done (2026-08-13) — node management in admin_gui/launcher_agent.py

Integrated node management into the agent, per the "Next steps" item
below. Deliberately a **separate** registry (`NodeInstance`/`NodeRegistry`)
alongside `GatewayInstance`/`InstanceRegistry`, not a generalization of it
-- the domains genuinely differ (no port to allocate, no ifaces/plugins of
its own; a node needs a target gateway via `BOAT_HOST` and arbitrary
script-specific CLI args, `extra_args`, since a plain list, not a
structured shape).

- **Agent**: `GET /api/node-scripts` (discovery, mirrors
  `control_panel.py`'s), `GET/POST /api/nodes`, `GET/PUT/DELETE
  /api/nodes/<id>` (edit/delete refused (409) while running, same pattern
  as gateway instances), `POST /api/nodes/<id>/start|stop`, `GET
  /api/nodes/<id>/log`. `NodeInstance.start()` sets `BOAT_HOST=<target_host>`
  in the spawned process's env -- the actual point of the `BOAT_HOST` work
  from earlier this session finally being exercised by something other than
  a human typing it into a shell.
- **admin_gui**: second tab, "Nodes", same shape as Gateways (table,
  New/Edit/Start/Stop/Delete, log viewer, equivalent command line). New
  Node dialog's Script dropdown pulls from `GET /api/node-scripts` and
  shows the module docstring; Extra Args is free text (`shlex.split()` on
  submit) since node scripts don't share one CLI shape the way gateway
  plugin configs do. No Managed column / external-node discovery (arbitrary
  Python scripts aren't reliably identifiable by process name).
  `PollWorker` now polls both `/api/instances` and `/api/nodes` per host
  per cycle; the node table got the stale-selection-clear fix
  (`backlog/launcher_agent_backlog.md`'s TC_AdminGui_011 bug) applied
  proactively from day one rather than discovered the hard way again.

Verified end-to-end on real hardware (`agn-testcomputer`), agent API first
via curl (discover scripts, create+start a node pointed at a real gateway,
confirmed genuinely functioning via `candump` -- request/response round
trip through a fully agent-managed node, no `--address` flag involved at
all, purely `BOAT_HOST` set by the agent's own subprocess env -- edit/
delete-while-running refused with 409, log, clean stop), then the full Qt
flow with real screenshots: New Node dialog's script dropdown and docstring
label populated from a live agent; created+started a node via the dialog's
own `result_payload()`; selected it via a real table click; confirmed the
"Equivalent command line" panel matched exactly; confirmed the started
node was genuinely responding on the wire (not just "a process exists");
edit-while-running refused server-side; Edit dialog correctly pre-filled
from the existing node's definition; both tabs screenshotted showing a
rich Gateway instance (plugins, multiple interfaces) and a rich Node
instance side by side, tab switching working, no regression to the
existing Gateways tab.

## Done (2026-08-13, continued) — Target gateway dropdown + paste-to-fill for nodes

User feedback on the first Nodes-tab pass: **Target host** (free-text
"host:port") was misleading -- the host/IP is already picked by the
**Host** field above it, so retyping it (or getting it wrong) for Target
host was redundant friction. Fixed by replacing it with **Target
gateway**, an editable dropdown of the selected host's own
`GET /api/instances` (gateway instances), each shown as `name —
localhost:<port> (status)` and storing the plain `localhost:<port>` as
that item's data -- since a node's process is spawned by the agent on the
*same* machine as any gateway it's pointed at there, `localhost` is always
correct and there's no separate hostname/IP to get right. Typing a bare
port number (`50052`) still normalizes to `localhost:50052`; a full
`host:port` is still accepted verbatim for the less common case of
pointing a node at a different machine's gateway.

Also added the Nodes-side equivalent of the Gateways tab's paste feature:
**From command line** + **Parse && Fill**, parsing a pasted `BOAT_HOST=...
python3 <script> <args>` line (`_parse_node_command_line()`) back into
Target gateway/Script/Extra args. Uses `shlex` (not the Gateways dialog's
brace-aware tokenizer) since node extra_args can contain quoted values
with no JSON structure to protect -- e.g. `--message "hello world"` tokenizes
correctly as one arg, which the brace/whitespace-only tokenizer would have
split wrongly. `_format_node_command_line()`'s extra_args formatting was
upgraded to `shlex.join()` too (was a plain space-join before), so a value
containing a space round-trips correctly through the command-line panel
and back through paste.

Building the dropdown surfaced a real bug in the first draft: reading
`target_host_combo.currentText()` directly returns whatever's in the
editable combo's line edit -- for a *picked* item that's the full display
label (`"main — localhost:50051 (running)"`), not the plain address stored
as that item's data. Fixed by only trusting `currentData()` when the
displayed text still matches the selected index's label exactly (i.e. the
user picked it and didn't retype); otherwise the text is treated as
free-form entry (bare port or explicit host:port). A test written before
this fix caught it immediately (asserted the payload's `target_host`
against the picked port, got the raw label back instead).

Verified on real hardware (`agn-testcomputer`): dropdown correctly listed
both a running and a stopped gateway instance with accurate labels;
picking one resolved to the plain `localhost:<port>` (confirmed only after
the fix above); a bare port normalized correctly; an explicit `host:port`
passed through verbatim; `_parse_node_command_line()` correctly handled
both the full `BOAT_HOST=... python3 <script> <args>` form and a bare
`<script> <args>` form with no prefix, including a quoted multi-word arg;
the dialog's own **Parse && Fill** on a real formatted command line
populated every field correctly, and creating a node from that
parsed-and-filled payload matched exactly.

## Done (2026-08-13, continued) — cross-host Target gateway dropdown

User pushback on the first pass, worth recording verbatim since the
reasoning matters: "Nodes are talking via gRPC to the gw. So there are
three options: 1. node runs on the same ip as the gw and the ui. 2. Node
runs on same ip as the ui but gw runs on different ip 3. gw, node and ui
are all running on different ips. So where are the nodes from the dropdown
menu currently located?"

Answer that came out of thinking it through: a node's *process* always
runs on whichever host's agent spawned it (the dialog's **Host** field) --
never wherever `admin_gui` itself happens to be running, since the UI is a
pure REST client and no gRPC traffic ever flows through it. But the
*first* Target gateway dropdown only ever queried that same host's own
`GET /api/instances`, so it only ever represented the "node + gateway, same
machine" case (the user's option 1) -- options 2/3 (a node reaching a
gateway on a genuinely different machine) were only possible by typing a
raw `host:port` by hand, with the dropdown offering zero visibility into
what's running elsewhere.

Fixed: `_reload_target_hosts()` now queries *every configured host's*
`GET /api/instances`, not just the node's own. Same-host entries still
resolve to `localhost:<port>` (correct and DNS-free, since it really is
the same machine). Cross-host entries resolve to that *other* host's own
address instead -- parsed from its agent URL via `urlparse().hostname` --
and are tagged `[host-name]` in the label, since from the spawned node's
own point of view `localhost` would mean itself, not the other machine.
Noted (not fixed, may not be fixable in general): a host added to this app
as `localhost:<agent-port>` rather than its real hostname/IP will produce
a `localhost` cross-host entry too, which is only actually correct if the
node's own host happens to be that literal same physical box -- there's no
way for the tool to know an agent's "real" externally-reachable address
beyond the URL the user gave it.

Verified on real hardware (`agn-testcomputer`) with two agents on one box,
deliberately addressed via two genuinely distinct non-"localhost" strings
(a real IP and the real hostname) so the test wasn't fooled by a
degenerate same-machine case: created a gateway on each; confirmed the
dropdown, with the dialog's Host set to the first agent, showed the first
agent's own gateway as `localhost:<port>` untagged and the second agent's
gateway as `<real-hostname>:<port>` tagged `[host-name]`; switched the
dialog's Host to the second agent and confirmed the roles correctly
flipped (now *that* agent's gateway is the untagged localhost entry, the
first agent's is the tagged cross-host one at its real IP); picked the
cross-host entry and confirmed `result_payload()`'s `target_host` matched
exactly. Bonus, not a bug: externally-discovered (`managed: false`)
gateways showed up in the dropdown too, on both sides -- a real running
gateway is a valid node target regardless of which agent (if any) manages
it, so no filtering was added to exclude them.

## Done (2026-08-13, continued) — cyclic_can_sender.py's cycle timing was consistently 3-4ms slow

User, running a node through the admin GUI they'd just set up, noticed via
`candump -t d` that a `--cycle-ms 300` sender was actually landing frames
303-304ms apart, and remembered a similarly-shaped bug being fixed earlier
this session -- asked "wasn't that fixed some time ago?" It was, but in a
different, unrelated piece of code: the CanTp plugin's C++ TX thread. This
script (`boat-platform/nodes/cyclic_can_sender.py`) is new this session
and had independently reintroduced the *same class* of bug in its own,
much simpler Python sleep loop.

Root cause: `deadline = time.monotonic() + cycle_s` was computed *after*
`node.send_can()` returned, not before it was called -- `send_can()` is a
synchronous gRPC call (protobuf serialization + a loopback round trip),
consistently a few ms even locally, and computing the deadline afterward
silently stretched every single cycle by that amount. Fixed by capturing
`cycle_start = time.monotonic()` *before* the send and computing the
deadline from that instead. Also tightened the wait loop itself: it used
to always sleep a flat 50ms chunk regardless of how close the actual
deadline was, which could round the final wait up by nearly 50ms in the
worst case; now it sleeps `min(remaining, 0.05)` so the last wait is
precise while still checking for shutdown at least every 50ms.

Verified on real hardware (`agn-testcomputer`) with real `candump -t d`
timing captures, not just "does it still send": before the fix, 12
consecutive cycles measured 302.96-304.49ms (mean 303.94ms, ~1.3% slow)
against a 300ms-configured cycle; after, 11 steady-state cycles measured
299.98-300.33ms (mean ~300.14ms, within 0.05% of the target). The one
low outlier (292ms on the very first cycle in the "after" run) is one-time
gRPC channel/stub warm-up on the first call, not a steady-state issue.

`boat-platform/demo/cyclic_sender_node.py` and `eth_cyclic_sender_node.py`
(the older, demo-specific, deprecated-adjacent scripts -- not part of the
active Nodes feature) have the identical bug pattern in their own cyclic
loops. Left as-is: out of scope for this fix since nothing in the current
Nodes/admin_gui path uses them, but noted here in case they're ever
resurrected.

## Done (2026-08-14) — dynamic per-argument fields in New/Edit Node

User feedback on the Extra Args free-text field: "Is it possible to add a
variable amount of fields with examples for every argument a node might
have (for the once that are not already defined at another point)?" --
i.e. build one input field per CLI argument a node script declares,
showing an example value, for everything not already covered by another
dialog field (`--address`, already the Target gateway dropdown).

Two-sided change:

- **Node scripts**: `cyclic_can_sender.py` and `can_request_responder.py`
  refactored to extract argument parsing into a module-level
  `build_parser() -> argparse.ArgumentParser`, separate from `main()`, by
  convention -- documented in each script's docstring. This lets
  `launcher_agent.py` import a script and call `build_parser()` alone,
  without triggering any of `main()`'s side effects (connecting to a
  gateway, parsing `sys.argv`, running forever). `--response-data`'s help
  text also gained a concrete example (`e.g. 5001`) specifically so a
  default-empty-string argument still has something to show.
- **Agent**: `_introspect_node_args()` (`ui/launcher_agent.py`) imports a
  discovered script via `importlib.util.spec_from_file_location` +
  `exec_module` (registering it in `sys.modules` under a synthetic name
  for the duration, so any relative-import assumptions in the script's
  own top-level code still work, then popping it back out), calls
  `build_parser()` if present, and turns `parser._actions` into a JSON
  schema: `flag` (the long option string), `help`, `default` (its native
  JSON-serializable type -- `int`/`bool`/`str`/`null`, not always a
  string), and `is_flag` (`action.nargs == 0`, true for
  `store_true`/`store_false`/`store_const`/help actions alike -- a more
  general test than checking against argparse's specific private action
  classes). `--address` and `-h` are always skipped. The whole function is
  wrapped in a blanket `try/except Exception: return []` -- a script with
  no `build_parser()`, one that isn't importable in this environment
  (e.g. a missing dependency), or anything else going wrong just yields an
  empty schema, never a broken `/api/node-scripts` response for every
  *other* script. `_discover_node_scripts()` now includes this schema as
  each script's `"args"` key.
- **admin_gui**: `NewNodeDialog` grew a "Script arguments" `QGroupBox`,
  rebuilt via `_rebuild_arg_fields()` whenever the Script selection
  changes -- a `QCheckBox` per boolean flag, a `QLineEdit` per everything
  else, placeholder text `e.g. <default>` falling back to the argument's
  help text when the default is empty (so `--data`, default `""`, still
  shows "Payload as hex bytes, e.g. AABBCCDD ..." rather than a blank
  hint). Extra Args remains as the escape hatch for anything outside the
  script's declared schema; on submit, populated per-argument fields are
  prepended to whatever's typed there. In Edit mode (and in **Parse &&
  Fill**), `_prefill_arg_fields()` walks the node's existing `extra_args`
  list, pulling any recognized `--flag [value]` pair back into its
  matching field and returning the unrecognized leftovers for the flat
  field -- so a node saved before this feature (or one using a flag
  outside the current schema) still edits cleanly.

Verified on real hardware (`agn-testcomputer`): `/api/node-scripts` via
curl showed both scripts' correct argument schemas (six args for
`cyclic_can_sender`, `--fd`/`--brs` correctly typed as `is_flag: true`,
`--cycle-ms` default as a real `int`). Two throwaway Qt driver scripts run
under a real Xvfb + `xcb` platform (screenshotted via `QWidget.grab()`,
not an offscreen render) confirmed: the New Node dialog rendered all six
fields with correct placeholders/checkboxes; a node created with
`extra_args` mixing three recognized flags and one unrecognized one
(`--not-a-real-flag xyz`), reopened via Edit, pre-filled the three
recognized fields exactly and left only the unrecognized pair in Extra
Args. See `test/AdminGui.md`'s TC_AdminGui_016 for the full account;
`admin_gui/docs/new_node_dialog.png` updated to the new dialog layout.

## Done (2026-08-14, continued) — gateway restart left nodes stuck (SDK-level fix)

User feedback, running two different node types against one gateway
through admin_gui: "When i start the two different nodes on one gw 50057.
Then i stop the gw only the cyclic_can_sender.py actually stops, the
can_request_responder keeps running. However it does not react (as the gw
is stopped). Then when i restart the gw i have to first stop and then
start the can_request_responder node again. In addition a second gw was
running on 50056 with a can_request_responder node without issues." (The
50056 instance being fine wasn't a contradiction -- its gateway simply
never went down during that session.)

Root cause was in the shared SDK, not either node script:
`FrameNode.subscribe()`'s background thread (`sdk/python/boat/
frame_node.py`) wrapped its entire streaming-RPC read loop in a bare
`except Exception: pass`. When the gateway went away, the stream raised
`grpc.RpcError` (`UNAVAILABLE`), that got silently swallowed, and the
background thread just quietly exited -- `run()`'s main-thread wait loop
had no idea the subscription had died, so it waited forever, keeping the
process "running" while genuinely doing nothing forever after. Meanwhile
`cyclic_can_sender.py`'s main loop called `node.send_can(...)` with no
try/except at all, so the *same* underlying failure crashed that process
outright -- different code, same underlying gap (no failure handling at
all), producing opposite-looking symptoms (zombie-but-alive vs. crashed)
purely by accident of which SDK method each script happened to call.

Two-layer fix, both needed (confirmed on real hardware -- see below, the
first alone was not enough):

1. `FrameNode.subscribe()`'s background thread now retries the stream on
   any failure with capped exponential backoff (1s, 2s, 4s, ..., capped
   at 10s, reset after a connection stays up >5s), logging each failure
   to stderr instead of hiding it. `cyclic_can_sender.py`'s send loop got
   a matching `try/except` around `send_can()` so a transient failure
   logs and retries next cycle instead of crashing the process -- makes
   the two node types behave consistently across a gateway restart, which
   was the other half of the user's report (the asymmetry itself, not
   just the responder's non-recovery).
2. Retrying against the *same* gRPC channel turned out not to be enough
   on its own: verifying on real hardware, both a sender and a responder
   stayed stuck in a `Connection refused` retry loop for 90+ seconds
   *after* the gateway had already come back up and was independently
   confirmed reachable (a fresh `grpc.channel_ready_future` check from a
   brand new process succeeded immediately). grpc-python tracks each
   channel's own reconnect backoff internally, uncapped by anything the
   SDK controls, up to grpc's own ceiling (~120s default) -- after enough
   consecutive failures, retrying an RPC on the *same* channel can just
   fail fast against a still-backed-off subchannel without the client
   library even attempting a fresh TCP connection, regardless of how
   often the application layer calls the RPC. This is exactly why the
   user's manual workaround (stop, then start the node again) "worked":
   a fresh process means a fresh `BoAtClient`/channel with no failure
   history, bypassing the stale backoff entirely. Fixed by adding
   `FrameNode._reconnect()`, which closes the current channel and opens a
   fresh `BoAtClient` -- called from `subscribe()`'s retry loop, and from
   `send()` on any failure (re-raising the original exception after, so
   callers still see it) -- so every retry gets a channel with no backoff
   baggage, not just a repeated call on the same stuck one.

Verified on real hardware (`agn-testcomputer`), on an isolated test
gateway/vcan0 pair the user's own live session (port 50056, vcan1) was
never touched: started both node types against a test gateway, confirmed
baseline (cyclic traffic + a real request/response round trip via
`cansend`/`candump`), killed the gateway. With only the auto-reconnect fix
(no channel recreation) applied, both nodes correctly avoided crashing
this time, but genuinely failed to recover even 90+ seconds after the
gateway came back up and was confirmed independently reachable --
reproducing the *actual* remaining bug behind the user's report, not just
the crash/zombie asymmetry. With `_reconnect()` added, restarting the
gateway (on the same port, after waiting out an unrelated TIME_WAIT delay
-- see below) had both nodes fully working again within a few seconds,
with zero manual intervention: `candump` showed the cyclic sender's
300ms-cycle traffic resume on its own, and a fresh `cansend vcan0
7E0#22F19000` got the expected `7E8 [50 01]` reply back in ~3.6ms.

Side finding surfaced while reproducing this bug, originally left unfixed
here as a different component (C++ gateway startup, not this SDK): while
restarting the test gateway on the *same* port shortly after killing the
previous instance, the C++ gateway's own "port already in use" startup
check refused to start for up to ~60s even though nothing was actually
listening (`ss -ltnp` showed no listener) -- `ss -tan` showed why: a
lingering IPv6 `[::1]:<port>` connection in `TIME-WAIT` from a killed
client connection was blocking the rebind, meaning the gateway's port-in-use
probe wasn't using `SO_REUSEADDR`. Picked up and fixed the same day -- see
`backlog/gateway_backlog.md`'s now-✅-RESOLVED entry for the fix and its
own real-hardware verification.

## Done (2026-08-14, continued) — plugin-based example node scripts

> **Superseded (2026-08-17):** `can_tp_echo_responder.py` described below
> was replaced by `can_tp_trigger_sender.py` -- the echo design didn't
> hold up to real manual testing. See the "can_tp_echo_responder.py ->
> can_tp_trigger_sender.py" entry further down for why and what changed;
> this entry is kept as the historical record of the original version.

User feedback: "we need some more nodes. They shall work (of course) but
they shall also be examples of how to create notes, especially how to use
plugins." `cyclic_can_sender.py`/`can_request_responder.py` only ever
exercised `FrameNode` -- raw CAN frames straight through the gateway's
core `FrameSink`, no plugin involved. Added two new nodes specifically to
show the *other* shape: talking to a plugin's own gRPC service instead.

- **`pdu_cyclic_publisher.py`** -- `pdu_router` plugin example.
  `PduNode.configure_route()` registers a PDU ID -> (transport, iface, CAN
  ID) routing rule, then `send()` sends a fixed payload as that PDU on a
  cycle. Mirrors `PduMessageNode`'s database-driven approach
  (`sdk/python/boat/pdu_message_node.py`) but spelled out by hand for one
  message, as the simplest possible example.
- **`can_tp_echo_responder.py`** -- `can_tp` plugin example. Same
  request/responder shape as `can_request_responder.py`, but over ISO-TP
  (`CanTpHandle.configure()` registers an N-SDU session, `subscribe()`
  streams reassembled RX payloads, `send()` echoes each one back through
  the plugin's own segmentation) -- so it carries payloads far longer than
  one raw CAN frame, closer to what real UDS/diagnostic services need.

Key lesson both docstrings call out explicitly, and both nodes' reconnect
loops implement: a plugin's configuration (routes, N-SDU sessions) lives
in the *gateway process*, not the client -- unlike a raw CAN sender, which
has nothing to lose when the gateway restarts, a gateway restart wipes a
plugin's state along with the connection. Retrying the data call alone
(what `FrameNode`'s fix from earlier the same day does) isn't enough here;
both nodes re-run `configure()`/`configure_route()` on every reconnect,
not just retry `send()`.

Building `can_tp_echo_responder.py`'s reconnect loop surfaced a real,
separate finding: unlike `PduNode.configure_route()`/`send()` (which catch
`grpc.RpcError` internally and return `False`), `CanTpHandle.configure()`/
`send()`/`subscribe()` (`sdk/python/boat/can_tp.py`) do **not** catch it --
they raise. My first draft assumed the same "returns False on failure"
contract as `PduNode` and crashed with an unhandled traceback the first
time it hit a real disconnect during verification. Fixed at the call site
(wrapped `configure()` in its own `try/except` in the node's reconnect
loop) rather than changing the shared SDK class this time, to keep this
change scoped to "add example nodes" -- the inconsistency itself is noted
in `AGENTS.md`'s "Plugin-based node scripts" section for whoever picks up
`can_tp.py` next.

Also surfaced, and fixed, a real bug in `launcher_agent.py` unrelated to
either specific plugin: `NodeInstance.start()`'s `subprocess.Popen` never
set `PYTHONUNBUFFERED`, so every node's stdout -- CPython fully
block-buffers it whenever it isn't a tty, which a piped subprocess never
is -- sat invisibly in an ~8KB libc buffer until it filled or the process
exited. Only `stderr` writes (Python's `stderr` is always unbuffered) were
ever actually appearing promptly in `admin_gui`'s/`control_panel`'s live
log -- meaning ordinary informational `print()` output from *every* node
script, not just the two new ones, was effectively invisible in real time
this whole session; only the retry/backoff `stderr` warnings added for
gateway-restart resilience earlier the same day were ever visible live.
Fixed with one line (`env["PYTHONUNBUFFERED"] = "1"`).

Verified on real hardware (`agn-testcomputer`) on an isolated test
gateway/`vcan0` with both plugins loaded
(`BOAT_NODE_PLUGINS=pdu_router.so,can_tp.so?{"iface":"vcan0"}`), the
user's own live session confirmed untouched throughout:
- `pdu_cyclic_publisher.py`: `candump` showed CAN ID `0x100` with the
  configured payload on the configured cycle.
- `can_tp_echo_responder.py`: a single-frame request (`7E0#03112233`) came
  back correctly echoed (`7E8#03112233CCCCCCCC`, `CC` = the default pad
  byte) in ~4ms. A genuine multi-frame exchange via `isotpsend -D 20`
  (First Frame + Flow Control + 2 Consecutive Frames, 20-byte payload)
  reassembled correctly server-side, and the plugin correctly began
  segmenting the matching echo back out (First Frame with the same length
  and leading bytes) -- confirming real multi-frame reassembly, not just
  the single-frame path.
- Introspection: `/api/node-scripts` correctly showed both scripts' full
  argument schemas via curl.
- `PYTHONUNBUFFERED` fix: log output appeared within ~1s of process start
  (previously invisible for 60+ seconds in the same setup).
- Gateway-restart resilience (repeating the earlier scenario, this time
  with plugins loaded): killed the plugin-loaded gateway -- neither node
  crashed, both logged clean retry/backoff messages (including the
  now-fixed `can_tp_echo_responder.py`, which crashed with a traceback
  before the `try/except` fix above). Restarted the gateway on the same
  port -- both nodes recovered entirely on their own, `candump` showing
  the PDU publisher's traffic resume and a fresh single-frame request
  getting echoed correctly, no manual intervention.

## Done (2026-08-17) — can_tp_echo_responder.py → can_tp_trigger_sender.py

User feedback after manually testing the plugin-example nodes: "I quickly
checked the nodes, all good except the can-tp_echo_responder. It seem not
to echo the messages back (I sent via cansend). However it sends out flow
control messages. I think we have to rework the functionality of the
node. It makes no sense to let it just echo a whole tp message."

Root cause of the reported symptom (not a bug in the plugin or the node
as such -- an unworkable design for manual testing): the echo responder
needed to first *receive* a complete multi-frame ISO-TP message before it
could echo anything, and receiving one requires being a full ISO-TP
*requester* -- send Consecutive Frames yourself, in response to the
plugin's own Flow Control, at the right times. A single `cansend` of
something that looks like a First Frame makes the plugin correctly emit
Flow Control and then wait (up to its N_Bs timeout) for Consecutive
Frames nobody is sending by hand -- nothing ever reassembles, so nothing
was ever going to be echoed back. The "it sends out flow control
messages" the user observed was the plugin behaving completely correctly
in response to an incomplete manual request, not a malfunction.

The user then specified the replacement design directly, worked example
included: a trigger frame on a plain CAN ID (`0x111` in their example)
whose payload is the desired message length (`0A` = 10 bytes) causes the
node to send a fresh **incrementing-byte** payload (`00 01 02 ...`)
through the plugin's own segmentation on a *different* address pair
(`0x200` out, Flow Control expected on `0x201`) -- putting the plugin's
automatic segmentation on the *send* side, where a human only needs to
hand-craft one Flow Control frame (fixed format, no sequence tracking) to
watch/drive a real multi-frame exchange from a terminal. Implemented
exactly as specified in the new `can_tp_trigger_sender.py`, replacing
`can_tp_echo_responder.py` outright (git `rm` + new file, not a rename --
the whole interaction model changed): `FrameNode.subscribe()` listens for
the plain trigger frame (no plugin involved for that side), and
`CanTpHandle.send()` fires once per trigger with `bytes(i % 256 for i in
range(length))`.

Verified on real hardware (`agn-testcomputer`) on an isolated test
gateway/`vcan0` pair, the user's own live session confirmed untouched
throughout, matching the user's own example values as this node's
defaults (`--trigger-id 0x111`, `--source-addr 0x200`,
`--target-addr 0x201`) so it works out of the box with no flags:
- `cansend vcan0 111#0A` -> `0x200  10 0A 00 01 02 03 04 05` (First
  Frame, length 10) -- exactly the user's own worked example.
- `cansend vcan0 201#300000CCCCCCCCCC` (Flow Control) ->
  `0x200  21 06 07 08 09 CC CC CC` (Consecutive Frame) -- again exactly
  matching the user's example byte-for-byte.
- A 20-byte variant produced a First Frame plus two Consecutive Frames
  with the full `00`-`13` sequence intact across both, confirming
  multi-CF segmentation (not just the single-CF case above).
- A length <= 7 (`cansend vcan0 111#06`) correctly produced a Single
  Frame instead of segmenting -- the plugin choosing the right frame type
  on its own, not something this node has to decide.
- Empty/RTR trigger payload correctly fell back to `--default-length`
  (8); an explicit `00` length byte correctly sent nothing and logged
  "0 bytes -- nothing to send" instead of a degenerate send attempt.
- Plugin-not-loaded case: `configure()` raised (`CanTpHandle` doesn't
  catch `grpc.RpcError`, see below), caught by `ensure_configured()`,
  printing the exception's own specific message rather than guessing.

Testing the gateway-restart scenario end-to-end (not just "does it crash")
surfaced a second real, closeable gap: `state["configured"]` has no way
to know the gateway restarted (and wiped the plugin's N-SDU session)
until an actual `configure()`/`send()` call fails -- nothing else
invalidates it. Without a retry, the *first* trigger after any restart
hit `send()`'s `FAILED_PRECONDITION` ("no N-SDU connection configured"),
got logged as a failure, and was silently dropped -- only the *second*
trigger would actually go out. Fixed by retrying once within the same
trigger event: on a `send()` failure, discard the `CanTpHandle`
(same reasoning as `FrameNode._reconnect()`), reconfigure, and retry that
same payload immediately before giving up on it. Re-verified after the
fix by killing and restarting the gateway, waiting only for `FrameNode`'s
own background stream to reconnect (no other warm-up), then sending a
*single* trigger cold -- both a short (Single Frame) and a 20-byte
(First Frame + 2 CFs) case succeeded on that one trigger, confirmed on
the wire and via a log showing the self-heal ("send() raised (...);
reconfiguring and retrying this trigger once...") followed by no further
error.

Full account, including the original design and why it didn't hold up,
in the "plugin-based example node scripts" entry above (marked
superseded) and `test/WebUIs.md`'s `TC_WebUIs_012`.

## Next steps (not started)

- More node scripts as real needs surface -- four now cover raw-CAN
  send/responder and PDU-route/CanTp-session plugin examples; still not a
  complete ECU simulation library (Ethernet/PDU-database/SOME-IP examples
  are still unwritten).
- Session save/load doesn't cover Nodes yet -- only Gateway instances are
  captured in a session file today.
- Node instance persistence across an agent restart -- same in-memory-only
  gap as gateway instances (`backlog/launcher_agent_backlog.md`).
- Scenarios/Simulations/Replays panels are still a separate, unstarted
  architectural layer (direct gRPC to a `boat_gateway`, not through an
  agent) -- see the `admin-gui-future-scope` memory note.

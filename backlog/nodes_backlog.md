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

## Next steps (not started)

- More node scripts as real needs surface -- the two here are deliberately
  minimal building blocks, not a complete ECU simulation library.
- Session save/load doesn't cover Nodes yet -- only Gateway instances are
  captured in a session file today.
- Node instance persistence across an agent restart -- same in-memory-only
  gap as gateway instances (`backlog/launcher_agent_backlog.md`).
- Scenarios/Simulations/Replays panels are still a separate, unstarted
  architectural layer (direct gRPC to a `boat_gateway`, not through an
  agent) -- see the `admin-gui-future-scope` memory note.

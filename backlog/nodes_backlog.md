# Nodes Backlog

Tracking the "Nodes" GUI feature — the third area (after gateway
process/instance management in `admin_gui/`) identified as worth adding a
GUI for, alongside Scenarios/Simulations/Replays. See the `admin-gui-future-scope`
memory note for the full context of how this fits the bigger picture.

Status: `boat-platform/nodes/` now has real content and works end-to-end
through the existing `ui/control_panel.py` "Nodes" web UI. Not yet
integrated into `admin_gui`/`launcher_agent.py`.

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

## Next steps (not started)

- Integrate node management into `admin_gui`/`launcher_agent.py` itself
  (the memory note's original framing) -- `InstanceRegistry`/
  `GatewayInstance` generalizes almost directly to "manage arbitrary
  script processes on a host," reusing `boat-platform/nodes/` as the
  discovery source for consistency with `control_panel.py`. Would give
  node management the same multi-host aggregation, Managed-column,
  session-save/load treatment the gateway instances already have.
- More node scripts as real needs surface -- the two here are deliberately
  minimal building blocks, not a complete ECU simulation library.
- Scenarios/Simulations/Replays panels are still a separate, unstarted
  architectural layer (direct gRPC to a `boat_gateway`, not through an
  agent) -- see the `admin-gui-future-scope` memory note.

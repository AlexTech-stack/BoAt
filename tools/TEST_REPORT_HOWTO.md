# System-test campaign HOWTO

How to run BoAt's **manual** system-test suite and record the results with
`tools/test_report.py`.

> This is the hand-verified release sign-off record, not the automated
> `boat test run <manifest>` runner and not `ctest`/`pytest`. Verdicts here come
> from a person (or agent) driving the platform against real hardware/VMs. Nothing
> fills them in automatically.

## The model in one paragraph

Test **definitions** live in `test/<TestSet>.md` (`Gateway.md`, `CAN.md`, …) and
contain only preconditions, steps and expected outcome — no verdict. A **campaign**
is one run of that suite, stored as `test/campaigns/<date>-<name>.yaml`, with one
`verdict` + `notes` per `TC_` id. Each run is its own committed file, so older
campaigns are never overwritten. `tools/test_report.py` parses the definitions,
records into the campaign, checks completeness, and renders a sign-off report.

## Layout

```
test/
  Structure.md            # the template / verdict semantics
  TestSuite.md            # index: run order, per-set counts, cross-cutting tags
  <TestSet>.md            # definitions (no results)
  campaigns/
    2026-11-24-baseline.yaml   # imported pre-overhaul state (reference)
```

## 1. Start a campaign

```bash
python3 tools/test_report.py new 2026-11-25-v0.2.0 \
  --product-version 0.2.0 --operator "your name"
```

This creates `test/campaigns/2026-11-25-v0.2.0.yaml` with an **empty** results
list (a fresh run has no recorded cases). The date defaults to today. `new`
also remembers it as the **current campaign**, so later commands do not need
the name repeated.

Seeding from the previous run is optional and useful for a focused regression
pass — but for a release sign-off, start empty and re-verify everything:

```bash
python3 tools/test_report.py new 2026-11-25-v0.2.0 --from 2026-11-24-baseline
```

## 2. Review the suite (interactive, recommended)

```bash
python3 tools/test_report.py review
python3 tools/test_report.py review --set CanTp
python3 tools/test_report.py review --unrecorded --compare 2026-11-24-baseline
```

A full-screen terminal UI walks the cases in run order and shows the whole
definition (preconditions, steps, expected) beside the current and previous
result. Keys:

| Key | Action |
|---|---|
| `o` / `n` / `i` / `s` | record OK / NOK / INCONCLUSIVE / NOT_TESTED |
| `e` | edit notes in `$VISUAL`/`$EDITOR` |
| `u` | undo the last change (verdict or notes) |
| `j` / `k` | next / previous case |
| `J` / `K` | scroll the detail one line |
| `PgUp` / `PgDn` | scroll the detail one page |
| `{` / `}` | previous / next section in the detail |
| `y` / `Y` | copy the case id / full rendered definition |
| `g` / `G` | first / last case |
| `/text` | filter by substring over id/body (`Enter` apply, `Esc` cancel) |
| `f` | cycle filter (all / unrecorded / failures / OK / NOT_TESTED / set) |
| `F` | filter editor: Set, Tag, Verdict, Unrecorded, Text |
| `x` | clear **all** filters (text/tag/set/verdict/unrecorded) |
| `c` | switch campaign |
| `?` | help overlay |
| `q` | quit |

Every verdict and notes edit is saved immediately, so stopping mid-run loses
nothing. The UI needs a TTY; `--plain` (also automatic without a TTY) runs the
same session as a line-based prompt, which is what the automated tests drive.
`--no-notes-prompt` skips the per-verdict notes question.

**Filters.** `f` cycles a few common kinds; `F` opens a form with **Set**,
**Tag**, **Verdict**, **Unrecorded** and **Text** (Tab between fields, ←/→ to
change a value, Space to toggle, Enter apply, Esc cancel, `c` clear the form).
A live `matches: N/total` line shows the effect. `x` clears everything at once
(the `f` cycle deliberately keeps a text/tag filter, so use `x` when a search
seems stuck). Prompts (`/` search, notes, campaign) accept with Enter and
cancel with Esc. `F` is TUI-only; with `--plain`, set the initial filter with
`--set/--tag/--verdict/--unrecorded/--text`.

### One case at a time (scripted or non-TUI)

No campaign name is needed once it is current, and ids may be abbreviated:

```bash
python3 tools/test_report.py next --set Gateway        # print the next unrecorded case
python3 tools/test_report.py show CAN_011              # procedure + current result
python3 tools/test_report.py set CAN_011 NOK --notes "link down still returned accepted=true (#66)"
python3 tools/test_report.py set CAN_011 OK --notes "re-ran after reboot" --append
python3 tools/test_report.py unset CAN_011             # back to unrecorded
```

`set`, `show`, `unset`, `status`, `summary`, `validate` and `render` all use the
current campaign when no campaign is given.

### Verdicts and evidence

**Verdicts** (also in `test/Structure.md`):

| Verdict | Meaning |
|---|---|
| `OK` | actual matches expected |
| `NOK` | actual differs from expected |
| `INCONCLUSIVE` | ambiguous (flaky, infra problem, missing info) |
| `NOT_TESTED` | deliberately skipped (precondition not met, out of scope) |

`NOT_TESTED` is a *recorded* decision, not "forgot to run it": a strict
`validate` still requires an entry for every case.

**Evidence:** keep large artifacts (traces, screenshots, logs) out of git — put
them under `traces/` (gitignored) or `/tmp` and mention the path/issue number in
`--notes`.

## Campaign memory, short ids and discovery

- `use NAME` sets the current campaign; `current` prints it. `new` and `review`
  set it automatically.
- Resolution order: positional campaign → `-c/--campaign` → `BOAT_TEST_CAMPAIGN`
  → remembered campaign (`test/.test_report_state.json`, gitignored).
- Ids accept exact, `CAN_011`, `tx_failure_reported`, or `@42` (1-based run
  order). An ambiguous abbreviation lists the candidates instead of guessing.
- `list [--set S] [--tag T] [--verdict V] [--unrecorded] [--text X] [--ids-only] [--json]`
  discovers cases and (when a campaign is current) their verdicts.
- `campaigns` lists campaign names; `next` prints the next unrecorded case.
- Shell completion: add
  `eval "$(python3 tools/test_report.py completion bash)"` to `~/.bashrc`
  (or `completion zsh` with `bashcompinit`). It completes campaign names and
  test ids.

## 3. Check progress whenever you like

```bash
python3 tools/test_report.py status 2026-11-25-v0.2.0 --next 5
```

Shows recorded/total, verdict counts, a per-TestSet breakdown, and the next
unrecorded ids.

```bash
python3 tools/test_report.py validate 2026-11-25-v0.2.0 --allow-partial
```

`--allow-partial` downgrades "missing result" to a warning while the run is in
progress. It still reports real errors (unknown id, duplicate entry, bad verdict).

## 4. Close out the campaign

```bash
# strict: every case must have exactly one valid result
python3 tools/test_report.py validate 2026-11-25-v0.2.0

# verdict totals + list of NOK/INCONCLUSIVE with the first note line
python3 tools/test_report.py summary 2026-11-25-v0.2.0

# sign-off report (Markdown by default, or --format json)
python3 tools/test_report.py render 2026-11-25-v0.2.0 --out reports/signoff-v0.2.0.md

git add test/campaigns/2026-11-25-v0.2.0.yaml
git commit -m "test(system): v0.2.0 campaign"
```

Commit the campaign file (and the rendered report if you keep it). Do **not**
commit verdicts into the TestSet `.md` files.

## 5. Compare runs

```bash
python3 tools/test_report.py diff 2026-11-24-baseline 2026-11-25-v0.2.0
```

Classifies verdict changes: `regressions` (OK→NOK), `fixes` (NOK→OK), other
changes, added and removed ids. Add `--fail-on-regression` to exit 1 when any
OK→NOK exists — suitable as a CI gate on a campaign.

## Command reference

All commands take `--root`, `--tests-dir`, `--campaigns-dir` and
`-c/--campaign` (options may come before or after the subcommand). A campaign
argument is optional wherever the remembered/`BOAT_TEST_CAMPAIGN` campaign can
be used, and may be a **name** (`2026-11-25-v0.2.0`, looked up in
`test/campaigns/`) or a **path**.

| Command | Purpose |
|---|---|
| `new NAME [--from CAMPAIGN] [--date D] [--product-version V] [--operator O] [--environment E] [--note T] [--force]` | create an empty campaign (or seed from another) and make it current |
| `review [CAMPAIGN] [--set S] [--tag T] [--text X] [--unrecorded] [--verdict V] [--start ID] [--compare C] [--plain] [--no-notes-prompt]` | interactive reviewer (curses TUI; `--plain` line mode) |
| `set [CAMPAIGN] ID VERDICT [--notes T] [--append]` | record one case (ID may be abbreviated) |
| `show [CAMPAIGN] ID` | print the case procedure + current result |
| `unset [CAMPAIGN] ID` | clear a result back to unrecorded |
| `next [CAMPAIGN] [--set S] [--tag T]` | print the next unrecorded case |
| `list [CAMPAIGN] [--set S] [--tag T] [--verdict V] [--unrecorded] [--text X] [--ids-only] [--json]` | discover cases |
| `use CAMPAIGN` / `current` | set / print the remembered campaign |
| `campaigns [--json]` | list campaign names |
| `status [CAMPAIGN] [--next N]` | progress, per-set breakdown, next unrecorded |
| `validate [CAMPAIGN] [--allow-partial] [--json]` | completeness/consistency check; exit 1 on errors |
| `summary [CAMPAIGN] [--json]` | verdict totals + non-OK cases |
| `render [CAMPAIGN] [--out FILE] [--format md\|json]` | sign-off report |
| `diff A B [--fail-on-regression] [--json]` | compare two campaigns |
| `completion bash\|zsh` | emit a shell completion script |

## How it parses the suite

- Only ids matching `TC_<Set>_<3 digits>_<name>` are treated as cases, so the
  placeholder in `Structure.md` is ignored. `Structure.md` and `TestSuite.md` are
  skipped entirely.
- Run order comes from `TestSuite.md`'s TestSet table; case order follows the
  file.
- `TestSuite.md`'s declared per-file counts, total, and cross-cutting tag names
  are checked during `validate` and reported as **warnings** if they drift.

## Troubleshooting

- **"campaign not found"** — the name must match a file in `test/campaigns/`
  (e.g. `2026-11-25-v0.2.0`) or you passed a path that does not exist.
- **"no campaign selected"** — run `use CAMPAIGN`, pass `-c CAMPAIGN`, set
  `BOAT_TEST_CAMPAIGN`, or let `review` pick one interactively.
- **"ambiguous test id"** — the abbreviation matched several cases; use a longer
  one, or run `list --text <fragment>` to see the candidates.
- **"unknown test id"** — run `list --ids-only` or `show`; `@N` selects the
  N-th case in run order.
- **`validate` says "missing result"** — expected during a partial run; use
  `--allow-partial`, or record the case as `NOT_TESTED` with a reason when done.
- **The TUI does not start / garbled screen** — it needs a TTY. Use `--plain`,
  or check `TERM`; the plain fallback is automatic when stdin/stdout is not a
  terminal.
- **PyYAML missing** — `pip install pyyaml` (already a repo dependency via
  `admin_gui/requirements.txt`).

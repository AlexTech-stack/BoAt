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
list (a fresh run has no recorded cases). The date defaults to today.

Seeding from the previous run is optional and useful for a focused regression
pass — but for a release sign-off, start empty and re-verify everything:

```bash
python3 tools/test_report.py new 2026-11-25-v0.2.0 --from 2026-11-24-baseline
```

## 2. Work through the TestSets in order

Pick the next case from the suite order (Gateway first, … Tools/Release last;
see `test/TestSuite.md`). To see a case's procedure and its current result
without opening two files:

```bash
python3 tools/test_report.py show 2026-11-25-v0.2.0 TC_Gateway_001_start_with_vcan
```

Perform the steps, then record the outcome:

```bash
# pass
python3 tools/test_report.py set 2026-11-25-v0.2.0 TC_Gateway_001_start_with_vcan OK \
  --notes "gRPC on 0.0.0.0:50051; list-ifaces shows vcan0 via virtual driver"

# fail -- reference the issue
python3 tools/test_report.py set 2026-11-25-v0.2.0 TC_CAN_011_tx_failure_reported NOK \
  --notes "link down still returned accepted=true (#66)"

# deliberately skipped -- say why
python3 tools/test_report.py set 2026-11-25-v0.2.0 TC_Release_004_arm64_packages NOT_TESTED \
  --notes "no aarch64 host available this run"

# add to existing notes instead of replacing
python3 tools/test_report.py set 2026-11-25-v0.2.0 TC_Gateway_001_start_with_vcan OK \
  --notes "re-ran after reboot: still OK" --append
```

Each `set` saves immediately, so a long session is crash-safe and you can stop
and resume any time.

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

All commands take `--root`, `--tests-dir`, `--campaigns-dir` (options may come
before or after the subcommand) and accept a campaign **name** (`2026-11-25-v0.2.0`,
looked up in `test/campaigns/`) or a **path**.

| Command | Purpose |
|---|---|
| `new NAME [--from CAMPAIGN] [--date D] [--product-version V] [--operator O] [--environment E] [--note T] [--force]` | create an empty campaign (or seed from another) |
| `set CAMPAIGN ID VERDICT [--notes T] [--append]` | record one case |
| `show CAMPAIGN ID` | print the case procedure + current result |
| `status CAMPAIGN [--next N]` | progress, per-set breakdown, next unrecorded |
| `validate CAMPAIGN [--allow-partial] [--json]` | completeness/consistency check; exit 1 on errors |
| `summary CAMPAIGN [--json]` | verdict totals + non-OK cases |
| `render CAMPAIGN [--out FILE] [--format md\|json]` | sign-off report |
| `diff A B [--fail-on-regression] [--json]` | compare two campaigns |

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
- **"unknown test id"** — check `status`/`show`; ids are exact.
- **`validate` says "missing result"** — expected during a partial run; use
  `--allow-partial`, or record the case as `NOT_TESTED` with a reason when done.
- **PyYAML missing** — `pip install pyyaml` (already a repo dependency via
  `admin_gui/requirements.txt`).

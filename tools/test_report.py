# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Record, validate and report on BoAt's manual system-test campaigns.

The test *definitions* live in ``test/<TestSet>.md`` and carry no results. What a
particular run observed lives in ``test/campaigns/<date>-<name>.yaml``, one entry
per ``TC_`` id. This tool is the bridge between the two: it parses the
definitions, records verdicts into a campaign, validates that a campaign is
complete and consistent, and renders sign-off reports.

It is deliberately *not* the automated ``boat test run`` runner. Verdicts here
are the observations of a person (or agent) driving the platform by hand; nothing
updates them automatically.

See ``tools/TEST_REPORT_HOWTO.md`` for the day-to-day workflow.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a repo dependency
    sys.stderr.write(
        "test_report.py needs PyYAML (pip install pyyaml); it is already a repo "
        "dependency via admin_gui/requirements.txt.\n"
    )
    raise SystemExit(2)


VERDICTS = ("OK", "NOK", "INCONCLUSIVE", "NOT_TESTED")
PASSING = "OK"
FAILING = {"NOK", "INCONCLUSIVE"}

# Strict id shape: TC_<Set>_<3 digits>_<name>. Deliberately excludes the
# ``TC_<TestSet>_<NNN>_<short_description>`` placeholder in Structure.md.
CASE_HEADING_RE = re.compile(r"(?m)^### (TC_[A-Za-z0-9]+_\d{3}_[A-Za-z0-9_]+)\s*$")
TESTSETS_RE = re.compile(r"(?m)^\*\*TestSets:\*\*\s*(.+)$")
TAG_RE = re.compile(r"\[([^\]]+)\]")

INDEX_ROW_RE = re.compile(
    r"(?m)^\|\s*\[([^\]]+)\]\s*\|\s*\[([^\]]+)\]\(([^)]+)\)\s*\|\s*(\d+)\s*\|"
)
INDEX_TOTAL_RE = re.compile(r"(?m)^\*\*Total:\s*(\d+)\s+TestCases\.\*\*")
CROSS_CUTTING_HEADING_RE = re.compile(
    r"(?m)^##\s+Cross-cutting TestSets.*$"
)
CROSS_CUTTING_ROW_RE = re.compile(r"(?m)^\|\s*\[([^\]]+)\]\s*\|")

NON_DEFINITION_FILES = {"Structure.md", "TestSuite.md"}


class TestReportError(Exception):
    """A usage or data error that should end the process with exit code 2."""


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class Definition:
    id: str
    file: str
    set_name: str
    sets: list[str]
    body: str
    order: int = 0


@dataclass
class Result:
    id: str
    verdict: str
    notes: str = ""


@dataclass
class Campaign:
    name: str
    date: str
    results: list[Result] = field(default_factory=list)
    product_version: str = ""
    operator: str = ""
    environment: str = ""
    note: str = ""
    path: Optional[Path] = None

    def by_id(self) -> dict[str, Result]:
        return {r.id: r for r in self.results}

    def get(self, test_id: str) -> Optional[Result]:
        return self.by_id().get(test_id)


@dataclass
class SuiteIndex:
    order: list[str] = field(default_factory=list)
    declared_counts: dict[str, int] = field(default_factory=dict)
    declared_total: Optional[int] = None
    cross_cutting: list[str] = field(default_factory=list)


@dataclass
class Context:
    root: Path
    tests_dir: Path
    campaigns_dir: Path
    definitions: list[Definition] = field(default_factory=list)
    suite_index: SuiteIndex = field(default_factory=SuiteIndex)

    def definition_ids(self) -> set[str]:
        return {d.id for d in self.definitions}

    def definition(self, test_id: str) -> Definition:
        for d in self.definitions:
            if d.id == test_id:
                return d
        raise TestReportError(f"unknown test id: {test_id}")


# ── Parsing ───────────────────────────────────────────────────────────────────


def parse_suite_index(tests_dir: Path) -> SuiteIndex:
    """Read run order, declared per-set counts and cross-cutting tags.

    Best-effort: a missing or unparsable TestSuite.md yields an empty index,
    which only disables ordering and the drift warnings.
    """
    index = SuiteIndex()
    path = tests_dir / "TestSuite.md"
    if not path.is_file():
        return index
    text = path.read_text(encoding="utf-8")

    for m in INDEX_ROW_RE.finditer(text):
        _set_name, _label, filename, count = m.groups()
        index.order.append(filename)
        index.declared_counts[filename] = int(count)

    total = INDEX_TOTAL_RE.search(text)
    if total:
        index.declared_total = int(total.group(1))

    heading = CROSS_CUTTING_HEADING_RE.search(text)
    if heading:
        tail = text[heading.end():]
        next_heading = re.search(r"(?m)^##\s+\S", tail)
        section = tail[: next_heading.start()] if next_heading else tail
        for m in CROSS_CUTTING_ROW_RE.finditer(section):
            index.cross_cutting.append(m.group(1))

    return index


def _parse_sets(body: str) -> list[str]:
    m = TESTSETS_RE.search(body)
    if not m:
        return []
    return TAG_RE.findall(m.group(1))


def parse_definitions(tests_dir: Path, suite_index: Optional[SuiteIndex] = None) -> list[Definition]:
    """Return every TestCase definition, in the suite's documented run order."""
    if not tests_dir.is_dir():
        raise TestReportError(f"tests directory not found: {tests_dir}")

    index = suite_index or parse_suite_index(tests_dir)
    order_of = {name: i for i, name in enumerate(index.order)}

    keyed: list[tuple[int, int, Definition]] = []
    for path in sorted(tests_dir.glob("*.md")):
        if path.name in NON_DEFINITION_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        matches = list(CASE_HEADING_RE.finditer(text))
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[start:end].rstrip()
            keyed.append(
                (
                    order_of.get(path.name, len(order_of)),
                    i,
                    Definition(
                        id=m.group(1),
                        file=path.name,
                        set_name=path.stem,
                        sets=_parse_sets(body),
                        body=body,
                    ),
                )
            )

    keyed.sort(key=lambda item: (item[0], item[1]))
    definitions = [item[2] for item in keyed]
    for i, d in enumerate(definitions):
        d.order = i
    return definitions


# ── Campaign I/O ──────────────────────────────────────────────────────────────


class _Dumper(yaml.SafeDumper):
    def increase_indent(self, flow: bool = False, indentless: bool = False):  # noqa: D401
        return super().increase_indent(flow, False)


def _represent_str(dumper: yaml.SafeDumper, data: str):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _represent_str)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    return str(value)


def resolve_campaign_path(name: str, campaigns_dir: Path) -> Path:
    candidate = Path(name)
    if candidate.is_file():
        return candidate.resolve()
    for option in (
        campaigns_dir / name,
        campaigns_dir / f"{name}.yaml",
        campaigns_dir / f"{name}.yml",
    ):
        if option.is_file():
            return option.resolve()
    raise TestReportError(
        f"campaign not found: {name} (looked in {campaigns_dir})"
    )


def load_campaign(path: Path) -> Campaign:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise TestReportError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise TestReportError(f"{path}: top level must be a mapping")

    raw_results = data.get("results") or []
    if not isinstance(raw_results, list):
        raise TestReportError(f"{path}: 'results' must be a list")

    results: list[Result] = []
    for i, item in enumerate(raw_results):
        if not isinstance(item, dict):
            raise TestReportError(f"{path}: results[{i}] must be a mapping")
        test_id = item.get("id")
        verdict = item.get("verdict")
        if not test_id:
            raise TestReportError(f"{path}: results[{i}] has no 'id'")
        if not verdict:
            raise TestReportError(f"{path}: {test_id} has no 'verdict'")
        results.append(Result(str(test_id), str(verdict), _as_text(item.get("notes"))))

    return Campaign(
        name=_as_text(data.get("campaign")) or path.stem,
        date=_as_text(data.get("date")),
        results=results,
        product_version=_as_text(data.get("product_version")),
        operator=_as_text(data.get("operator")),
        environment=_as_text(data.get("environment")),
        note=_as_text(data.get("note")),
        path=path.resolve(),
    )


def save_campaign(campaign: Campaign, path: Path, order: Optional[dict[str, int]] = None) -> None:
    if order is not None:
        campaign.results.sort(key=lambda r: order.get(r.id, len(order)))

    data: dict[str, Any] = {"campaign": campaign.name, "date": campaign.date}
    if campaign.product_version:
        data["product_version"] = campaign.product_version
    if campaign.operator:
        data["operator"] = campaign.operator
    if campaign.environment:
        data["environment"] = campaign.environment
    if campaign.note:
        data["note"] = campaign.note

    entries: list[dict[str, Any]] = []
    for r in campaign.results:
        entry: dict[str, Any] = {"id": r.id, "verdict": r.verdict}
        if r.notes:
            entry["notes"] = r.notes
        entries.append(entry)
    data["results"] = entries

    text = yaml.dump(
        data,
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=2_000_000_000,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── Aggregation / validation ──────────────────────────────────────────────────


def aggregate(definitions: list[Definition], campaign: Campaign) -> dict[str, Any]:
    recorded = campaign.by_id()
    per_set: dict[str, dict[str, int]] = {}
    for d in definitions:
        row = per_set.setdefault(
            d.set_name,
            {"cases": 0, "recorded": 0, "missing": 0, **{v: 0 for v in VERDICTS}},
        )
        row["cases"] += 1
        r = recorded.get(d.id)
        if r is None:
            row["missing"] += 1
        else:
            row["recorded"] += 1
            row["verdict_" + r.verdict] = row.get("verdict_" + r.verdict, 0) + 1

    totals = {v: 0 for v in VERDICTS}
    for r in campaign.results:
        totals[r.verdict] = totals.get(r.verdict, 0) + 1

    return {
        "total": len(definitions),
        "recorded": sum(1 for d in definitions if d.id in recorded),
        "missing": sum(1 for d in definitions if d.id not in recorded),
        "orphans": [r.id for r in campaign.results if r.id not in {d.id for d in definitions}],
        "verdicts": totals,
        "per_set": per_set,
    }


def validate_campaign(
    definitions: list[Definition],
    campaign: Campaign,
    *,
    allow_partial: bool = False,
    suite_index: Optional[SuiteIndex] = None,
    tests_dir: Optional[Path] = None,
) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)``. Errors make the campaign invalid."""
    errors: list[str] = []
    warnings: list[str] = []
    def_ids = {d.id for d in definitions}

    seen: set[str] = set()
    for r in campaign.results:
        if r.verdict not in VERDICTS:
            errors.append(
                f"{r.id}: invalid verdict '{r.verdict}' "
                f"(expected one of {', '.join(VERDICTS)})"
            )
        if r.id in seen:
            errors.append(f"{r.id}: duplicate result entry")
        seen.add(r.id)
        if r.id not in def_ids:
            errors.append(f"{r.id}: result has no matching TestCase definition")

    for d in definitions:
        if d.id not in seen:
            message = f"{d.id}: missing result ({d.file})"
            (warnings if allow_partial else errors).append(message)

    # Documentation drift (warn only -- it must not block a campaign).
    index = suite_index or (parse_suite_index(tests_dir) if tests_dir else SuiteIndex())
    actual_counts: dict[str, int] = {}
    for d in definitions:
        actual_counts[d.file] = actual_counts.get(d.file, 0) + 1
    for filename, declared in index.declared_counts.items():
        actual = actual_counts.get(filename, 0)
        if declared != actual:
            warnings.append(
                f"TestSuite.md says {filename} has {declared} cases, found {actual}"
            )
    if index.declared_total is not None and index.declared_total != len(definitions):
        warnings.append(
            f"TestSuite.md says {index.declared_total} TestCases, found {len(definitions)}"
        )

    known_sets = _known_set_names(index)
    if known_sets:
        for d in definitions:
            for tag in d.sets:
                if tag not in known_sets:
                    warnings.append(f"{d.id}: unknown TestSet tag [{tag}]")

    return errors, warnings


def _known_set_names(index: SuiteIndex) -> set[str]:
    names = {Path(name).stem for name in index.order}
    names.update(index.cross_cutting)
    return names


def diff_campaigns(a: Campaign, b: Campaign, definitions: list[Definition]) -> dict[str, Any]:
    av = a.by_id()
    bv = b.by_id()
    order = [d.id for d in definitions]
    known = set(order)

    def sort_ids(ids: set[str]) -> list[str]:
        return [i for i in order if i in ids] + sorted(ids - known)

    both = set(av) & set(bv)
    regressions = []
    fixes = []
    changed = []
    for test_id in sort_ids(both):
        old, new = av[test_id].verdict, bv[test_id].verdict
        if old == new:
            continue
        entry = {"id": test_id, "from": old, "to": new}
        if old == PASSING and new in {"NOK"}:
            regressions.append(entry)
        elif old == "NOK" and new == PASSING:
            fixes.append(entry)
        else:
            changed.append(entry)

    return {
        "regressions": regressions,
        "fixes": fixes,
        "changed": changed,
        "added": [{"id": i, "to": bv[i].verdict} for i in sort_ids(set(bv) - set(av))],
        "removed": [{"id": i, "from": av[i].verdict} for i in sort_ids(set(av) - set(bv))],
        "unchanged": sum(1 for i in both if av[i].verdict == bv[i].verdict),
    }


# ── Rendering ─────────────────────────────────────────────────────────────────


def summary_lines(definitions: list[Definition], campaign: Campaign) -> list[str]:
    agg = aggregate(definitions, campaign)
    lines = [
        f"Campaign: {campaign.name}  ({campaign.date or 'no date'})",
        f"Recorded: {agg['recorded']}/{agg['total']}  missing: {agg['missing']}",
        "Verdicts: " + ", ".join(f"{v}={agg['verdicts'].get(v, 0)}" for v in VERDICTS),
    ]
    if agg["orphans"]:
        lines.append(f"Orphans: {len(agg['orphans'])}")

    lines.append("")
    lines.append("Per TestSet:")
    for set_name, row in agg["per_set"].items():
        lines.append(
            f"  {set_name:<16} recorded {row['recorded']}/{row['cases']:<3} "
            + "  ".join(f"{v}={row.get('verdict_' + v, 0)}" for v in VERDICTS)
            + (f"  missing={row['missing']}" if row["missing"] else "")
        )
    return lines


def render_markdown(definitions: list[Definition], campaign: Campaign) -> str:
    agg = aggregate(definitions, campaign)
    recorded = campaign.by_id()
    out: list[str] = [f"# System Test Campaign: {campaign.name}", ""]
    meta = [f"- **Date:** {campaign.date or 'not set'}"]
    if campaign.product_version:
        meta.append(f"- **Product version:** {campaign.product_version}")
    if campaign.operator:
        meta.append(f"- **Operator:** {campaign.operator}")
    if campaign.environment:
        meta.append(f"- **Environment:** {campaign.environment}")
    out.extend(meta)
    if campaign.note:
        out.extend(["", campaign.note])
    out.extend(
        [
            "",
            "## Summary",
            "",
            "| Verdict | Count |",
            "|---|---:|",
        ]
    )
    for v in VERDICTS:
        out.append(f"| {v} | {agg['verdicts'].get(v, 0)} |")
    out.append(f"| **Recorded** | {agg['recorded']}/{agg['total']} |")
    if agg["missing"]:
        out.append(f"| **Missing** | {agg['missing']} |")
    if agg["orphans"]:
        out.append(f"| **Orphans** | {len(agg['orphans'])} |")

    out.extend(["", "## Per TestSet", "", "| TestSet | Cases | OK | NOK | INCONCLUSIVE | NOT_TESTED | Missing |", "|---|---:|---:|---:|---:|---:|---:|"])
    for set_name, row in agg["per_set"].items():
        out.append(
            f"| {set_name} | {row['cases']} | "
            + " | ".join(str(row.get("verdict_" + v, 0)) for v in VERDICTS)
            + f" | {row['missing']} |"
        )

    non_ok = [r for r in campaign.results if r.verdict != PASSING]
    if non_ok:
        out.extend(["", "## Non-OK cases", ""])
        for r in non_ok:
            out.append(f"- **{r.id}** — {r.verdict}")
            if r.notes:
                for line in r.notes.splitlines():
                    out.append(f"  {line}")
        out.append("")

    noted_ok = [r for r in campaign.results if r.verdict == PASSING and r.notes]
    if noted_ok:
        out.extend(["", "## Notes on passing cases", ""])
        for r in noted_ok:
            out.append(f"- **{r.id}**")
            for line in r.notes.splitlines():
                out.append(f"  {line}")
        out.append("")

    if agg["missing"]:
        missing = [d.id for d in definitions if d.id not in recorded]
        out.extend(["", "## Missing results", ""])
        out.extend(f"- {i}" for i in missing)
        out.append("")

    return "\n".join(out).rstrip() + "\n"


# ── CLI commands ──────────────────────────────────────────────────────────────


def _context(args: argparse.Namespace) -> Context:
    root_arg = getattr(args, "root", None)
    tests_arg = getattr(args, "tests_dir", None)
    campaigns_arg = getattr(args, "campaigns_dir", None)
    root = Path(root_arg).resolve() if root_arg else Path(__file__).resolve().parent.parent
    tests_dir = Path(tests_arg).resolve() if tests_arg else root / "test"
    campaigns_dir = Path(campaigns_arg).resolve() if campaigns_arg else tests_dir / "campaigns"
    index = parse_suite_index(tests_dir)
    definition_list = parse_definitions(tests_dir, index)
    return Context(root, tests_dir, campaigns_dir, definition_list, index)


def _campaign_arg(args: argparse.Namespace, ctx: Context) -> Path:
    return resolve_campaign_path(args.campaign, ctx.campaigns_dir)


def _order_map(definitions: list[Definition]) -> dict[str, int]:
    return {d.id: d.order for d in definitions}


def cmd_new(args: argparse.Namespace, ctx: Context) -> int:
    name = args.name
    if name.endswith((".yaml", ".yml")):
        name = Path(name).stem
    path = ctx.campaigns_dir / f"{name}.yaml"
    if path.exists() and not args.force:
        raise TestReportError(f"{path} already exists (use --force to overwrite)")

    results: list[Result] = []
    note = args.note
    if args.from_campaign:
        source = load_campaign(resolve_campaign_path(args.from_campaign, ctx.campaigns_dir))
        source_by_id = source.by_id()
        for d in ctx.definitions:
            r = source_by_id.get(d.id)
            if r:
                results.append(Result(d.id, r.verdict, r.notes))
        if not note:
            note = f"Seeded from campaign '{source.name}'."
    else:
        if not note:
            note = ""

    campaign = Campaign(
        name=name,
        date=args.date or _dt.date.today().isoformat(),
        results=results,
        product_version=args.product_version or "",
        operator=args.operator or "",
        environment=args.environment or "",
        note=note,
    )
    save_campaign(campaign, path, _order_map(ctx.definitions))
    print(f"created {path}")
    print(f"  {len(results)}/{len(ctx.definitions)} cases seeded; record the rest with 'set'")
    return 0


def cmd_set(args: argparse.Namespace, ctx: Context) -> int:
    if args.verdict not in VERDICTS:
        raise TestReportError(
            f"invalid verdict '{args.verdict}' (expected one of {', '.join(VERDICTS)})"
        )
    ctx.definition(args.id)  # raises for unknown ids
    path = _campaign_arg(args, ctx)
    campaign = load_campaign(path)

    existing = campaign.get(args.id)
    if existing is None:
        existing = Result(args.id, args.verdict, "")
        campaign.results.append(existing)

    if args.notes is not None:
        if args.append and existing.notes:
            existing.notes = existing.notes.rstrip() + "\n" + args.notes
        else:
            existing.notes = args.notes
    existing.verdict = args.verdict

    save_campaign(campaign, path, _order_map(ctx.definitions))
    print(f"{args.id}: {args.verdict}")
    return 0


def cmd_show(args: argparse.Namespace, ctx: Context) -> int:
    definition = ctx.definition(args.id)
    campaign = load_campaign(_campaign_arg(args, ctx))
    result = campaign.get(args.id)

    print(f"{definition.id}  [{definition.file}]")
    if definition.sets:
        print(f"TestSets: {', '.join('[' + s + ']' for s in definition.sets)}")
    if result is None:
        print("Current: (not recorded)")
    else:
        print(f"Current: {result.verdict}")
        if result.notes:
            print("Notes:")
            for line in result.notes.splitlines():
                print(f"  {line}")
    print()
    print(definition.body)
    return 0


def cmd_status(args: argparse.Namespace, ctx: Context) -> int:
    campaign = load_campaign(_campaign_arg(args, ctx))
    agg = aggregate(ctx.definitions, campaign)
    print(f"Campaign: {campaign.name}  ({campaign.date or 'no date'})")
    print(f"Progress: {agg['recorded']}/{agg['total']} recorded, {agg['missing']} missing")
    print("Verdicts: " + ", ".join(f"{v}={agg['verdicts'].get(v, 0)}" for v in VERDICTS))
    print()
    print(f"{'TestSet':<16} {'done':>6}  verdicts")
    for set_name, row in agg["per_set"].items():
        verdicts = " ".join(
            f"{v}={row.get('verdict_' + v, 0)}" for v in VERDICTS if row.get("verdict_" + v, 0)
        )
        print(f"{set_name:<16} {row['recorded']:>3}/{row['cases']:<3} {verdicts}")

    recorded = campaign.by_id()
    pending = [d.id for d in ctx.definitions if d.id not in recorded]
    if pending:
        limit = max(0, args.next)
        if limit:
            print()
            print(f"Next {min(limit, len(pending))} unrecorded:")
            for test_id in pending[:limit]:
                print(f"  {test_id}")
    return 0


def cmd_validate(args: argparse.Namespace, ctx: Context) -> int:
    campaign = load_campaign(_campaign_arg(args, ctx))
    errors, warnings = validate_campaign(
        ctx.definitions,
        campaign,
        allow_partial=args.allow_partial,
        suite_index=ctx.suite_index,
        tests_dir=ctx.tests_dir,
    )
    if args.json:
        print(
            json.dumps(
                {
                    "ok": not errors,
                    "campaign": campaign.name,
                    "errors": errors,
                    "warnings": warnings,
                },
                indent=2,
            )
        )
    else:
        for message in errors:
            print(f"ERROR: {message}")
        for message in warnings:
            print(f"WARN:  {message}")
        if not errors and not warnings:
            print(f"{campaign.name}: OK ({len(campaign.results)} results)")
        elif not errors:
            print(f"{campaign.name}: OK with {len(warnings)} warning(s)")
        else:
            print(f"{campaign.name}: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


def cmd_summary(args: argparse.Namespace, ctx: Context) -> int:
    campaign = load_campaign(_campaign_arg(args, ctx))
    if args.json:
        print(json.dumps(_summary_json(ctx.definitions, campaign), indent=2))
        return 0

    print("\n".join(summary_lines(ctx.definitions, campaign)))
    failures = [r for r in campaign.results if r.verdict in FAILING]
    if failures:
        print()
        print("Failing / inconclusive:")
        for r in failures:
            first = r.notes.splitlines()[0] if r.notes else ""
            print(f"  {r.id}: {r.verdict}" + (f" — {first}" if first else ""))
    return 0


def _summary_json(definitions: list[Definition], campaign: Campaign) -> dict[str, Any]:
    agg = aggregate(definitions, campaign)
    return {
        "campaign": campaign.name,
        "date": campaign.date,
        "product_version": campaign.product_version,
        "operator": campaign.operator,
        "environment": campaign.environment,
        "total": agg["total"],
        "recorded": agg["recorded"],
        "missing": agg["missing"],
        "orphans": agg["orphans"],
        "verdicts": agg["verdicts"],
        "per_set": agg["per_set"],
    }


def cmd_render(args: argparse.Namespace, ctx: Context) -> int:
    campaign = load_campaign(_campaign_arg(args, ctx))
    if args.format == "json":
        text = json.dumps(_summary_json(ctx.definitions, campaign), indent=2) + "\n"
    else:
        text = render_markdown(ctx.definitions, campaign)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_diff(args: argparse.Namespace, ctx: Context) -> int:
    a = load_campaign(resolve_campaign_path(args.campaign_a, ctx.campaigns_dir))
    b = load_campaign(resolve_campaign_path(args.campaign_b, ctx.campaigns_dir))
    result = diff_campaigns(a, b, ctx.definitions)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{a.name} -> {b.name}")
        print(f"unchanged: {result['unchanged']}")
        for label, key in (
            ("regressions (OK->NOK)", "regressions"),
            ("fixes (NOK->OK)", "fixes"),
            ("other changes", "changed"),
            ("added", "added"),
            ("removed", "removed"),
        ):
            entries = result[key]
            if not entries:
                continue
            print(f"{label}: {len(entries)}")
            for e in entries:
                if "from" in e and "to" in e:
                    print(f"  {e['id']}: {e['from']} -> {e['to']}")
                elif "to" in e:
                    print(f"  {e['id']}: (new) {e['to']}")
                else:
                    print(f"  {e['id']}: (gone) {e['from']}")

    if result["regressions"] and args.fail_on_regression:
        return 1
    return 0


# ── Parser ────────────────────────────────────────────────────────────────────


def _add_campaign_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("campaign", help="campaign name or path (e.g. 2026-11-25-v0.2.0)")


def _add_common(parser: argparse.ArgumentParser) -> None:
    # SUPPRESS lets the same option be given before or after the subcommand
    # without one parser's default clobbering the other's value.
    parser.add_argument("--root", default=argparse.SUPPRESS, help="repository root (default: parent of tools/)")
    parser.add_argument("--tests-dir", default=argparse.SUPPRESS, help="directory holding the TestSet .md files")
    parser.add_argument("--campaigns-dir", default=argparse.SUPPRESS, help="directory holding campaign .yaml files")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="test_report.py",
        description="Record, validate and report on manual system-test campaigns.",
        epilog="See tools/TEST_REPORT_HOWTO.md for the workflow.",
    )
    _add_common(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    p_new = sub.add_parser("new", help="create a new (empty) campaign")
    _add_common(p_new)
    p_new.add_argument("name", help="campaign name (a .yaml suffix is stripped)")
    p_new.add_argument("--from", dest="from_campaign", help="seed verdicts from an existing campaign")
    p_new.add_argument("--date", help="campaign date (default: today)")
    p_new.add_argument("--product-version")
    p_new.add_argument("--operator")
    p_new.add_argument("--environment")
    p_new.add_argument("--note")
    p_new.add_argument("--force", action="store_true", help="overwrite an existing file")
    p_new.set_defaults(func=cmd_new)

    p_set = sub.add_parser("set", help="record one case's verdict/notes")
    _add_common(p_set)
    _add_campaign_arg(p_set)
    p_set.add_argument("id", help="TestCase id, e.g. TC_CAN_001_send_frame_cli")
    p_set.add_argument("verdict", choices=VERDICTS)
    p_set.add_argument("--notes", help="notes/evidence (replaces existing unless --append)")
    p_set.add_argument("--append", action="store_true", help="append notes instead of replacing")
    p_set.set_defaults(func=cmd_set)

    p_show = sub.add_parser("show", help="print a case's procedure and its current result")
    _add_common(p_show)
    _add_campaign_arg(p_show)
    p_show.add_argument("id")
    p_show.set_defaults(func=cmd_show)

    p_status = sub.add_parser("status", help="show progress through a campaign")
    _add_common(p_status)
    _add_campaign_arg(p_status)
    p_status.add_argument("--next", type=int, default=5, help="how many unrecorded ids to list (default 5)")
    p_status.set_defaults(func=cmd_status)

    p_validate = sub.add_parser("validate", help="check a campaign for completeness/consistency")
    _add_common(p_validate)
    _add_campaign_arg(p_validate)
    p_validate.add_argument(
        "--allow-partial",
        action="store_true",
        help="treat unrecorded cases as warnings instead of errors",
    )
    p_validate.add_argument("--json", action="store_true")
    p_validate.set_defaults(func=cmd_validate)

    p_summary = sub.add_parser("summary", help="print verdict totals and non-OK cases")
    _add_common(p_summary)
    _add_campaign_arg(p_summary)
    p_summary.add_argument("--json", action="store_true")
    p_summary.set_defaults(func=cmd_summary)

    p_render = sub.add_parser("render", help="render a sign-off report")
    _add_common(p_render)
    _add_campaign_arg(p_render)
    p_render.add_argument("--out", help="write to a file instead of stdout")
    p_render.add_argument("--format", choices=("md", "json"), default="md")
    p_render.set_defaults(func=cmd_render)

    p_diff = sub.add_parser("diff", help="compare two campaigns")
    _add_common(p_diff)
    p_diff.add_argument("campaign_a")
    p_diff.add_argument("campaign_b")
    p_diff.add_argument("--fail-on-regression", action="store_true", help="exit 1 if OK->NOK")
    p_diff.add_argument("--json", action="store_true")
    p_diff.set_defaults(func=cmd_diff)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        ctx = _context(args)
        return args.func(args, ctx)
    except TestReportError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

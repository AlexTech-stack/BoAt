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

``review`` is the interactive path: a full-screen curses session over the suite
order, with one-key verdicts, notes editing, undo and filtering. Without a TTY
(or with ``--plain``) the same session runs as a line-based prompt. The current
campaign is remembered in ``test/.test_report_state.json`` (gitignored), so the
campaign name does not have to be repeated.

See ``tools/TEST_REPORT_HOWTO.md`` for the day-to-day workflow.
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

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

# Remembered current campaign. Repo-local on purpose: a campaign name belongs
# to this checkout's test/campaigns/, not to the user or another repository.
STATE_FILE_NAME = ".test_report_state.json"

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


def _atomic_write(path: Path, text: str) -> None:
    """Write via a same-directory temp file + rename.

    Campaign and state files are written after every recorded verdict; a
    half-written YAML file from an interrupted write would lose the whole run.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _state_path(ctx: "Context") -> Path:
    return ctx.tests_dir / STATE_FILE_NAME


def read_state(ctx: "Context") -> dict[str, Any]:
    path = _state_path(ctx)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def set_current_campaign(ctx: "Context", name: str) -> None:
    data = read_state(ctx)
    data["campaign"] = name
    _atomic_write(_state_path(ctx), json.dumps(data, indent=2) + "\n")


def current_campaign_name(ctx: "Context") -> Optional[str]:
    env = os.environ.get("BOAT_TEST_CAMPAIGN", "").strip()
    if env:
        return env
    remembered = read_state(ctx).get("campaign")
    return str(remembered) if remembered else None


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
    _atomic_write(path, text)


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


# ── Resolution / recording helpers ────────────────────────────────────────────


def resolve_definition(query: str, definitions: list[Definition]) -> Definition:
    """Resolve an exact id, a unique abbreviation, or ``@N`` (1-based run order)."""
    raw = query.strip()
    if not raw:
        raise TestReportError("test id must not be empty")
    if raw.startswith("@"):
        try:
            index = int(raw[1:])
        except ValueError as exc:
            raise TestReportError(f"invalid run-order index: {raw}") from exc
        if 1 <= index <= len(definitions):
            return definitions[index - 1]
        raise TestReportError(f"run-order index {index} out of range 1..{len(definitions)}")

    lower = raw.lower()
    exact = [d for d in definitions if d.id.lower() == lower]
    if len(exact) == 1:
        return exact[0]

    def short(d: Definition) -> str:
        return d.id[3:] if d.id.lower().startswith("tc_") else d.id

    suffix = [d for d in definitions if d.id.lower().endswith("_" + lower)]
    prefix = [
        d
        for d in definitions
        if short(d).lower().startswith(lower) or d.id.lower().startswith(lower)
    ]
    contains = [d for d in definitions if lower in d.id.lower()]

    for bucket in (suffix, prefix, contains):
        if len(bucket) == 1:
            return bucket[0]
        if bucket:
            listing = ", ".join(d.id for d in bucket[:8])
            more = "" if len(bucket) <= 8 else f" … (+{len(bucket) - 8})"
            raise TestReportError(
                f"ambiguous test id '{query}' matches {len(bucket)}: {listing}{more}"
            )
    raise TestReportError(f"unknown test id: {query}")


def apply_result(
    campaign: Campaign,
    test_id: str,
    verdict: str,
    notes: Optional[str],
    *,
    append: bool = False,
) -> Result:
    if verdict not in VERDICTS:
        raise TestReportError(
            f"invalid verdict '{verdict}' (expected one of {', '.join(VERDICTS)})"
        )
    existing = campaign.get(test_id)
    if existing is None:
        existing = Result(test_id, verdict, "")
        campaign.results.append(existing)
    if notes is not None:
        if append and existing.notes:
            existing.notes = existing.notes.rstrip() + "\n" + notes
        else:
            existing.notes = notes
    existing.verdict = verdict
    return existing


def edit_notes(initial: str) -> str:
    """Open ``$VISUAL``/``$EDITOR`` on a temp file and return the saved text."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    fd, name = tempfile.mkstemp(prefix="boat-test-notes-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(initial)
        subprocess.call(shlex.split(editor) + [name])
        return Path(name).read_text(encoding="utf-8").rstrip("\n")
    finally:
        if os.path.exists(name):
            os.unlink(name)


# ── Interactive review ────────────────────────────────────────────────────────

FILTER_CYCLE = ("all", "unrecorded", "failures", "ok", "not_tested", "set")
VERDICT_MARK = {
    None: " ",
    "OK": "+",
    "NOK": "x",
    "INCONCLUSIVE": "?",
    "NOT_TESTED": "-",
}

SECTION_PREFIXES = (
    "**Preconditions:**",
    "**TestSteps:**",
    "**Expected:**",
    "Notes:",
    "Previous notes",
)


def section_offsets(lines: list[tuple[str, int]]) -> list[int]:
    """Rendered line indices that start a detail-pane section."""
    offsets: list[int] = []
    for index, (text, _attr) in enumerate(lines):
        stripped = text.strip()
        if index == 0 or stripped.startswith(SECTION_PREFIXES):
            offsets.append(index)
    return offsets


def osc52_sequence(text: str) -> str:
    """OSC 52 clipboard sequence (base64), understood by most terminals/tmux."""
    payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return "\x1b]52;c;" + payload + "\x07"


@dataclass
class ReviewFilter:
    kind: str = "all"
    text: str = ""
    tag: str = ""
    set_name: str = ""
    verdict: Optional[str] = None
    unrecorded: bool = False

    def matches(self, definition: Definition, result: Optional[Result]) -> bool:
        if self.set_name and definition.set_name != self.set_name:
            return False
        if self.tag and self.tag not in definition.sets:
            return False
        if self.text:
            needle = self.text.lower()
            haystack = (
                definition.id + " " + " ".join(definition.sets) + " " + definition.body
            ).lower()
            if needle not in haystack:
                return False
        if self.verdict:
            return result is not None and result.verdict == self.verdict
        if self.unrecorded and result is not None:
            return False
        if self.kind == "unrecorded":
            return result is None
        if self.kind == "failures":
            return result is not None and result.verdict in FAILING
        if self.kind == "ok":
            return result is not None and result.verdict == PASSING
        if self.kind == "not_tested":
            return result is not None and result.verdict == "NOT_TESTED"
        return True

    def label(self) -> str:
        parts: list[str] = []
        if self.verdict:
            parts.append(self.verdict)
        if self.set_name:
            parts.append(f"Set: {self.set_name}")
        elif self.kind == "set":
            parts.append("Set: any")
        elif self.kind != "all":
            parts.append(self.kind.replace("_", " ").title())
        if self.unrecorded and self.kind != "unrecorded":
            parts.append("Unrecorded")
        if self.tag:
            parts.append(f"[{self.tag}]")
        if self.text:
            parts.append(f"/{self.text}")
        return ", ".join(parts) if parts else "All"


class LineEditor:
    """Minimal line editor for TUI prompts (pure, so Esc/Enter are testable).

    ``feed`` returns ``None`` to keep editing, ``("accept", text)`` on Enter, or
    ``("cancel", None)`` on Esc. Backspace and Ctrl-U edit the buffer.
    """

    def __init__(self, text: str = "") -> None:
        self.text = text

    def feed(self, key) -> Optional[tuple[str, Optional[str]]]:
        import curses

        if isinstance(key, str):
            if key in ("\n", "\r"):
                return ("accept", self.text)
            if key == "\x1b":
                return ("cancel", None)
            if key in ("\x7f", "\b"):
                self.text = self.text[:-1]
            elif key == "\x15":
                self.text = ""
            elif key.isprintable() and key != "\t":
                self.text += key
            return None

        if key in (curses.KEY_ENTER, 10, 13):
            return ("accept", self.text)
        if key == 27:
            return ("cancel", None)
        if key in (curses.KEY_BACKSPACE, 127, 8):
            self.text = self.text[:-1]
        elif key == 21:
            self.text = ""
        elif 32 <= key <= 0x10FFFF:
            try:
                self.text += chr(key)
            except ValueError:
                pass
        return None


@dataclass
class FilterForm:
    """State of the modal filter editor. Field order: Set, Tag, Verdict,
    Unrecorded, Text."""

    sets: list[str]
    tags: list[str]
    set_index: int = 0          # 0 = any
    tag_index: int = 0          # 0 = any
    verdict_index: int = 0      # 0 = any, then VERDICTS order
    unrecorded: bool = False
    text: str = ""
    focus: int = 0
    field_count: int = 5

    @classmethod
    def from_filter(
        cls, current: ReviewFilter, sets: list[str], tags: list[str]
    ) -> "FilterForm":
        set_index = sets.index(current.set_name) + 1 if current.set_name in sets else 0
        tag_index = tags.index(current.tag) + 1 if current.tag in tags else 0
        verdict_index = (
            VERDICTS.index(current.verdict) + 1
            if current.verdict in VERDICTS
            else 0
        )
        return cls(
            sets=sets,
            tags=tags,
            set_index=set_index,
            tag_index=tag_index,
            verdict_index=verdict_index,
            unrecorded=current.unrecorded,
            text=current.text,
        )

    def to_filter(self) -> ReviewFilter:
        set_name = self.sets[self.set_index - 1] if self.set_index else ""
        tag = self.tags[self.tag_index - 1] if self.tag_index else ""
        verdict = (
            VERDICTS[self.verdict_index - 1] if self.verdict_index else None
        )
        return ReviewFilter(
            kind="set" if set_name else "all",
            set_name=set_name,
            tag=tag,
            verdict=verdict,
            unrecorded=self.unrecorded,
            text=self.text,
        )

    def preview_count(self, definitions: list[Definition], recorded: dict[str, Result]) -> int:
        review_filter = self.to_filter()
        return sum(
            1
            for definition in definitions
            if review_filter.matches(definition, recorded.get(definition.id))
        )

    def clear(self) -> None:
        self.set_index = 0
        self.tag_index = 0
        self.verdict_index = 0
        self.unrecorded = False
        self.text = ""
        self.focus = 0

    def _cycle(self, index: int, size: int, delta: int) -> int:
        return (index + delta) % size

    def handle_key(self, key) -> Optional[str]:
        """Return ``"apply"``, ``"cancel"``, ``"clear"`` or ``None`` (continue)."""
        import curses

        char = ""
        backspace = clear_line = tab = btab = left = right = up = down = False

        if isinstance(key, str):
            if key in ("\n", "\r"):
                return "apply"
            if key == "\x1b":
                return "cancel"
            if key == "\t":
                tab = True
            elif key in ("\x7f", "\b"):
                backspace = True
            elif key == "\x15":
                clear_line = True
            elif key.isprintable():
                char = key
        else:
            if key in (curses.KEY_ENTER, 10, 13):
                return "apply"
            if key == 27:
                return "cancel"
            if key == 9:
                tab = True
            elif key == curses.KEY_BTAB:
                btab = True
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                backspace = True
            elif key == 21:
                clear_line = True
            elif key == curses.KEY_LEFT:
                left = True
            elif key == curses.KEY_RIGHT:
                right = True
            elif key == curses.KEY_UP:
                up = True
            elif key == curses.KEY_DOWN:
                down = True
            elif 32 <= key <= 0x10FFFF:
                char = chr(key)

        if tab:
            self.focus = (self.focus + 1) % self.field_count
            return None
        if btab:
            self.focus = (self.focus - 1) % self.field_count
            return None

        if self.focus in (0, 1, 2):
            if char == "c" or clear_line:
                self.clear()
                return "clear"
            delta = -1 if (left or up or char in ("h", "k")) else (
                1 if (right or down or char in ("j", "l")) else 0
            )
            if delta:
                if self.focus == 0:
                    self.set_index = self._cycle(self.set_index, len(self.sets) + 1, delta)
                elif self.focus == 1:
                    self.tag_index = self._cycle(self.tag_index, len(self.tags) + 1, delta)
                else:
                    self.verdict_index = self._cycle(
                        self.verdict_index, len(VERDICTS) + 1, delta
                    )
            return None

        if self.focus == 3:
            if char == " " or left or right or up or down:
                self.unrecorded = not self.unrecorded
            elif char == "c":
                self.clear()
                return "clear"
            return None

        # Text field: typing edits the value; 'c'/space are literal characters.
        if backspace:
            self.text = self.text[:-1]
        elif clear_line:
            self.text = ""
        elif char:
            self.text += char
        return None

    def field_label(self, index: int) -> str:
        return ("Set", "Tag", "Verdict", "Unrecorded", "Text")[index]

    def field_value(self, index: int) -> str:
        if index == 0:
            return self.sets[self.set_index - 1] if self.set_index else "(any)"
        if index == 1:
            return self.tags[self.tag_index - 1] if self.tag_index else "(any)"
        if index == 2:
            return VERDICTS[self.verdict_index - 1] if self.verdict_index else "(any)"
        if index == 3:
            return "on" if self.unrecorded else "off"
        return self.text


class ReviewSession:
    """State/behaviour of one review session, independent of the renderer."""

    def __init__(
        self,
        ctx: Context,
        campaign_path: Path,
        *,
        compare_path: Optional[Path] = None,
        review_filter: Optional[ReviewFilter] = None,
        start: Optional[str] = None,
    ) -> None:
        self.ctx = ctx
        self.campaign_path = campaign_path
        self.campaign = load_campaign(campaign_path)
        self.compare = load_campaign(compare_path) if compare_path else None
        self.order = _order_map(ctx.definitions)
        self.filter = review_filter or ReviewFilter()
        self.undo_stack: list[dict[str, Any]] = []
        self.visible: list[str] = []
        self.cursor = 0
        self.detail_scroll = 0
        self.messages: list[str] = []
        self.rebuild(keep_id=start, first_unrecorded=start is None)

    # ── queries

    def current(self) -> Optional[Definition]:
        if not self.visible:
            return None
        test_id = self.visible[self.cursor]
        for definition in self.ctx.definitions:
            if definition.id == test_id:
                return definition
        return None

    def current_result(self) -> Optional[Result]:
        definition = self.current()
        return self.campaign.get(definition.id) if definition else None

    def compare_result(self, test_id: str) -> Optional[Result]:
        return self.compare.get(test_id) if self.compare else None

    def recorded_count(self) -> tuple[int, int]:
        recorded = self.campaign.by_id()
        return (
            sum(1 for d in self.ctx.definitions if d.id in recorded),
            len(self.ctx.definitions),
        )

    # ── navigation / filtering

    def rebuild(
        self,
        *,
        keep_id: Optional[str] = None,
        first_unrecorded: bool = False,
    ) -> None:
        recorded = self.campaign.by_id()
        self.visible = [
            d.id for d in self.ctx.definitions if self.filter.matches(d, recorded.get(d.id))
        ]
        if keep_id and keep_id in self.visible:
            self.cursor = self.visible.index(keep_id)
        elif first_unrecorded:
            self.cursor = next(
                (i for i, test_id in enumerate(self.visible) if test_id not in recorded),
                0,
            )
        else:
            self.cursor = min(self.cursor, max(0, len(self.visible) - 1))
        self.detail_scroll = 0

    def move(self, delta: int) -> None:
        if not self.visible:
            return
        self.cursor = max(0, min(len(self.visible) - 1, self.cursor + delta))
        self.detail_scroll = 0

    def first(self) -> None:
        self.cursor = 0
        self.detail_scroll = 0

    def last(self) -> None:
        self.cursor = max(0, len(self.visible) - 1)
        self.detail_scroll = 0

    def advance(self) -> None:
        # Next case in the current filter, recorded or not: a re-verification
        # pass (`--compare`) wants to walk every case, and a fresh run is
        # already filtered to the unrecorded ones.
        self.move(1)

    def jump_to_run_order(self, index: int) -> None:
        if 1 <= index <= len(self.ctx.definitions):
            definition = self.ctx.definitions[index - 1]
            self.filter = ReviewFilter()
            self.rebuild(keep_id=definition.id)

    def cycle_filter(self) -> None:
        index = FILTER_CYCLE.index(self.filter.kind) + 1
        self.filter.kind = FILTER_CYCLE[index % len(FILTER_CYCLE)]
        self.filter.unrecorded = False
        self.filter.verdict = None
        self.filter.set_name = ""
        if self.filter.kind == "set":
            definition = self.current() or (self.ctx.definitions[0] if self.ctx.definitions else None)
            self.filter.set_name = definition.set_name if definition else ""
        self.rebuild(first_unrecorded=False)

    def set_text_filter(self, text: str) -> None:
        self.filter.text = text
        self.rebuild(first_unrecorded=False)

    def set_filter(self, review_filter: ReviewFilter, keep_id: Optional[str] = None) -> None:
        if keep_id is None:
            current = self.current()
            keep_id = current.id if current else None
        self.filter = review_filter
        self.rebuild(keep_id=keep_id, first_unrecorded=False)

    def clear_filter(self, keep_id: Optional[str] = None) -> None:
        self.set_filter(ReviewFilter(), keep_id=keep_id)

    def scroll_detail(self, delta: int) -> None:
        self.detail_scroll = max(0, self.detail_scroll + delta)

    def scroll_detail_to(self, offset: int) -> None:
        self.detail_scroll = max(0, offset)

    # ── recording

    def _snapshot(self, test_id: str) -> None:
        old = self.campaign.get(test_id)
        self.undo_stack.append(
            {
                "id": test_id,
                "result": Result(old.id, old.verdict, old.notes) if old else None,
            }
        )

    def _save(self) -> None:
        save_campaign(self.campaign, self.campaign_path, self.order)

    def set_verdict(self, verdict: str, notes: Optional[str] = None) -> Optional[Definition]:
        definition = self.current()
        if definition is None:
            return None
        self._snapshot(definition.id)
        apply_result(self.campaign, definition.id, verdict, notes)
        self._save()
        return definition

    def set_notes(self, notes: str) -> Optional[Definition]:
        definition = self.current()
        if definition is None:
            return None
        result = self.campaign.get(definition.id)
        verdict = result.verdict if result else "NOT_TESTED"
        self._snapshot(definition.id)
        apply_result(self.campaign, definition.id, verdict, notes)
        self._save()
        return definition

    def undo(self) -> Optional[str]:
        if not self.undo_stack:
            return None
        snapshot = self.undo_stack.pop()
        self.campaign.results = [r for r in self.campaign.results if r.id != snapshot["id"]]
        if snapshot["result"] is not None:
            self.campaign.results.append(snapshot["result"])
        self._save()
        self.rebuild(keep_id=snapshot["id"])
        return snapshot["id"]

    def reload_campaign(self, path: Path) -> None:
        self.campaign_path = path
        self.campaign = load_campaign(path)
        self.undo_stack.clear()
        self.rebuild(first_unrecorded=True)


class ReviewPlain:
    """Line-based fallback: non-TTY, ``--plain``, or when curses is unavailable."""

    def __init__(
        self,
        session: ReviewSession,
        *,
        input_fn: Callable[[str], str] = input,
        output: Callable[[str], None] = print,
        ask_notes: bool = True,
    ) -> None:
        self.session = session
        self.input_fn = input_fn
        self.output = output
        self.ask_notes = ask_notes

    def run(self) -> int:
        while True:
            definition = self.session.current()
            if definition is None:
                self.output("No cases match the current filter.")
                break
            self._render(definition)
            try:
                choice = self.input_fn(
                    "Verdict [o]k [n]ok [i]nconclusive [s]kip [e]dit [b]ack "
                    "[u]ndo [f]ilter [q]uit  /text  @N: "
                ).strip()
            except EOFError:
                break
            try:
                if not self._dispatch(choice):
                    break
            except TestReportError as exc:
                self.output(f"error: {exc}")
        done, total = self.session.recorded_count()
        self.output(f"\nRecorded {done}/{total}. Save/commit the campaign when finished.")
        return 0

    def _render(self, definition: Definition) -> None:
        result = self.session.current_result()
        previous = self.session.compare_result(definition.id)
        self.output("")
        self.output(
            f"[{self.session.cursor + 1}/{len(self.session.visible)}] {definition.id}  "
            f"[{definition.file}]  filter: {self.session.filter.label()}"
        )
        self.output("TestSets: " + ", ".join(f"[{s}]" for s in definition.sets))
        current = f"Current: {result.verdict}" if result else "Current: (not recorded)"
        if previous:
            current += f"   Previous: {previous.verdict}"
        self.output(current)
        if result and result.notes:
            self.output("Notes: " + result.notes)
        self.output(definition.body)

    def _dispatch(self, choice: str) -> bool:
        lowered = choice.lower()
        if lowered in ("q", "quit", "exit"):
            return False
        if lowered in ("o", "ok"):
            self._record("OK")
        elif lowered in ("n", "nok"):
            self._record("NOK")
        elif lowered in ("i", "inconclusive"):
            self._record("INCONCLUSIVE")
        elif lowered in ("s", "skip", "not_tested"):
            self._record("NOT_TESTED")
        elif lowered in ("e", "edit", "notes"):
            self._edit()
        elif lowered in ("b", "back", "k"):
            self.session.move(-1)
        elif lowered in ("j", ""):
            self.session.move(1)
        elif lowered == "g":
            self.session.first()
        elif choice == "G":
            self.session.last()
        elif lowered in ("u", "undo"):
            test_id = self.session.undo()
            self.output(f"undo: {test_id}" if test_id else "nothing to undo")
        elif lowered == "f":
            self.session.cycle_filter()
            self.output(f"filter: {self.session.filter.label()}")
        elif lowered == "x":
            self.session.clear_filter()
            self.output("filters cleared")
        elif choice.startswith("/"):
            self.session.set_text_filter(choice[1:].strip())
            self.output(f"filter: {self.session.filter.label()}")
        elif choice.startswith("@"):
            try:
                self.session.jump_to_run_order(int(choice[1:]))
            except ValueError:
                self.output("invalid run-order index (use @N)")
        elif lowered in ("?", "h", "help"):
            self.output(
                "o/n/i/s verdict · e edit notes · u undo · b/j move · g/G first/last\n"
                "/text filter · f cycle filter · x clear filters · @N jump to run order N · q quit"
            )
        else:
            self.output("unknown key (o/n/i/s/e/u/b/j/g/G/f/q, /text, @N, ?)")
        return True

    def _record(self, verdict: str) -> None:
        definition = self.session.set_verdict(verdict)
        if definition is None:
            return
        if self.ask_notes:
            result = self.session.current_result()
            initial = result.notes if result else ""
            try:
                notes = self.input_fn(
                    "Notes (Enter=keep, '-'=clear, ':e'=editor): "
                ).strip()
            except EOFError:
                notes = ""
            if notes == ":e":
                self.session.set_notes(edit_notes(initial))
            elif notes == "-":
                self.session.set_notes("")
            elif notes:
                self.session.set_notes(notes)
        self.session.advance()

    def _edit(self) -> None:
        result = self.session.current_result()
        initial = result.notes if result else ""
        self.session.set_notes(edit_notes(initial))


class ReviewTUI:
    """Full-screen curses reviewer over a :class:`ReviewSession`."""

    def __init__(
        self,
        session: ReviewSession,
        *,
        ask_notes: bool = True,
        ctx: Optional[Context] = None,
    ) -> None:
        self.session = session
        self.ctx = ctx or session.ctx
        self.ask_notes = ask_notes
        self.running = True
        self.show_help = False
        self._status = ""
        self._status_until = 0.0
        self._list_offset = 0
        self._section_offsets: list[int] = []
        self._detail_text = ""

    # ── curses lifecycle

    def run(self) -> int:
        import curses

        curses.wrapper(self._main)
        done, total = self.session.recorded_count()
        print(f"Recorded {done}/{total}.")
        return 0

    def _main(self, stdscr) -> None:
        import curses
        import locale

        try:
            locale.setlocale(locale.LC_ALL, "")
        except locale.Error:
            pass
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        stdscr.keypad(True)
        self._init_colors()

        while self.running:
            self._draw(stdscr)
            try:
                key = stdscr.getch()
            except KeyboardInterrupt:
                break
            if key == curses.KEY_RESIZE:
                continue
            if not self._handle_key(stdscr, key):
                break

    def _init_colors(self) -> None:
        import curses

        self.color = {
            "OK": 0,
            "NOK": 0,
            "INCONCLUSIVE": 0,
            "NOT_TESTED": 0,
            "header": 0,
        }
        if not curses.has_colors():
            return
        curses.start_color()
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:
            background = curses.COLOR_BLACK
        curses.init_pair(1, curses.COLOR_GREEN, background)
        curses.init_pair(2, curses.COLOR_RED, background)
        curses.init_pair(3, curses.COLOR_YELLOW, background)
        curses.init_pair(4, curses.COLOR_CYAN, background)
        self.color["OK"] = curses.color_pair(1) | curses.A_BOLD
        self.color["NOK"] = curses.color_pair(2) | curses.A_BOLD
        self.color["INCONCLUSIVE"] = curses.color_pair(3) | curses.A_BOLD
        self.color["NOT_TESTED"] = curses.color_pair(3)
        self.color["header"] = curses.color_pair(4) | curses.A_BOLD

    # ── key handling (separated from rendering for tests)

    def _handle_key(self, stdscr, key: int) -> bool:
        import curses

        if key in (ord("q"), ord("Q")):
            return False
        if key in (ord("j"), curses.KEY_DOWN):
            self.session.move(1)
        elif key in (ord("k"), curses.KEY_UP):
            self.session.move(-1)
        elif key == ord("J"):
            self.session.scroll_detail(1)
        elif key == ord("K"):
            self.session.scroll_detail(-1)
        elif key == curses.KEY_NPAGE:
            self.session.scroll_detail(10)
        elif key == curses.KEY_PPAGE:
            self.session.scroll_detail(-10)
        elif key == ord("{"):
            self._jump_section(-1)
        elif key == ord("}"):
            self._jump_section(1)
        elif key == ord("g"):
            self.session.first()
        elif key in (ord("G"), curses.KEY_END):
            self.session.last()
        elif key == ord("y"):
            definition = self.session.current()
            if definition is not None:
                self._copy(stdscr, definition.id, "case id")
        elif key == ord("Y"):
            if self._detail_text:
                self._copy(stdscr, self._detail_text, "definition")
        elif key == ord("o"):
            self._record(stdscr, "OK")
        elif key == ord("n"):
            self._record(stdscr, "NOK")
        elif key == ord("i"):
            self._record(stdscr, "INCONCLUSIVE")
        elif key == ord("s"):
            self._record(stdscr, "NOT_TESTED")
        elif key == ord("e"):
            self._edit(stdscr)
        elif key == ord("u"):
            test_id = self.session.undo()
            self._set_status(f"undo: {test_id}" if test_id else "nothing to undo")
        elif key == ord("f"):
            self.session.cycle_filter()
            self._set_status(f"filter: {self.session.filter.label()}")
        elif key == ord("F"):
            self._filter_modal(stdscr)
        elif key == ord("x"):
            self.session.clear_filter()
            self._set_status("filters cleared")
        elif key == ord("/"):
            text = self._read_line(stdscr, "Filter text: ")
            if text is None:
                self._set_status("search cancelled")
            else:
                self.session.set_text_filter(text.strip())
                self._set_status(f"filter: {self.session.filter.label()}")
        elif key == 27:
            if self.show_help:
                self.show_help = False
        elif key == ord("c"):
            self._change_campaign(stdscr)
        elif key in (ord("?"), ord("h")):
            self.show_help = not self.show_help
        return True

    def _jump_section(self, direction: int) -> None:
        offsets = self._section_offsets
        if not offsets:
            self._set_status("no sections in this definition")
            return
        current = self.session.detail_scroll
        if direction < 0:
            previous = [offset for offset in offsets if offset < current]
            if not previous:
                self._set_status("already at the first section")
                return
            self.session.scroll_detail_to(max(previous))
        else:
            following = [offset for offset in offsets if offset > current]
            if not following:
                self._set_status("already at the last section")
                return
            self.session.scroll_detail_to(min(following))

    def _copy(self, stdscr, text: str, label: str) -> None:
        import curses

        try:
            os.write(sys.stdout.fileno(), osc52_sequence(text).encode("ascii"))
            self._set_status(f"copied {label} ({len(text)} chars)")
        except OSError as exc:
            self._set_status(f"clipboard failed: {exc}")
        if stdscr is not None:
            try:
                stdscr.redrawwin()
                stdscr.refresh()
            except (curses.error, AttributeError):
                pass

    def _record(self, stdscr, verdict: str) -> None:
        definition = self.session.set_verdict(verdict)
        if definition is None:
            return
        if self.ask_notes:
            result = self.session.current_result()
            initial = result.notes if result else ""
            notes = self._read_line(stdscr, "Notes (Enter=keep, Esc=cancel): ", initial)
            if notes is None or notes == initial:
                pass
            elif notes == "-":
                self.session.set_notes("")
            else:
                # Clearing the field and pressing Enter clears the notes.
                self.session.set_notes(notes)
        self.session.advance()
        self._set_status(f"{definition.id}: {verdict}")

    def _edit(self, stdscr) -> None:
        import curses

        result = self.session.current_result()
        initial = result.notes if result else ""
        curses.def_prog_mode()
        curses.endwin()
        try:
            notes = edit_notes(initial)
        finally:
            curses.reset_prog_mode()
            stdscr.refresh()
        if notes != initial:
            self.session.set_notes(notes)

    def _change_campaign(self, stdscr) -> None:
        name = self._read_line(stdscr, "Campaign name: ")
        if name is None:
            return
        name = name.strip()
        if not name:
            self._set_status("use 'use CAMPAIGN' to switch campaigns")
            return
        try:
            path = resolve_campaign_path(name, self.ctx.campaigns_dir)
            self.session.reload_campaign(path)
            set_current_campaign(self.ctx, path.stem)
            self._set_status(f"campaign: {path.stem}")
        except TestReportError as exc:
            self._set_status(f"error: {exc}")

    # ── rendering

    def _set_status(self, message: str) -> None:
        self._status = message
        self._status_until = time.time() + 4.0

    def _safe(self, stdscr, y: int, x: int, text: str, attr: int = 0) -> None:
        import curses

        height, width = stdscr.getmaxyx()
        if y < 0 or y >= height or x < 0 or x >= width:
            return
        try:
            stdscr.addnstr(y, x, text, max(0, width - x), attr)
        except curses.error:
            try:
                stdscr.addnstr(y, x, text.encode("ascii", "replace").decode(), max(0, width - x), attr)
            except curses.error:
                pass

    def _draw(self, stdscr) -> None:
        import curses

        stdscr.erase()
        height, width = stdscr.getmaxyx()
        if height < 16 or width < 60:
            self._safe(stdscr, 0, 0, "Terminal too small: need at least 60x16 (q to quit)")
            stdscr.refresh()
            return

        done, total = self.session.recorded_count()
        compare = f"  Compare: {self.session.compare.name}" if self.session.compare else ""
        header = (
            f" Campaign: {self.session.campaign.name}   "
            f"Filter: {self.session.filter.label()}   "
            f"Recorded {done}/{total}{compare}"
        )
        self._safe(stdscr, 0, 0, header.ljust(width), self.color["header"] | curses.A_REVERSE)

        footer_rows = 2
        list_width = min(46, max(30, width // 3))
        top = 1
        bottom = height - footer_rows - 1
        pane_height = max(1, bottom - top)

        definition = self.session.current()
        result = self.session.current_result()

        detail_x = list_width + 2
        detail_width = max(10, width - detail_x - 1)

        # Pane titles (no focus mode: the list always owns j/k, the detail
        # pane owns J/K and PgUp/PgDn).
        self._safe(stdscr, top, 0, " Cases"[:list_width].ljust(list_width), curses.A_BOLD)
        self._safe(
            stdscr,
            top,
            detail_x,
            " Details"[:detail_width].ljust(detail_width),
            curses.A_BOLD,
        )

        # Separator
        for y in range(top, bottom + 1):
            self._safe(stdscr, y, list_width, "│")

        # List rows
        visible = self.session.visible
        if self.session.cursor < self._list_offset:
            self._list_offset = self.session.cursor
        if self.session.cursor >= self._list_offset + pane_height - 1:
            self._list_offset = self.session.cursor - pane_height + 2
        recorded = self.session.campaign.by_id()
        for row in range(pane_height - 1):
            index = self._list_offset + row
            if index >= len(visible):
                break
            test_id = visible[index]
            r = recorded.get(test_id)
            mark = VERDICT_MARK.get(r.verdict if r else None, " ")
            text = f" {mark} {test_id[3:] if test_id.startswith('TC_') else test_id}"
            attr = self.color.get(r.verdict if r else "", 0)
            if index == self.session.cursor:
                attr |= curses.A_REVERSE | curses.A_BOLD
            self._safe(stdscr, top + 1 + row, 0, text[:list_width].ljust(list_width), attr)

        # Detail rows
        detail_lines = self._detail_lines(definition, result, detail_width)
        scroll = min(self.session.detail_scroll, max(0, len(detail_lines) - (pane_height - 1)))
        self.session.detail_scroll = scroll  # keep scrolling bounded
        for row in range(pane_height - 1):
            idx = scroll + row
            if idx >= len(detail_lines):
                break
            text, attr = detail_lines[idx]
            self._safe(stdscr, top + 1 + row, detail_x, text, attr)

        if self.show_help:
            self._draw_help(stdscr, top, detail_x, detail_width, pane_height)

        # Footer
        footer_y = height - footer_rows
        self._safe(
            stdscr,
            footer_y,
            0,
            " o OK  n NOK  i INCON  s SKIP  e notes  u undo  / search ".ljust(width),
            curses.A_REVERSE,
        )
        second = (
            " f/F filter  x clear  J/K scroll  PgUp/PgDn  {} jump  y/Y copy  ? help  q quit"
        )
        if self._status and time.time() < self._status_until:
            second = " " + self._status
        self._safe(stdscr, footer_y + 1, 0, second.ljust(width))
        stdscr.refresh()

    def _detail_lines(self, definition, result, width: int):
        import curses

        lines: list[tuple[str, int]] = []
        if definition is None:
            self._section_offsets = []
            self._detail_text = "No cases match the current filter."
            return [("No cases match the current filter.", 0)]
        previous = self.session.compare_result(definition.id)
        lines.append((f"{definition.id}  [{definition.file}]", curses.A_BOLD))
        lines.append(("TestSets: " + ", ".join(f"[{s}]" for s in definition.sets), 0))
        current = f"Current: {result.verdict}" if result else "Current: (not recorded)"
        if previous:
            current += f"    Previous: {previous.verdict}"
        lines.append((current, self.color.get(result.verdict if result else "", 0)))
        if result and result.notes:
            lines.append(("Notes:", curses.A_BOLD))
            for line in result.notes.splitlines():
                lines.extend((wrapped, 0) for wrapped in textwrap.wrap(line, width) or [""])
        if previous and previous.notes:
            lines.append((f"Previous notes ({previous.verdict}):", curses.A_DIM))
            for line in previous.notes.splitlines():
                lines.extend(
                    (wrapped, curses.A_DIM)
                    for wrapped in textwrap.wrap(line, width) or [""]
                )
        lines.append(("", 0))
        body = definition.body
        if body.startswith("### "):
            body = body.split("\n", 1)[1] if "\n" in body else ""
        for raw in body.splitlines():
            if not raw:
                lines.append(("", 0))
                continue
            wrapped = textwrap.wrap(
                raw, width, replace_whitespace=False, drop_whitespace=False
            ) or [raw]
            lines.extend((line, 0) for line in wrapped)
        self._section_offsets = section_offsets(lines)
        self._detail_text = "\n".join(text for text, _attr in lines)
        return lines

    def _draw_help(self, stdscr, top: int, x: int, width: int, height: int) -> None:
        import curses

        help_lines = [
            "Keys",
            "  o / n / i / s   record OK / NOK / INCONCLUSIVE / NOT_TESTED",
            "  e               edit notes in $EDITOR",
            "  u               undo the last change",
            "  j / k           next / previous case",
            "  J / K           scroll the detail one line",
            "  PgUp / PgDn     scroll the detail one page",
            "  { / }           previous / next section in the detail",
            "  y / Y           copy case id / full rendered definition",
            "  g / G           first / last case",
            "  /text           substring filter (Enter apply, Esc cancel)",
            "  f               cycle filter (all/unrecorded/failures/ok/not_tested/set)",
            "  F               filter editor: Set, Tag, Verdict, Unrecorded, Text",
            "  x               clear all filters",
            "  c               switch campaign",
            "  q               quit",
            "  ?               toggle this help (Esc closes it)",
            "  Esc             close this help",
        ]
        for row, line in enumerate(help_lines[:height]):
            self._safe(stdscr, top + row, x, line[:width].ljust(width), curses.A_REVERSE)

    def _read_line(self, stdscr, label: str, initial: str = "") -> Optional[str]:
        """Line editor used by prompts. Enter accepts, Esc cancels."""
        import curses

        editor = LineEditor(initial)
        height, width = stdscr.getmaxyx()
        if height < 4:
            return None
        y = height - 3
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        try:
            while True:
                self._safe(stdscr, y, 0, " " * width)
                text = f"{label}{editor.text}"
                self._safe(stdscr, y, 0, text)
                hint = "[Enter] apply  [Esc] cancel"
                if len(text) + len(hint) + 2 < width:
                    self._safe(stdscr, y, width - len(hint) - 1, hint, curses.A_DIM)
                try:
                    stdscr.move(y, min(len(text), width - 1))
                except curses.error:
                    pass
                stdscr.refresh()
                try:
                    key = stdscr.get_wch()
                except curses.error:
                    continue
                if key == curses.KEY_RESIZE:
                    height, width = stdscr.getmaxyx()
                    y = height - 3
                    continue
                result = editor.feed(key)
                if result is None:
                    continue
                action, value = result
                return value if action == "accept" else None
        finally:
            try:
                curses.curs_set(0)
            except curses.error:
                pass

    def _filter_modal(self, stdscr) -> None:
        import curses

        sets = sorted({d.set_name for d in self.ctx.definitions})
        tags = sorted({tag for d in self.ctx.definitions for tag in d.sets})
        form = FilterForm.from_filter(self.session.filter, sets, tags)
        height, width = stdscr.getmaxyx()
        if height < 16 or width < 60:
            self._set_status("terminal too small for the filter editor")
            return
        box_height = min(12, max(9, height - 4))
        box_width = min(72, max(50, width - 6))
        top = max(1, (height - box_height) // 2)
        left = max(0, (width - box_width) // 2)
        try:
            curses.curs_set(0)
        except curses.error:
            pass

        while True:
            # Rebuild the base view so the modal overlays current state.
            self._draw(stdscr)
            preview = form.preview_count(
                self.ctx.definitions, self.session.campaign.by_id()
            )
            for row in range(box_height):
                self._safe(
                    stdscr,
                    top + row,
                    left,
                    " " * box_width,
                    curses.A_REVERSE,
                )
            self._safe(stdscr, top, left + 2, " Filter ", curses.A_REVERSE | curses.A_BOLD)
            info = f"matches: {preview}/{len(self.ctx.definitions)}"
            self._safe(stdscr, top, left + box_width - len(info) - 2, info, curses.A_REVERSE)
            for field in range(form.field_count):
                row = top + 2 + field
                marker = ">" if form.focus == field else " "
                line = f"{marker} {form.field_label(field):<11} [ {form.field_value(field)} ]"
                attr = curses.A_REVERSE
                if form.focus == field:
                    attr |= curses.A_BOLD
                self._safe(stdscr, row, left + 2, line.ljust(box_width - 4), attr)
            help_line = (
                " Tab field  ←/→ change  Space toggle  Enter apply  Esc cancel  "
                "c clear (not in Text)"
            )
            self._safe(
                stdscr,
                top + box_height - 2,
                left + 2,
                help_line[: box_width - 4],
                curses.A_REVERSE | curses.A_DIM,
            )
            stdscr.refresh()

            try:
                key = stdscr.get_wch()
            except curses.error:
                continue
            if key == curses.KEY_RESIZE:
                height, width = stdscr.getmaxyx()
                box_height = min(12, max(9, height - 4))
                box_width = min(72, max(50, width - 6))
                top = max(1, (height - box_height) // 2)
                left = max(0, (width - box_width) // 2)
                continue
            action = form.handle_key(key)
            if action == "cancel":
                self._set_status("filter unchanged")
                return
            if action == "clear":
                form.clear()
                self._set_status("filter form cleared")
                continue
            if action == "apply":
                self.session.set_filter(form.to_filter())
                self._set_status(f"filter: {self.session.filter.label()}")
                return


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
    raw = (
        getattr(args, "campaign", None)
        or getattr(args, "campaign_opt", None)
        or current_campaign_name(ctx)
    )
    if not raw:
        raise TestReportError(
            "no campaign selected: pass one, use -c/--campaign, set "
            "BOAT_TEST_CAMPAIGN, or run 'use CAMPAIGN'"
        )
    return resolve_campaign_path(raw, ctx.campaigns_dir)


def _remember_explicit_campaign(args: argparse.Namespace, ctx: Context, path: Path) -> None:
    if getattr(args, "campaign", None) or getattr(args, "campaign_opt", None):
        set_current_campaign(ctx, path.stem)


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
    set_current_campaign(ctx, name)
    print(f"created {path}")
    print(f"  {len(results)}/{len(ctx.definitions)} cases seeded; record the rest with 'set'")
    return 0


def cmd_set(args: argparse.Namespace, ctx: Context) -> int:
    definition = resolve_definition(args.id, ctx.definitions)
    path = _campaign_arg(args, ctx)
    _remember_explicit_campaign(args, ctx, path)
    campaign = load_campaign(path)
    apply_result(
        campaign,
        definition.id,
        args.verdict,
        args.notes,
        append=args.append,
    )
    save_campaign(campaign, path, _order_map(ctx.definitions))
    print(f"{definition.id}: {args.verdict}")
    return 0


def cmd_show(args: argparse.Namespace, ctx: Context) -> int:
    definition = resolve_definition(args.id, ctx.definitions)
    campaign = load_campaign(_campaign_arg(args, ctx))
    result = campaign.get(definition.id)

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


def choose_campaign(ctx: Context) -> Path:
    """Interactive campaign picker used when `review` has no current campaign."""
    campaigns = sorted(ctx.campaigns_dir.glob("*.yaml"))
    if campaigns:
        print("Campaigns:")
        for index, path in enumerate(campaigns, 1):
            try:
                campaign = load_campaign(path)
                details = f"{campaign.date or 'no date'}, {len(campaign.results)} results"
            except TestReportError:
                details = "unreadable"
            print(f"  {index}) {path.stem}  ({details})")
    try:
        choice = input("Campaign number/name, or 'new': ").strip()
    except EOFError as exc:
        raise TestReportError("no campaign selected") from exc
    if not choice:
        raise TestReportError("no campaign selected")
    if choice.isdigit() and campaigns and 1 <= int(choice) <= len(campaigns):
        path = campaigns[int(choice) - 1]
        set_current_campaign(ctx, path.stem)
        return path
    if choice.lower() in ("n", "new"):
        try:
            name = input("New campaign name: ").strip()
        except EOFError as exc:
            raise TestReportError("campaign name must not be empty") from exc
        if not name:
            raise TestReportError("campaign name must not be empty")
        path = ctx.campaigns_dir / f"{name}.yaml"
        if path.exists():
            raise TestReportError(f"{path} already exists")
        save_campaign(
            Campaign(name=name, date=_dt.date.today().isoformat(), results=[]),
            path,
            _order_map(ctx.definitions),
        )
        set_current_campaign(ctx, name)
        return path
    path = resolve_campaign_path(choice, ctx.campaigns_dir)
    set_current_campaign(ctx, path.stem)
    return path


def _review_filter(args: argparse.Namespace) -> ReviewFilter:
    set_name = getattr(args, "set_name", "") or ""
    return ReviewFilter(
        kind="set" if set_name else "all",
        text=getattr(args, "text", "") or "",
        tag=getattr(args, "tag", "") or "",
        set_name=set_name,
        verdict=getattr(args, "verdict", None),
        unrecorded=getattr(args, "unrecorded", False),
    )


def cmd_use(args: argparse.Namespace, ctx: Context) -> int:
    path = resolve_campaign_path(args.campaign, ctx.campaigns_dir)
    set_current_campaign(ctx, path.stem)
    print(f"current campaign: {path.stem}  ({path})")
    return 0


def cmd_current(args: argparse.Namespace, ctx: Context) -> int:
    raw = current_campaign_name(ctx)
    if not raw:
        print("no current campaign (use 'use CAMPAIGN' or pass one)")
        return 0
    try:
        path = resolve_campaign_path(raw, ctx.campaigns_dir)
    except TestReportError:
        print(f"{raw}  (not found under {ctx.campaigns_dir})")
        return 0
    print(f"{path.stem}  ({path})")
    return 0


def cmd_list(args: argparse.Namespace, ctx: Context) -> int:
    campaign: Optional[Campaign] = None
    try:
        path = _campaign_arg(args, ctx)
        campaign = load_campaign(path)
    except TestReportError:
        campaign = None
    recorded = campaign.by_id() if campaign else {}

    rows: list[tuple[Definition, Optional[Result]]] = []
    for definition in ctx.definitions:
        result = recorded.get(definition.id)
        if args.unrecorded and result is not None:
            continue
        if args.verdict and (result.verdict if result else None) != args.verdict:
            continue
        if args.set_name and definition.set_name != args.set_name:
            continue
        if args.tag and args.tag not in definition.sets:
            continue
        if getattr(args, "text", "") and args.text.lower() not in (
            definition.id + " " + definition.body
        ).lower():
            continue
        rows.append((definition, result))

    if args.ids_only:
        for definition, _result in rows:
            print(definition.id)
        return 0
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": d.id,
                        "file": d.file,
                        "sets": d.sets,
                        "verdict": r.verdict if r else None,
                        "notes": r.notes if r else "",
                    }
                    for d, r in rows
                ],
                indent=2,
            )
        )
        return 0
    for definition, result in rows:
        verdict = result.verdict if result else "(not recorded)"
        print(f"{definition.id:<52} {verdict}")
    return 0


def cmd_unset(args: argparse.Namespace, ctx: Context) -> int:
    definition = resolve_definition(args.id, ctx.definitions)
    path = _campaign_arg(args, ctx)
    _remember_explicit_campaign(args, ctx, path)
    campaign = load_campaign(path)
    before = len(campaign.results)
    campaign.results = [r for r in campaign.results if r.id != definition.id]
    if len(campaign.results) == before:
        print(f"{definition.id}: not recorded")
        return 0
    save_campaign(campaign, path, _order_map(ctx.definitions))
    print(f"{definition.id}: unset")
    return 0


def cmd_next(args: argparse.Namespace, ctx: Context) -> int:
    path = _campaign_arg(args, ctx)
    set_name = getattr(args, "set_name", "") or ""
    session = ReviewSession(
        ctx,
        path,
        review_filter=ReviewFilter(
            kind="set" if set_name else "all",
            tag=getattr(args, "tag", "") or "",
            set_name=set_name,
            unrecorded=True,
        ),
    )
    definition = session.current()
    if definition is None:
        print("all matching cases are recorded")
        return 0
    result = session.current_result()
    print(f"{definition.id}  [{definition.file}]")
    if definition.sets:
        print("TestSets: " + ", ".join(f"[{s}]" for s in definition.sets))
    print(f"Current: {result.verdict}" if result else "Current: (not recorded)")
    print()
    print(definition.body)
    return 0


def cmd_campaigns(args: argparse.Namespace, ctx: Context) -> int:
    paths = sorted(ctx.campaigns_dir.glob("*.yaml"))
    if args.json:
        entries = []
        for path in paths:
            try:
                campaign = load_campaign(path)
                entries.append(
                    {
                        "name": campaign.name,
                        "date": campaign.date,
                        "path": str(path),
                        "results": len(campaign.results),
                    }
                )
            except TestReportError:
                entries.append({"name": path.stem, "path": str(path), "results": None})
        print(json.dumps(entries, indent=2))
        return 0
    for path in paths:
        print(path.stem)
    return 0


def _tui_available() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        import curses  # noqa: F401
    except ImportError:
        return False
    return True


def cmd_review(args: argparse.Namespace, ctx: Context) -> int:
    raw = (
        getattr(args, "campaign", None)
        or getattr(args, "campaign_opt", None)
        or current_campaign_name(ctx)
    )
    if raw:
        path = resolve_campaign_path(raw, ctx.campaigns_dir)
        set_current_campaign(ctx, path.stem)
    else:
        path = choose_campaign(ctx)

    compare_path = (
        resolve_campaign_path(args.compare, ctx.campaigns_dir) if args.compare else None
    )
    start_id = (
        resolve_definition(args.start, ctx.definitions).id if args.start else None
    )
    session = ReviewSession(
        ctx,
        path,
        compare_path=compare_path,
        review_filter=_review_filter(args),
        start=start_id,
    )
    ask_notes = not args.no_notes_prompt

    if args.plain or not _tui_available():
        return ReviewPlain(session, ask_notes=ask_notes).run()

    try:
        import curses as _curses

        return ReviewTUI(session, ask_notes=ask_notes, ctx=ctx).run()
    except (_curses.error, OSError) as exc:
        sys.stderr.write(f"curses UI unavailable ({exc}); using plain mode\n")
        return ReviewPlain(session, ask_notes=ask_notes).run()


def cmd_completion(args: argparse.Namespace, ctx: Context) -> int:
    tool = shlex.quote(str(Path(__file__).resolve()))
    script = f"""# Source this file from ~/.bashrc (bash) or via bashcompinit (zsh).
_test_report_complete() {{
    local cur cmd
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    cmd="${{COMP_WORDS[1]}}"
    local commands="new set show status validate summary render diff list review use current unset next completion campaigns"
    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=( $(compgen -W "$commands" -- "$cur") )
        return 0
    fi
    case "$cmd" in
        set|show|unset)
            if [ "$COMP_CWORD" -eq 2 ]; then
                COMPREPLY=( $(compgen -W "$(python3 {tool} campaigns 2>/dev/null)" -- "$cur") )
            elif [ "$COMP_CWORD" -eq 3 ]; then
                COMPREPLY=( $(compgen -W "$(python3 {tool} list --ids-only 2>/dev/null)" -- "$cur") )
            fi ;;
        review|status|summary|validate|render|list|next)
            if [ "$COMP_CWORD" -eq 2 ]; then
                COMPREPLY=( $(compgen -W "$(python3 {tool} campaigns 2>/dev/null)" -- "$cur") )
            fi ;;
        use)
            COMPREPLY=( $(compgen -W "$(python3 {tool} campaigns 2>/dev/null)" -- "$cur") ) ;;
    esac
}}
complete -F _test_report_complete test_report.py
complete -F _test_report_complete test-report
"""
    if args.shell == "zsh":
        print("autoload -Uz bashcompinit && bashcompinit")
    print(script)
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
    parser.add_argument(
        "campaign",
        nargs="?",
        help="campaign name or path (default: remembered campaign / BOAT_TEST_CAMPAIGN)",
    )


def _add_common(parser: argparse.ArgumentParser) -> None:
    # SUPPRESS lets the same option be given before or after the subcommand
    # without one parser's default clobbering the other's value.
    parser.add_argument("--root", default=argparse.SUPPRESS, help="repository root (default: parent of tools/)")
    parser.add_argument("--tests-dir", default=argparse.SUPPRESS, help="directory holding the TestSet .md files")
    parser.add_argument("--campaigns-dir", default=argparse.SUPPRESS, help="directory holding campaign .yaml files")
    parser.add_argument(
        "-c",
        "--campaign",
        dest="campaign_opt",
        metavar="CAMPAIGN",
        default=argparse.SUPPRESS,
        help="campaign to use (default: remembered campaign, then BOAT_TEST_CAMPAIGN)",
    )


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

    p_review = sub.add_parser(
        "review",
        help="interactive reviewer (curses TUI; --plain for line-based)",
    )
    _add_common(p_review)
    _add_campaign_arg(p_review)
    p_review.add_argument("--set", dest="set_name", help="only cases in this TestSet")
    p_review.add_argument("--tag", help="only cases carrying this cross-cutting tag")
    p_review.add_argument("--text", default="", help="initial substring filter over id/body")
    p_review.add_argument("--unrecorded", action="store_true", help="start with unrecorded cases only")
    p_review.add_argument("--verdict", choices=VERDICTS, help="start with this verdict only")
    p_review.add_argument("--start", help="start at this id or @N run-order index")
    p_review.add_argument("--compare", help="show another campaign's verdict/notes beside the current one")
    p_review.add_argument("--plain", action="store_true", help="line-based fallback instead of curses")
    p_review.add_argument(
        "--no-notes-prompt",
        action="store_true",
        help="do not ask for notes after each verdict",
    )
    p_review.set_defaults(func=cmd_review)

    p_use = sub.add_parser("use", help="remember a campaign as the default")
    _add_common(p_use)
    p_use.add_argument("campaign", help="campaign name or path")
    p_use.set_defaults(func=cmd_use)

    p_current = sub.add_parser("current", help="print the remembered campaign")
    _add_common(p_current)
    p_current.set_defaults(func=cmd_current)

    p_list = sub.add_parser("list", help="list cases (with the campaign's verdicts when available)")
    _add_common(p_list)
    _add_campaign_arg(p_list)
    p_list.add_argument("--set", dest="set_name", help="only cases in this TestSet")
    p_list.add_argument("--tag", help="only cases carrying this cross-cutting tag")
    p_list.add_argument("--verdict", choices=VERDICTS, help="only cases with this verdict")
    p_list.add_argument("--unrecorded", action="store_true", help="only cases with no result")
    p_list.add_argument("--text", default="", help="substring filter over id/body")
    p_list.add_argument("--json", action="store_true")
    p_list.add_argument("--ids-only", action="store_true", help="one id per line (for scripts/completion)")
    p_list.set_defaults(func=cmd_list)

    p_unset = sub.add_parser("unset", help="clear a case's result (back to unrecorded)")
    _add_common(p_unset)
    _add_campaign_arg(p_unset)
    p_unset.add_argument("id", help="TestCase id or unique abbreviation")
    p_unset.set_defaults(func=cmd_unset)

    p_next = sub.add_parser("next", help="print the next unrecorded case")
    _add_common(p_next)
    _add_campaign_arg(p_next)
    p_next.add_argument("--set", dest="set_name")
    p_next.add_argument("--tag")
    p_next.set_defaults(func=cmd_next)

    p_campaigns = sub.add_parser("campaigns", help="list campaign names")
    _add_common(p_campaigns)
    p_campaigns.add_argument("--json", action="store_true")
    p_campaigns.set_defaults(func=cmd_campaigns)

    p_completion = sub.add_parser("completion", help="emit a bash/zsh completion script")
    _add_common(p_completion)
    p_completion.add_argument("shell", choices=("bash", "zsh"))
    p_completion.set_defaults(func=cmd_completion)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        ctx = _context(args)
        return args.func(args, ctx)
    except TestReportError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    except KeyboardInterrupt:
        # TUI/plain review exit; whatever was recorded is already saved.
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

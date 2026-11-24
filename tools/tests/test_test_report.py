# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``tools/test_report.py`` (manual system-test campaign tooling)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_TOOLS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOLS_DIR))

from test_report import (  # noqa: E402
    load_campaign,
    main,
    parse_definitions,
    validate_campaign,
)

_ROOT = Path(__file__).resolve().parents[2]


SUITE_MD = """\
# Suite

| TestSet | File | Cases | Covers |
|---|---|---:|---|
| [Alpha] | [Alpha.md](Alpha.md) | 2 | things |
| [Beta] | [Beta.md](Beta.md) | 1 | more |

**Total: 3 TestCases.**

## Cross-cutting TestSets (no own file)

These sets exist only as `[tags]`.

| TestSet | Meaning | Index |
|---|---|---|
| [Cross] | cross-cutting | TC_Alpha_002_two |
"""

STRUCTURE_MD = """\
# Structure

```markdown
### TC_<TestSet>_<NNN>_<short_description>

**TestSets:** [TestSet1]
```
"""


def _case(test_id: str, sets: str, extra: str = "") -> str:
    return (
        f"### {test_id}\n\n"
        f"**TestSets:** {sets}\n\n"
        "**Preconditions:**\n- none\n\n"
        "**TestSteps:**\n1. do it\n\n"
        "**Expected:**\n- it works\n"
        f"{extra}"
    )


@pytest.fixture()
def tree(tmp_path: Path) -> dict[str, Path]:
    tests_dir = tmp_path / "test"
    tests_dir.mkdir()
    (tests_dir / "TestSuite.md").write_text(SUITE_MD)
    (tests_dir / "Structure.md").write_text(STRUCTURE_MD)
    (tests_dir / "Alpha.md").write_text(
        "# Alpha\n\n"
        + _case("TC_Alpha_001_one", "[Alpha]")
        + "\n---\n\n"
        + _case("TC_Alpha_002_two", "[Alpha], [Cross]")
    )
    (tests_dir / "Beta.md").write_text("# Beta\n\n" + _case("TC_Beta_001_one", "[Beta]"))
    campaigns = tmp_path / "campaigns"
    campaigns.mkdir()
    return {"tests": tests_dir, "campaigns": campaigns, "root": tmp_path}


def _args(tree: dict[str, Path], *rest: str) -> list[str]:
    return [
        "--tests-dir",
        str(tree["tests"]),
        "--campaigns-dir",
        str(tree["campaigns"]),
        *rest,
    ]


def test_parse_definitions_skips_template_and_follows_suite_order(tree):
    defs = parse_definitions(tree["tests"])
    assert [d.id for d in defs] == [
        "TC_Alpha_001_one",
        "TC_Alpha_002_two",
        "TC_Beta_001_one",
    ]
    assert defs[0].file == "Alpha.md"
    assert defs[1].sets == ["Alpha", "Cross"]
    assert all("<" not in d.id for d in defs)


def test_new_creates_empty_campaign_with_metadata(tree):
    rc = main(
        _args(
            tree,
            "new",
            "run1",
            "--date",
            "2026-11-25",
            "--product-version",
            "0.2.0",
            "--operator",
            "tester",
        )
    )
    assert rc == 0
    path = tree["campaigns"] / "run1.yaml"
    campaign = load_campaign(path)
    assert campaign.name == "run1"
    assert campaign.date == "2026-11-25"
    assert campaign.product_version == "0.2.0"
    assert campaign.results == []


def test_set_records_appends_and_roundtrips_multiline_notes(tree):
    assert main(_args(tree, "new", "run1")) == 0
    assert (
        main(
            _args(
                tree,
                "set",
                "run1",
                "TC_Alpha_001_one",
                "OK",
                "--notes",
                "line one\nline two",
            )
        )
        == 0
    )
    assert (
        main(
            _args(
                tree,
                "set",
                "run1",
                "TC_Alpha_001_one",
                "NOK",
                "--notes",
                "more",
                "--append",
            )
        )
        == 0
    )
    path = tree["campaigns"] / "run1.yaml"
    campaign = load_campaign(path)
    result = campaign.get("TC_Alpha_001_one")
    assert result.verdict == "NOK"
    assert result.notes == "line one\nline two\nmore"
    # Multi-line notes are written as a literal block for readable diffs.
    assert "notes: |" in path.read_text()


def test_validate_strict_requires_every_case(tree, tmp_path):
    path = tree["campaigns"] / "partial.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "campaign": "partial",
                "date": "2026-11-25",
                "results": [
                    {"id": "TC_Alpha_001_one", "verdict": "OK"},
                    {"id": "TC_NoSuch_001_ghost", "verdict": "OK"},
                    {"id": "TC_Alpha_001_one", "verdict": "NOK"},
                ],
            }
        )
    )
    defs = parse_definitions(tree["tests"])
    campaign = load_campaign(path)
    errors, _warnings = validate_campaign(defs, campaign)
    text = "\n".join(errors)
    assert "missing result" in text
    assert "no matching TestCase definition" in text
    assert "duplicate result entry" in text

    assert main(_args(tree, "validate", "partial")) == 1
    assert main(_args(tree, "validate", "partial", "--allow-partial")) == 1


def test_validate_accepts_complete_campaign(tree):
    assert main(_args(tree, "new", "run1")) == 0
    for test_id in ("TC_Alpha_001_one", "TC_Alpha_002_two"):
        assert main(_args(tree, "set", "run1", test_id, "OK")) == 0
    assert main(_args(tree, "set", "run1", "TC_Beta_001_one", "NOT_TESTED")) == 0
    assert main(_args(tree, "validate", "run1")) == 0


def test_invalid_verdict_is_rejected(tree, capsys):
    assert main(_args(tree, "new", "run1")) == 0
    with pytest.raises(SystemExit) as exc:
        main(_args(tree, "set", "run1", "TC_Alpha_001_one", "MAYBE"))
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_unknown_id_is_rejected(tree, capsys):
    assert main(_args(tree, "new", "run1")) == 0
    rc = main(_args(tree, "set", "run1", "TC_Ghost_001_x", "OK"))
    assert rc == 2
    assert "unknown test id" in capsys.readouterr().err


def test_status_and_summary_report_progress(tree, capsys):
    assert main(_args(tree, "new", "run1")) == 0
    assert main(_args(tree, "set", "run1", "TC_Alpha_001_one", "OK")) == 0

    capsys.readouterr()
    assert main(_args(tree, "status", "run1", "--next", "1")) == 0
    out = capsys.readouterr().out
    assert "Progress: 1/3 recorded, 2 missing" in out
    assert "TC_Alpha_002_two" in out

    capsys.readouterr()
    assert main(_args(tree, "summary", "run1")) == 0
    out = capsys.readouterr().out
    assert "OK=1" in out
    assert "Alpha" in out


def test_render_contains_summary_and_notes(tree, capsys):
    assert main(_args(tree, "new", "run1", "--date", "2026-11-25")) == 0
    assert (
        main(
            _args(tree, "set", "run1", "TC_Alpha_001_one", "NOK", "--notes", "it broke")
        )
        == 0
    )
    out_file = tree["root"] / "report.md"
    assert main(_args(tree, "render", "run1", "--out", str(out_file))) == 0
    text = out_file.read_text()
    assert "# System Test Campaign: run1" in text
    assert "## Non-OK cases" in text
    assert "it broke" in text
    assert "## Missing results" in text


def test_diff_classifies_regressions_and_fixes(tree, capsys):
    assert main(_args(tree, "new", "a")) == 0
    assert main(_args(tree, "new", "b")) == 0
    for test_id in ("TC_Alpha_001_one", "TC_Alpha_002_two", "TC_Beta_001_one"):
        assert main(_args(tree, "set", "a", test_id, "OK")) == 0
    assert main(_args(tree, "set", "b", "TC_Alpha_001_one", "NOK")) == 0
    assert main(_args(tree, "set", "b", "TC_Alpha_002_two", "OK")) == 0
    assert main(_args(tree, "set", "b", "TC_Beta_001_one", "OK")) == 0

    capsys.readouterr()
    assert main(_args(tree, "diff", "a", "b")) == 0
    out = capsys.readouterr().out
    assert "regressions (OK->NOK): 1" in out
    assert "TC_Alpha_001_one: OK -> NOK" in out

    assert main(_args(tree, "diff", "a", "b", "--fail-on-regression")) == 1


def test_new_from_seeds_previous_verdicts(tree):
    assert main(_args(tree, "new", "base")) == 0
    assert main(_args(tree, "set", "base", "TC_Alpha_001_one", "OK", "--notes", "seen")) == 0
    assert main(_args(tree, "new", "next", "--from", "base")) == 0
    campaign = load_campaign(tree["campaigns"] / "next.yaml")
    assert set(campaign.by_id()) == {"TC_Alpha_001_one"}
    assert campaign.get("TC_Alpha_001_one").verdict == "OK"
    assert campaign.get("TC_Alpha_001_one").notes == "seen"


def test_real_suite_parses_and_baseline_validates():
    tests_dir = _ROOT / "test"
    defs = parse_definitions(tests_dir)
    assert len(defs) == 201

    baseline = tests_dir / "campaigns" / "2026-11-24-baseline.yaml"
    if not baseline.is_file():
        pytest.skip("baseline campaign not present")
    errors, _warnings = validate_campaign(defs, load_campaign(baseline), tests_dir=tests_dir)
    assert errors == []

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
    Context,
    FilterForm,
    LineEditor,
    ReviewFilter,
    ReviewPlain,
    ReviewSession,
    ReviewTUI,
    load_campaign,
    main,
    osc52_sequence,
    parse_definitions,
    resolve_definition,
    section_offsets,
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


# ── QoL: campaign memory, short ids, review, discovery ───────────────────────


def _session(tree: dict[str, Path], campaign: str) -> ReviewSession:
    defs = parse_definitions(tree["tests"])
    ctx = Context(tree["root"], tree["tests"], tree["campaigns"], defs)
    return ReviewSession(ctx, tree["campaigns"] / f"{campaign}.yaml")


def test_resolve_definition_accepts_short_forms(tree):
    defs = parse_definitions(tree["tests"])
    assert resolve_definition("TC_Alpha_001_one", defs).id == "TC_Alpha_001_one"
    assert resolve_definition("Alpha_001", defs).id == "TC_Alpha_001_one"
    assert resolve_definition("two", defs).id == "TC_Alpha_002_two"
    assert resolve_definition("@3", defs).id == "TC_Beta_001_one"
    with pytest.raises(Exception, match="ambiguous"):
        resolve_definition("001", defs)
    with pytest.raises(Exception, match="unknown"):
        resolve_definition("does-not-exist", defs)


def test_campaign_is_remembered_and_env_overrides(tree, monkeypatch):
    assert main(_args(tree, "new", "run1")) == 0
    assert main(_args(tree, "new", "run2")) == 0

    # run2 is current after `new`; set it explicitly to run1 for the test.
    assert main(_args(tree, "use", "run1")) == 0
    # No campaign positional: resolves through the remembered campaign.
    assert main(_args(tree, "set", "TC_Alpha_001_one", "OK")) == 0
    assert load_campaign(tree["campaigns"] / "run1.yaml").get("TC_Alpha_001_one").verdict == "OK"
    assert load_campaign(tree["campaigns"] / "run2.yaml").results == []

    monkeypatch.setenv("BOAT_TEST_CAMPAIGN", "run2")
    assert main(_args(tree, "set", "TC_Alpha_002_two", "NOK")) == 0
    assert load_campaign(tree["campaigns"] / "run2.yaml").get("TC_Alpha_002_two").verdict == "NOK"

    # Explicit positional wins over the env var.
    assert main(_args(tree, "set", "run1", "TC_Beta_001_one", "NOT_TESTED")) == 0
    assert load_campaign(tree["campaigns"] / "run1.yaml").get("TC_Beta_001_one") is not None
    assert load_campaign(tree["campaigns"] / "run2.yaml").get("TC_Beta_001_one") is None


def test_review_plain_records_and_advances(tree):
    assert main(_args(tree, "new", "run1")) == 0
    session = _session(tree, "run1")
    inputs = iter(["o", "q"])
    view = ReviewPlain(
        session,
        input_fn=lambda _prompt: next(inputs),
        output=lambda _line: None,
        ask_notes=False,
    )
    assert view.run() == 0
    campaign = load_campaign(tree["campaigns"] / "run1.yaml")
    assert campaign.get("TC_Alpha_001_one").verdict == "OK"
    assert campaign.get("TC_Alpha_002_two") is None


def test_review_session_undo_and_filters(tree):
    assert main(_args(tree, "new", "run1")) == 0
    session = _session(tree, "run1")
    assert session.current().id == "TC_Alpha_001_one"
    session.set_verdict("NOK")
    assert load_campaign(tree["campaigns"] / "run1.yaml").get("TC_Alpha_001_one").verdict == "NOK"
    assert session.undo() == "TC_Alpha_001_one"
    assert load_campaign(tree["campaigns"] / "run1.yaml").get("TC_Alpha_001_one") is None

    session.filter = ReviewFilter(kind="set", set_name="Beta")
    session.rebuild()
    assert [tid for tid in session.visible] == ["TC_Beta_001_one"]


def test_review_handle_key_navigation_and_verdict(tree):
    assert main(_args(tree, "new", "run1")) == 0
    session = _session(tree, "run1")
    tui = ReviewTUI(session, ask_notes=False)
    assert tui._handle_key(None, ord("j")) is True
    assert session.current().id == "TC_Alpha_002_two"
    assert tui._handle_key(None, ord("o")) is True
    assert session.campaign.get("TC_Alpha_002_two").verdict == "OK"
    assert tui._handle_key(None, ord("u")) is True
    assert session.campaign.get("TC_Alpha_002_two") is None
    assert tui._handle_key(None, ord("q")) is False


def test_list_filters_and_ids_only(tree, capsys):
    assert main(_args(tree, "new", "run1")) == 0
    assert main(_args(tree, "set", "run1", "TC_Alpha_001_one", "OK")) == 0

    capsys.readouterr()
    assert main(_args(tree, "list", "run1", "--set", "Alpha", "--ids-only")) == 0
    assert capsys.readouterr().out.splitlines() == ["TC_Alpha_001_one", "TC_Alpha_002_two"]

    assert main(_args(tree, "list", "run1", "--set", "Alpha", "--unrecorded", "--ids-only")) == 0
    assert capsys.readouterr().out.splitlines() == ["TC_Alpha_002_two"]

    assert main(_args(tree, "list", "run1", "--verdict", "OK", "--ids-only")) == 0
    assert capsys.readouterr().out.splitlines() == ["TC_Alpha_001_one"]


def test_unset_clears_a_result(tree, capsys):
    assert main(_args(tree, "new", "run1")) == 0
    assert main(_args(tree, "set", "run1", "TC_Alpha_001_one", "OK")) == 0
    assert main(_args(tree, "unset", "run1", "Alpha_001")) == 0
    assert load_campaign(tree["campaigns"] / "run1.yaml").get("TC_Alpha_001_one") is None
    assert main(_args(tree, "unset", "run1", "Alpha_001")) == 0
    assert "not recorded" in capsys.readouterr().out


def test_completion_and_campaigns(tree, capsys):
    assert main(_args(tree, "new", "run1")) == 0
    capsys.readouterr()
    assert main(_args(tree, "campaigns")) == 0
    assert "run1" in capsys.readouterr().out
    assert main(_args(tree, "completion", "bash")) == 0
    script = capsys.readouterr().out
    assert "_test_report_complete" in script
    assert "list --ids-only" in script


# ── TUI UX: line editor, filter form, focus, clear ───────────────────────────


def test_line_editor_accept_cancel_and_edit():
    editor = LineEditor("ab")
    assert editor.feed(ord("c")) is None
    assert editor.text == "abc"
    assert editor.feed(127) is None
    assert editor.text == "ab"
    assert editor.feed(21) is None
    assert editor.text == ""
    for char in "hey":
        assert editor.feed(ord(char)) is None
    assert editor.feed(10) == ("accept", "hey")
    assert LineEditor().feed(27) == ("cancel", None)
    assert LineEditor("x").feed("\x1b") == ("cancel", None)


def test_filter_form_navigation_apply_and_clear():
    form = FilterForm(sets=["CAN", "Gateway"], tags=["AI", "Timeout"])
    assert form.handle_key(9) is None                 # Tab: Set -> Tag
    assert form.handle_key(ord("l")) is None          # Tag: any -> AI
    assert form.handle_key(9) is None                 # Tag -> Verdict
    assert form.handle_key(ord("l")) is None          # Verdict: any -> OK
    assert form.handle_key(9) is None                 # Verdict -> Unrecorded
    assert form.handle_key(ord(" ")) is None          # toggle on
    assert form.handle_key(9) is None                 # Unrecorded -> Text
    for char in "abc":
        assert form.handle_key(ord(char)) is None
    review_filter = form.to_filter()
    assert review_filter.set_name == ""
    assert review_filter.tag == "AI"
    assert review_filter.verdict == "OK"
    assert review_filter.unrecorded is True
    assert review_filter.text == "abc"

    # 'c' clears from a non-text field, but types in the text field.
    assert form.handle_key(9) is None                 # Text -> Set
    assert form.handle_key(ord("c")) == "clear"
    assert form.to_filter() == ReviewFilter()


def test_filter_form_preview_and_cancel(tree):
    defs = parse_definitions(tree["tests"])
    form = FilterForm.from_filter(ReviewFilter(kind="set", set_name="Alpha"), ["Alpha", "Beta"], [])
    assert form.preview_count(defs, {}) == 2
    assert form.handle_key(27) == "cancel"
    # From the current filter round-trip.
    assert FilterForm.from_filter(ReviewFilter(tag="Cross"), ["Alpha"], ["Cross"]).to_filter().tag == "Cross"


def test_handle_key_scroll_and_clear(tree):
    assert main(_args(tree, "new", "run1")) == 0
    session = _session(tree, "run1")
    tui = ReviewTUI(session, ask_notes=False)

    assert session.current().id == "TC_Alpha_001_one"
    assert tui._handle_key(None, ord("j")) is True
    assert session.current().id == "TC_Alpha_002_two"
    assert tui._handle_key(None, ord("k")) is True
    assert session.current().id == "TC_Alpha_001_one"

    import curses

    session.detail_scroll = 0
    tui._handle_key(None, ord("J"))
    assert session.detail_scroll == 1
    tui._handle_key(None, ord("K"))
    assert session.detail_scroll == 0
    tui._handle_key(None, curses.KEY_NPAGE)
    assert session.detail_scroll == 10
    tui._handle_key(None, curses.KEY_PPAGE)
    assert session.detail_scroll == 0

    session.set_filter(ReviewFilter(kind="set", set_name="Alpha", text="alpha_001"))
    assert session.visible == ["TC_Alpha_001_one"]
    assert tui._handle_key(None, ord("x")) is True
    assert session.filter.set_name == ""
    assert session.filter.text == ""
    assert len(session.visible) == 3


def test_handle_key_copy_and_section_jumps(tree):
    assert main(_args(tree, "new", "run1")) == 0
    session = _session(tree, "run1")
    tui = ReviewTUI(session, ask_notes=False)

    copied = []
    tui._copy = lambda stdscr, text, label: copied.append((label, text))
    assert tui._handle_key(None, ord("y")) is True
    assert copied == [("case id", "TC_Alpha_001_one")]

    tui._detail_text = "RENDERED"
    assert tui._handle_key(None, ord("Y")) is True
    assert copied[-1] == ("definition", "RENDERED")

    tui._section_offsets = [0, 5, 10]
    session.detail_scroll = 6
    assert tui._handle_key(None, ord("{")) is True
    assert session.detail_scroll == 5
    assert tui._handle_key(None, ord("}")) is True
    assert session.detail_scroll == 10


def test_osc52_sequence_and_section_offsets():
    lines = [
        ("TC_X_001_one  [X.md]", 0),
        ("TestSets: [X]", 0),
        ("**Preconditions:**", 0),
        ("- a", 0),
        ("**TestSteps:**", 0),
        ("**Expected:**", 0),
        ("Notes:", 0),
        ("text", 0),
    ]
    assert section_offsets(lines) == [0, 2, 4, 5, 6]
    assert osc52_sequence("abc") == "\x1b]52;c;YWJj\x07"


def test_cycle_filter_clears_a_stale_set(tree):
    assert main(_args(tree, "new", "run1")) == 0
    session = _session(tree, "run1")
    session.set_filter(ReviewFilter(kind="set", set_name="Alpha"))
    session.cycle_filter()
    assert session.filter.kind == "all"
    assert session.filter.set_name == ""


def test_filter_composition_set_and_tag(tree):
    defs = parse_definitions(tree["tests"])
    review_filter = ReviewFilter(kind="set", set_name="Alpha", tag="Cross")
    matched = [d.id for d in defs if review_filter.matches(d, None)]
    assert matched == ["TC_Alpha_002_two"]

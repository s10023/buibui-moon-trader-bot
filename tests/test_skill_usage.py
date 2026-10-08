"""Tests for tools/skill_usage.py -- the #885 skill-usage report."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tools.skill_usage import (
    Row,
    build_report,
    load_rows,
    local_skills,
    main,
    render,
)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def _row(days_ago: float, skill: str, source: str = "tool") -> Row:
    return Row(ts=NOW - timedelta(days=days_ago), skill=skill, source=source)


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _skills(tmp_path: Path, *names: str) -> Path:
    root = tmp_path / "skills"
    for n in names:
        (root / n).mkdir(parents=True)
        (root / n / "SKILL.md").write_text("---\n")
    return root


class TestLoad:
    def test_missing_ledger_is_empty_not_an_error(self, tmp_path: Path) -> None:
        assert load_rows(tmp_path / "absent.jsonl") == ([], 0)

    def test_bad_lines_are_counted_and_skipped(self, tmp_path: Path) -> None:
        led = tmp_path / "l.jsonl"
        led.write_text(
            '{"ts": "2026-10-01T00:00:00Z", "skill": "card", "source": "tool"}\n'
            "not json\n"
            '{"ts": "nope", "skill": "card"}\n'
            '{"ts": "2026-10-01T00:00:00Z"}\n'
            "\n"
        )
        rows, malformed = load_rows(led)
        assert [r.skill for r in rows] == ["card"]
        assert rows[0].ts == datetime(2026, 10, 1, tzinfo=UTC)
        assert malformed == 3

    def test_local_skills_need_a_skill_md(self, tmp_path: Path) -> None:
        root = _skills(tmp_path, "card", "recalibrate")
        (root / "hollow").mkdir()
        assert local_skills(root) == ["card", "recalibrate"]


class TestReport:
    def test_counts_split_by_source_inside_the_window(self) -> None:
        rows = [
            _row(1, "card"),
            _row(2, "card", "typed"),
            _row(3, "card", "typed"),
            _row(40, "card"),  # outside a 30-day window
            _row(45, "recalibrate"),  # makes coverage complete
        ]
        rep = build_report(rows, 0, ["card", "recalibrate"], now=NOW, days=30)
        assert (rep.tool["card"], rep.typed["card"], rep.total("card")) == (1, 2, 3)
        assert rep.complete
        assert rep.zero_use == ["recalibrate"]

    def test_partial_coverage_marks_zero_use_provisional(self) -> None:
        """Day one of the ledger must not read as a month of disuse."""
        rep = build_report(
            [_row(2, "card")], 0, ["card", "wfo-sweep"], now=NOW, days=30
        )
        assert not rep.complete
        assert rep.zero_use == ["wfo-sweep"]
        text = render(rep, Path("l.jsonl"))
        assert "PARTIAL: 2.0 of 30 days" in text
        assert "zero-use (PROVISIONAL): 1 of 2" in text

    def test_complete_coverage_drops_the_provisional_label(self) -> None:
        rep = build_report([_row(31, "card")], 0, ["card", "x"], now=NOW, days=30)
        text = render(rep, Path("l.jsonl"))
        assert "coverage  complete" in text
        assert "zero-use: 2 of 2" in text
        assert "PROVISIONAL" not in text

    def test_no_data_says_so_rather_than_naming_every_skill_unused(self) -> None:
        rep = build_report([], 0, ["card"], now=NOW, days=30)
        text = render(rep, Path("l.jsonl"))
        assert "NO DATA" in text
        assert "PROVISIONAL" in text

    def test_plugin_and_removed_skills_are_listed_apart(self) -> None:
        rows = [_row(1, "mattpocock-skills:pr"), _row(1, "gone"), _row(40, "card")]
        rep = build_report(rows, 0, ["card"], now=NOW, days=30)
        assert rep.other == ["gone", "mattpocock-skills:pr"]
        assert rep.zero_use == ["card"]
        assert "not in .claude/skills/" in render(rep, Path("l.jsonl"))

    def test_future_rows_are_ignored(self) -> None:
        """A skewed clock must not inflate this window's counts."""
        rep = build_report(
            [_row(-1, "card"), _row(40, "x")], 0, ["card"], now=NOW, days=30
        )
        assert rep.total("card") == 0

    def test_ranked_most_used_first(self) -> None:
        rows = [_row(1, "b"), _row(1, "b"), _row(1, "a"), _row(40, "a")]
        text = render(
            build_report(rows, 0, ["a", "b", "c"], now=NOW, days=30), Path("l")
        )
        lines = [
            ln.split()[0]
            for ln in text.splitlines()
            if ln.startswith("  ") and ln.split()[0] in "abc"
        ]
        assert lines[:3] == ["b", "a", "c"]


def test_main_reads_the_ledger_and_the_tree(tmp_path: Path) -> None:
    led = tmp_path / "l.jsonl"
    _write(led, [{"ts": "2026-01-01T00:00:00Z", "skill": "card", "source": "typed"}])
    root = _skills(tmp_path, "card", "unused-one")
    assert main(["--ledger", str(led), "--skills-dir", str(root), "--days", "30"]) == 0


def test_bare_invocation_works(tmp_path: Path) -> None:
    led = tmp_path / "l.jsonl"
    stamp = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write(led, [{"ts": stamp, "skill": "card", "source": "tool"}])
    root = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        [
            sys.executable,
            str(root / "tools/skill_usage.py"),
            "--ledger",
            str(led),
            "--skills-dir",
            str(_skills(tmp_path, "card", "idle")),
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
    ).stdout
    assert "zero-use (PROVISIONAL): 1 of 2" in out
    assert "- idle" in out

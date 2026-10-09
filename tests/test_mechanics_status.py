"""Tests for tools/mechanics_status.py — the #977 mechanics-backlog status count."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tools.mechanics_status import (
    closed_kinds,
    count,
    main,
    parse_entries,
    problems,
    render,
)

REPO = Path(__file__).resolve().parent.parent

PREAMBLE = """# Mechanics Backlog (gitignored)

- **In-tree claims** — a prose bullet in the preamble, never an entry.

---

"""


def _backlog(*entries: str, tail: str = "") -> str:
    return PREAMBLE + "\n".join(entries) + tail


OK = [
    "- 2026-07-01 (@a): rule one. TEST: replay it.\n  Status: NEW",
    "- 2026-07-02 (@b): rule two.\n  Status: PRICED — G3 bar +0.04R",
    "- 2026-07-03 (@c): rule three.\n  Status: TESTED — verdict: docs/audits/x.md",
    "- 2026-07-04 (@d): rule four.\n  Status: CLOSED (evidence) — verdict: docs/audits/y.md",
    "- 2026-07-05 (@e): rule five.\n  Status: CLOSED (ruling) — verdict: #922 froze the book",
]


class TestParse:
    def test_preamble_bullets_are_not_entries(self) -> None:
        entries = parse_entries(_backlog(*OK))
        assert len(entries) == 5
        assert all(e.title.startswith("2026-07-0") for e in entries)

    def test_status_kind_and_verdict(self) -> None:
        e = parse_entries(_backlog(OK[3]))[0]
        assert e.statuses == ("CLOSED",)
        assert e.kind == "evidence"
        assert e.verdict == "docs/audits/y.md"

    def test_entry_spans_blank_and_indented_lines(self) -> None:
        text = _backlog(
            "- 2026-07-01: rule.\n  more text\n\n  - nested\n    deeper\n  Status: NEW"
        )
        e = parse_entries(text)[0]
        assert e.statuses == ("NEW",)
        assert e.end - e.start == 5

    def test_prose_paragraph_ends_an_entry(self) -> None:
        text = _backlog(
            "- 2026-07-01: rule.\n\n## Next section\n\nProse.\n  Status: NEW\n"
        )
        e = parse_entries(text)[0]
        assert e.statuses == ()
        assert e.end == e.start

    def test_column_zero_fence_is_skipped(self) -> None:
        text = _backlog(
            OK[0], tail="\n\nIntro.\n\n```text\n- 2026-01-01 not an entry\n```\n"
        )
        assert len(parse_entries(text)) == 1

    def test_no_separator_parses_whole_file(self) -> None:
        assert len(parse_entries("- 2026-07-01: rule.\n  Status: NEW\n")) == 1


class TestProblems:
    def test_clean_backlog_has_none(self) -> None:
        assert problems(parse_entries(_backlog(*OK))) == []

    def test_missing_status(self) -> None:
        (p,) = problems(parse_entries(_backlog("- 2026-07-01: rule.")))
        assert p.startswith("no Status line")

    def test_two_status_lines(self) -> None:
        (p,) = problems(
            parse_entries(_backlog("- 2026-07-01: r.\n  Status: NEW\n  Status: NEW"))
        )
        assert p.startswith("2 Status lines")

    def test_unknown_status(self) -> None:
        (p,) = problems(parse_entries(_backlog("- 2026-07-01: r.\n  Status: OPEN")))
        assert p.startswith("unknown status 'OPEN'")

    def test_closed_needs_a_kind(self) -> None:
        (p,) = problems(
            parse_entries(_backlog("- 2026-07-01: r.\n  Status: CLOSED — verdict: x"))
        )
        assert p.startswith("CLOSED needs a kind")

    def test_closed_kind_must_be_known(self) -> None:
        text = _backlog("- 2026-07-01: r.\n  Status: CLOSED (vibes) — verdict: x")
        (p,) = problems(parse_entries(text))
        assert p.startswith("CLOSED needs a kind")

    def test_only_closed_takes_a_kind(self) -> None:
        (p,) = problems(
            parse_entries(_backlog("- 2026-07-01: r.\n  Status: NEW (evidence)"))
        )
        assert p.startswith("only CLOSED takes a kind")

    def test_terminal_status_needs_a_verdict(self) -> None:
        for line in (
            "  Status: TESTED — docs/audits/x.md",
            "  Status: CLOSED (adopted)",
        ):
            (p,) = problems(parse_entries(_backlog(f"- 2026-07-01: r.\n{line}")))
            assert "without a 'verdict:' pointer" in p

    def test_new_and_priced_need_no_verdict(self) -> None:
        assert problems(parse_entries(_backlog(OK[0], OK[1]))) == []


class TestCount:
    def test_count_by_status_and_kind(self) -> None:
        entries = parse_entries(_backlog(*OK, "- 2026-07-06: no status."))
        c = count(entries)
        assert (c["NEW"], c["PRICED"], c["TESTED"], c["CLOSED"], c["INVALID"]) == (
            1,
            1,
            1,
            2,
            1,
        )
        assert closed_kinds(entries) == {"evidence": 1, "ruling": 1}

    def test_measured_excludes_rulings(self) -> None:
        out = render(parse_entries(_backlog(*OK)), Path("b.md"))
        assert (
            "TESTED+CLOSED 3; measured (TESTED + CLOSED evidence/unreachable) 2" in out
        )

    def test_list_one_status(self) -> None:
        out = render(parse_entries(_backlog(*OK)), Path("b.md"), "CLOSED")
        assert "(ruling): 2026-07-05 (@e): rule five." in out


class TestMain:
    def test_check_exit_codes(self, tmp_path: Path) -> None:
        good = tmp_path / "good.md"
        good.write_text(_backlog(*OK), encoding="utf-8")
        bad = tmp_path / "bad.md"
        bad.write_text(_backlog(*OK, "- 2026-07-06: no status."), encoding="utf-8")
        assert main(["--path", str(good), "--check"]) == 0
        assert main(["--path", str(bad), "--check"]) == 1
        assert main(["--path", str(bad)]) == 0
        assert main(["--path", str(tmp_path / "absent.md")]) == 2


class TestBareInvocation:
    """The tool imports nothing from the repo, so `python3 tools/mechanics_status.py` must work
    without PYTHONPATH. If an `analytics.*` import is ever added, this fails."""

    def test_bare_invocation_works(self, tmp_path: Path) -> None:
        p = tmp_path / "b.md"
        p.write_text(_backlog(*OK), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "tools/mechanics_status.py", "--path", str(p), "--check"],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={"PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, proc.stderr
        assert "mechanics backlog: 5 entries" in proc.stdout

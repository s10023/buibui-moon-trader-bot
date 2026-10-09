"""Count `docs/plans/mechanics-backlog.md` entries by status, and fail on a missing one.

Every Stream B entry carries one `Status:` line as its last line (#977):

- `NEW`: routed, nothing done. An optional note may follow (`Status: NEW — waits on #984`).
- `PRICED`: a power or cost price is on the row and the test is unrun.
- `TESTED`: this claim, or a construction identical to it, was measured.
- `CLOSED (kind)`: resolved without a test of its own. The kind says why, so a
  measured closure is never counted together with a ruling or a reference row:
  `evidence` (a filed verdict covers the claim or its family), `unreachable` (priced
  out on power), `ruling` (an operator direction ruling, e.g. #922's frozen signal
  book), `adopted` (already implemented), `untestable` (no falsifiable form as stated,
  or a reference row) and `duplicate` (restates another entry, whose test it shares).

`TESTED` and `CLOSED` must name their verdict, so a terminal status can always be traced:

    - 2026-07-29 (@author, source): move the stop to entry once ... TEST: ...
      Status: CLOSED (evidence) — verdict: bot-book exit tuning is closed (...)

The count exists for #918 Q11: once ten or more entries read `TESTED` or `CLOSED`, Stream
B's conversion is compared with Stream A's, using the MEASURED subset (`TESTED` plus
`CLOSED` evidence and unreachable). A status nobody can count is how the thesis inbox
reached 11 stale `NEW` rows, so `/ingest-video` and `/ingest-x` run `--check` after every
route that writes here.

An entry is a top-level `- ` bullet after the preamble's first `---` line; it runs until the
next unindented non-blank line. Column-0 code fences in the section prose are skipped.

Deliberately stdlib-only, with NO `sys.path` bootstrap, because it imports nothing from the
repo; `test_bare_invocation_works` pins the bare `python3 tools/mechanics_status.py` form.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

STATUSES = ("NEW", "PRICED", "TESTED", "CLOSED")
NEEDS_VERDICT = frozenset({"TESTED", "CLOSED"})
CLOSED_KINDS = (
    "evidence",
    "unreachable",
    "ruling",
    "adopted",
    "untestable",
    "duplicate",
)
MEASURED_KINDS = frozenset({"evidence", "unreachable"})

DEFAULT_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "plans" / "mechanics-backlog.md"
)

_STATUS_RE = re.compile(r"^\s+Status:\s*([A-Za-z]+)\b(?:\s*\(([a-z]+)\))?(.*)$")
_VERDICT_RE = re.compile(r"verdict:\s*(\S.*)$")
_TITLE_CHARS = 90


@dataclass(frozen=True)
class Entry:
    """One backlog bullet. `start`/`end` are 1-based line numbers of its first and last line."""

    start: int
    end: int
    title: str
    statuses: tuple[str, ...]
    kind: str | None
    verdict: str | None


def _body_start(lines: list[str]) -> int:
    """Index of the first line after the preamble's `---`, or 0 when there is none."""
    for i, line in enumerate(lines):
        if line.strip() == "---":
            return i + 1
    return 0


def parse_entries(text: str) -> list[Entry]:
    """Every entry in the backlog body, in file order."""
    lines = text.splitlines()
    entries: list[Entry] = []
    in_fence = False
    cur: list[tuple[int, str]] = []

    def close() -> None:
        while cur and not cur[-1][1].strip():
            cur.pop()
        if not cur:
            return
        statuses: list[str] = []
        kind: str | None = None
        verdict: str | None = None
        for _, line in cur[1:]:
            m = _STATUS_RE.match(line)
            if m:
                statuses.append(m.group(1))
                kind = m.group(2)
                v = _VERDICT_RE.search(m.group(3))
                verdict = v.group(1).strip() if v else None
        entries.append(
            Entry(
                start=cur[0][0],
                end=cur[-1][0],
                title=cur[0][1][2:].strip()[:_TITLE_CHARS],
                statuses=tuple(statuses),
                kind=kind,
                verdict=verdict,
            )
        )
        cur.clear()

    for i in range(_body_start(lines), len(lines)):
        line = lines[i]
        if in_fence:
            if line.startswith("```"):
                in_fence = False
            continue
        if line.startswith("- "):
            close()
            cur.append((i + 1, line))
        elif not line.strip() or line[0] in " \t":
            if cur:
                cur.append((i + 1, line))
        else:
            close()
            if line.startswith("```"):
                in_fence = True
    close()
    return entries


def status_of(entry: Entry) -> str | None:
    """The entry's status when it has exactly one recognised value, else None."""
    if len(entry.statuses) == 1 and entry.statuses[0] in STATUSES:
        return entry.statuses[0]
    return None


def problems(entries: list[Entry]) -> list[str]:
    """One line per entry whose status is missing, duplicated, unknown or unpointed."""
    out: list[str] = []
    for e in entries:
        where = f"L{e.start}: {e.title}"
        if not e.statuses:
            out.append(f"no Status line - {where}")
            continue
        if len(e.statuses) > 1:
            out.append(f"{len(e.statuses)} Status lines - {where}")
            continue
        status = e.statuses[0]
        if status not in STATUSES:
            out.append(f"unknown status {status!r} - {where}")
        elif status == "CLOSED" and e.kind not in CLOSED_KINDS:
            out.append(
                f"CLOSED needs a kind, one of {', '.join(CLOSED_KINDS)} - {where}"
            )
        elif status != "CLOSED" and e.kind is not None:
            out.append(f"only CLOSED takes a kind, not {status} - {where}")
        elif status in NEEDS_VERDICT and not e.verdict:
            out.append(f"{status} without a 'verdict:' pointer - {where}")
    return out


def count(entries: list[Entry]) -> Counter[str]:
    """Entries per status; anything unparseable counts under `INVALID`."""
    return Counter(status_of(e) or "INVALID" for e in entries)


def closed_kinds(entries: list[Entry]) -> Counter[str]:
    """`CLOSED` entries per kind."""
    return Counter(e.kind or "?" for e in entries if status_of(e) == "CLOSED")


def render(entries: list[Entry], path: Path, listed: str | None = None) -> str:
    """The report: count by status and closure kind, every problem, optionally one status's entries."""
    counts = count(entries)
    kinds = closed_kinds(entries)
    lines = [f"mechanics backlog: {len(entries)} entries ({path})"]
    for s in (*STATUSES, "INVALID"):
        if s == "INVALID" and not counts[s]:
            continue
        lines.append(f"  {s:<8}{counts[s]:>5}")
        if s == "CLOSED":
            lines.extend(f"    {k:<12}{kinds[k]:>5}" for k in CLOSED_KINDS if kinds[k])
    terminal = counts["TESTED"] + counts["CLOSED"]
    measured = counts["TESTED"] + sum(kinds[k] for k in MEASURED_KINDS)
    lines.append(
        f"  TESTED+CLOSED {terminal}; measured (TESTED + CLOSED evidence/unreachable) {measured}"
    )
    found = problems(entries)
    if found:
        lines.append(f"problems: {len(found)}")
        lines.extend(f"  {p}" for p in found)
    if listed:
        lines.append(f"{listed} entries:")
        for e in entries:
            if status_of(e) == listed:
                tag = f" ({e.kind})" if e.kind else ""
                lines.append(f"  L{e.start}{tag}: {e.title}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None
    )
    ap.add_argument("--path", type=Path, default=DEFAULT_PATH, help="backlog file")
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when any entry's status is missing, duplicated, unknown or unpointed",
    )
    ap.add_argument(
        "--list", choices=STATUSES, help="also list the entries of one status"
    )
    args = ap.parse_args(argv)
    path: Path = args.path
    if not path.is_file():
        print(f"mechanics backlog not found: {path} (pass --path)", file=sys.stderr)
        return 2
    entries = parse_entries(path.read_text(encoding="utf-8"))
    # Titles carry CJK author names; a cp1252 pipe must not crash the count.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")
    print(render(entries, path, args.list))
    return 1 if args.check and problems(entries) else 0


if __name__ == "__main__":
    sys.exit(main())

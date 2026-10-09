"""Skill-usage report (#885): invocations per skill over a window, zero-use named.

Reads the ledger `.claude/hooks/log-skill-usage.py` appends to
(`docs/plans/skill-usage.jsonl`) and the skills on disk
(`.claude/skills/<name>/SKILL.md`), and prints each skill's count over the last
`--days` (default 30), split by how it was reached: `tool` (the model invoked it)
and `typed` (the operator typed `/name`).

A zero is a finding ONLY over a window the ledger fully covers. The ledger
starts empty, so on its first day every skill reads zero, and a report that
printed that as "unused" would name the whole tree for deletion. So the report
states its coverage first, and while the oldest row is younger than the window
it labels the zero-use list PROVISIONAL rather than dropping it: a skill nobody
touched in 9 days is worth a glance, never a removal.

Plugin skills (`plugin:skill`) and skills logged but no longer on disk are
listed apart from the local tree, since only the local tree can be zero.

Stdlib only. Its one repo import, the stdlib-only `utils.stdio`, happens inside the
`__main__` block after a scoped path insert, so a bare `python3 tools/skill_usage.py`
still works and importing the module mutates nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = REPO / "docs" / "plans" / "skill-usage.jsonl"
DEFAULT_SKILLS = REPO / ".claude" / "skills"


@dataclass(frozen=True)
class Row:
    ts: datetime
    skill: str
    source: str


@dataclass
class Report:
    days: int
    now: datetime
    oldest: datetime | None
    malformed: int
    tool: Counter[str] = field(default_factory=Counter)
    typed: Counter[str] = field(default_factory=Counter)
    local: list[str] = field(default_factory=list)

    @property
    def window_start(self) -> datetime:
        return self.now - timedelta(days=self.days)

    @property
    def covered_days(self) -> float:
        if self.oldest is None:
            return 0.0
        return max(0.0, (self.now - self.oldest).total_seconds() / 86400)

    @property
    def complete(self) -> bool:
        """True when the ledger reaches back past the window's start."""
        return self.oldest is not None and self.oldest <= self.window_start

    def total(self, skill: str) -> int:
        return self.tool[skill] + self.typed[skill]

    @property
    def zero_use(self) -> list[str]:
        return [s for s in self.local if self.total(s) == 0]

    @property
    def other(self) -> list[str]:
        """Logged in the window but not a local skill (plugins, removed skills)."""
        seen = set(self.tool) | set(self.typed)
        return sorted(seen - set(self.local))


def parse_ts(text: object) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def load_rows(path: Path) -> tuple[list[Row], int]:
    """(rows, malformed count). A bad line is counted, never fatal."""
    if not path.is_file():
        return [], 0
    rows: list[Row] = []
    malformed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        ts = parse_ts(obj.get("ts")) if isinstance(obj, dict) else None
        skill = obj.get("skill") if isinstance(obj, dict) else None
        if ts is None or not isinstance(skill, str) or not skill:
            malformed += 1
            continue
        rows.append(Row(ts=ts, skill=skill, source=str(obj.get("source", ""))))
    return rows, malformed


def local_skills(skills_dir: Path) -> list[str]:
    if not skills_dir.is_dir():
        return []
    return sorted(p.parent.name for p in skills_dir.glob("*/SKILL.md"))


def build_report(
    rows: list[Row], malformed: int, local: list[str], *, now: datetime, days: int
) -> Report:
    report = Report(
        days=days,
        now=now,
        oldest=min((r.ts for r in rows), default=None),
        malformed=malformed,
        local=local,
    )
    for r in rows:
        if r.ts < report.window_start or r.ts > now:
            continue
        (report.typed if r.source == "typed" else report.tool)[r.skill] += 1
    return report


def render(report: Report, ledger: Path) -> str:
    out = [f"Skill usage, last {report.days} days (ledger {ledger})"]
    if report.oldest is None:
        out.append(
            "  NO DATA: the ledger is missing or empty, so no count below is a "
            "finding. It fills once log-skill-usage.py has run in a session."
        )
    elif report.complete:
        out.append(f"  coverage  complete (oldest row {report.oldest:%Y-%m-%d})")
    else:
        out.append(
            f"  coverage  PARTIAL: {report.covered_days:.1f} of {report.days} days "
            f"(oldest row {report.oldest:%Y-%m-%d}). Zero-use below is PROVISIONAL."
        )
    if report.malformed:
        out.append(f"  malformed rows skipped  {report.malformed}")
    out.append("")
    width = max((len(s) for s in report.local + report.other), default=5)
    out.append(f"  {'skill':<{width}}  total  tool  typed")
    ranked = sorted(report.local, key=lambda s: (-report.total(s), s))
    for s in ranked:
        out.append(
            f"  {s:<{width}}  {report.total(s):>5}  {report.tool[s]:>4}  {report.typed[s]:>5}"
        )
    if report.other:
        out.append("")
        out.append("  not in .claude/skills/ (plugins, or removed since):")
        for s in report.other:
            out.append(
                f"  {s:<{width}}  {report.total(s):>5}  {report.tool[s]:>4}  {report.typed[s]:>5}"
            )
    out.append("")
    zero = report.zero_use
    label = "zero-use" if report.complete else "zero-use (PROVISIONAL)"
    if zero:
        out.append(f"  {label}: {len(zero)} of {len(report.local)}")
        out.extend(f"    - {s}" for s in zero)
        out.append(
            "  Each one is a candidate to remove, retrigger or merge; decide per skill."
        )
    else:
        out.append(f"  {label}: none")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--skills-dir", type=Path, default=DEFAULT_SKILLS)
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args(argv)
    if args.days <= 0:
        ap.error("--days must be positive")
    rows, malformed = load_rows(args.ledger)
    report = build_report(
        rows,
        malformed,
        local_skills(args.skills_dir),
        now=datetime.now(UTC),
        days=args.days,
    )
    print(render(report, args.ledger))
    return 0


if __name__ == "__main__":
    # Repo root, for utils.stdio: a bare `python tools/<name>.py` puts only
    # tools/ on the path. Scoped to the entry so an import mutates nothing.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from utils.stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())

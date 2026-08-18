"""Era boundaries — the dated system-rule changes a performance sample must not straddle.

WHY THIS EXISTS
    Blending pre- and post-rule-change performance into one sample is a defect this
    repo commits in at least three known places, none of which announced itself:

      * ``backtest_trades`` spans one regime AND several config eras, yet star
        ratings, the decay review's cells and ``min_avg_r`` are all fitted over the
        whole table at once;
      * the ``backtest_runs`` writer collision meant swept rows and live-gate rows
        sat in one pool until the 2026-08-13 heal re-ran every backtest;
      * the 2026-08-13 orphan prune deleted 206 ``confidence_ratings`` rows, so a
        decay review straddling that date measures THE PRUNE, not decay.

    In every case the sample looked like one population. This module makes the
    boundaries explicit so a consumer can say which ones its window crosses.

THE DESIGN PRINCIPLE, AND IT IS THE WHOLE POINT
    **This module must never report "no boundaries" as a consequence of its own
    failure.** An empty result reads as "your sample straddles nothing", which is a
    false all-clear — the same shape as the powered-null family, where absence of
    evidence was rendered as evidence of absence. So a missing registry, an
    unparseable entry, or a git invocation that fails all RAISE. Silence is only
    ever a real answer here.

TWO SOURCES, AND THE SPLIT IS DELIBERATE
    ``git``      — derived from the history of the paths whose edits change what the
                   book does. Drift-proof by construction: nobody has to remember to
                   file a config change, because the commit IS the record.
    ``declared`` — ``config/eras.toml``, for events git cannot see: a DB prune, a
                   re-run that rewrote stored rows, a data gap that made a window
                   unrepresentative. Small by design; every entry needs a ``why``.

SCOPES
    A boundary is tagged with the sample it invalidates, so a consumer asks only for
    what applies to the rows it holds. A config edit does not invalidate the live
    outcome ledger, and a ratings prune does not invalidate backtest trades.

⚠ CHOOSING THE ERA KEY — THE ONE WAY TO MISUSE THIS MODULE
    Boundaries are dated in WALL-CLOCK time, so the timestamps you pass must also be
    wall-clock. That is not the same column in every table:

      backtest_trades / backtest_runs -> `backtest_runs.run_at_ms` (when the run was
        SAVED). A row's `entry_time` is *simulated market time*: a run executed today
        over 2025-09 bars stamps 2025-09 on rows produced by today's code, and every
        trade in one run shares one code version. Keying on `entry_time` compares bar
        timestamps against code-change dates — a category error that cost this module
        one wrong wiring during its own build, where it confidently reported 64% of
        the decay review's sample as pre-dating the first boundary. It only meant the
        OHLCV is older than the repo.

      live outcome ledger -> the FIRE time. An alert fired under whatever code was
        live at that moment, so there market time and wall-clock time coincide.

    The trap is that both columns are integer ms and neither name says which it is —
    the same shape as the `bar`-units trap and the `n`-vs-days conflation.
"""

from __future__ import annotations

import subprocess
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ERAS_PATH = REPO_ROOT / "config" / "eras.toml"

#: Sample populations a boundary can invalidate.
SCOPES = ("backtest", "ledger", "ratings")

#: Paths whose history marks a change in what the backtest book does. This is the
#: regression trigger set MINUS `tests/fixtures/` and `poetry.lock`, which move for
#: reasons that are not behaviour changes.
BACKTEST_PATHS = (
    "analytics/backtest",
    "analytics/strategies",
    "analytics/signal_config.py",
    "config/signal_watch.toml",
    "config/signal_watch_all.toml",
    "config/signal_watch_weekdays.toml",
    "config/strategy_params.toml",
)

#: Paths whose history changes what reaches the live outcome ledger — i.e. what the
#: OOS evidence base contains.
#:
#: `analytics/strategies` is here as well as in BACKTEST_PATHS, and the overlap is
#: deliberate. This set once excluded it, on the reasoning that a detector change
#: only moves backtest rows. The `bos` causality fix (2026-08-18) is the
#: counterexample: re-stamping a signal from the swing bar to its confirmation bar
#: changed the ledger's `candle_ts_ms`, changed the alert's quoted entry price —
#: the live alert quotes the close of the STAMPED bar, so it had been quoting a
#: price five bars stale — and cut `bos` alert volume by roughly 80% once the
#: gates began evaluating at the decision bar. All three are ledger CONTENT.
#: A detector edit is a ledger event whenever it changes what is emitted or when.
LEDGER_PATHS = (
    "signals",
    "analytics/signal",
    "analytics/strategies",
    "cli/signal.py",
)

#: Conventional-commit types that cannot change what the book does, so a commit
#: carrying one is not a boundary even when it touches a behaviour path (a `docs:`
#: commit recording a WFO verdict edits `analytics/strategies/`; a `test:` commit
#: edits fixtures).
#:
#: The list is SHORT on purpose and the omissions are the deliberate part.
#: `chore:` and `build:` are NOT excluded — a dependency bump or a config tidy has
#: moved behaviour here before, and `refactor:` is kept because this repo runs a
#: regression suite precisely because refactors move goldens. Over-including costs
#: an over-cautious warning; under-including costs a silent blend, which is the
#: defect this module exists to prevent. Bias to over-including.
NON_BEHAVIOUR_TYPES = ("docs", "test", "ci", "style")

_GIT_TIMEOUT_S = 15

#: ASCII record separator. Emitted by git's own ``%x1e`` escape rather than being
#: passed through argv as a literal — a NUL would have been the natural choice and
#: raises ``ValueError: embedded null byte`` at ``exec``, since argv is NUL-terminated.
#: RS is used because a commit subject or a path can contain a tab or a newline.
_RECORD_SEP = "\x1e"


class GitRunner(Protocol):
    """Runs a git command and returns stdout. Injected so tests need no repo."""

    def __call__(self, args: Sequence[str], *, cwd: Path) -> str: ...


@dataclass(frozen=True, order=True)
class EraBoundary:
    """One dated event after which earlier observations are a different population."""

    ts_ms: int
    label: str
    scope: str
    source: str  # "git" | "declared"
    ref: str  # commit sha, or the declared entry's id
    why: str = ""

    @property
    def date(self) -> str:
        return datetime.fromtimestamp(self.ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d")

    def describe(self) -> str:
        return f"{self.date}  [{self.scope}/{self.source}]  {self.label} ({self.ref})"


@dataclass(frozen=True)
class EraSpan:
    """A contiguous stretch between two boundaries, with the sample it holds."""

    start_ms: int | None  # None = open at the start (before the first boundary)
    end_ms: int | None  # None = open at the end
    opened_by: EraBoundary | None
    n_obs: int

    def describe(self) -> str:
        opener = self.opened_by.label if self.opened_by else "(start of sample)"
        return f"n={self.n_obs:>7,}  since {opener}"


def _run_git(args: Sequence[str], *, cwd: Path) -> str:
    """Default git runner. Raises rather than returning empty on failure."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - env
        raise RuntimeError(f"git invocation failed: {exc}") from exc
    if out.returncode != 0:
        raise RuntimeError(f"git exited {out.returncode}: {out.stderr.strip()}")
    return out.stdout


def _is_non_behaviour(subject: str) -> bool:
    """True for a conventional-commit type that cannot change what the book does."""
    head = subject.strip().split(":", 1)[0].strip().lower()
    kind = head.split("(", 1)[0].strip()
    return kind in NON_BEHAVIOUR_TYPES


def git_boundaries(
    *,
    scope: str = "backtest",
    paths: Sequence[str] = BACKTEST_PATHS,
    since_ms: int | None = None,
    repo_root: Path = REPO_ROOT,
    runner: GitRunner = _run_git,
) -> list[EraBoundary]:
    """Derive boundaries from the commit history of behaviour-changing paths.

    Drift-proof: the commit is the record, so nothing has to be remembered. Raises
    if git fails — an empty list would be a false all-clear.
    """
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}; expected one of {SCOPES}")
    args = [
        "log",
        "--format=%x1e%at%x09%h%x09%s",
        "--name-only",
        "--no-merges",
    ]
    if since_ms is not None:
        args.append(f"--since=@{since_ms // 1000}")
    args += ["--", *paths]

    out = runner(args, cwd=repo_root)
    boundaries: list[EraBoundary] = []
    for record in out.split(_RECORD_SEP):
        if not record.strip():
            continue
        head, _, tail = record.strip().partition("\n")
        parts = head.split("\t")
        if len(parts) < 3:
            continue
        ts_s, sha, subject = parts[0], parts[1], "\t".join(parts[2:])
        if _is_non_behaviour(subject):
            continue
        touched = sorted({line.strip() for line in tail.split("\n") if line.strip()})
        boundaries.append(
            EraBoundary(
                ts_ms=int(ts_s) * 1000,
                label=subject.strip(),
                scope=scope,
                source="git",
                ref=sha,
                why=", ".join(touched),
            )
        )
    return sorted(boundaries)


def declared_boundaries(path: Path = DEFAULT_ERAS_PATH) -> list[EraBoundary]:
    """Read the events git cannot see from ``config/eras.toml``.

    Raises on a missing or malformed registry. Returning ``[]`` there would report a
    sample as straddling nothing when in truth nobody looked.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"era registry missing at {path}. It is committed on purpose — an absent "
            "registry would silently report every sample as straddling nothing."
        )
    with path.open("rb") as fh:
        data = tomllib.load(fh)

    entries = data.get("boundary")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path} declares no [[boundary]] entries")

    out: list[EraBoundary] = []
    for i, entry in enumerate(entries):
        missing = [k for k in ("id", "date", "scope", "label", "why") if k not in entry]
        if missing:
            raise ValueError(f"{path} [[boundary]] #{i + 1} is missing {missing}")
        if entry["scope"] not in SCOPES:
            raise ValueError(
                f"{path} boundary {entry['id']!r} has unknown scope "
                f"{entry['scope']!r}; expected one of {SCOPES}"
            )
        try:
            ts = datetime.strptime(str(entry["date"]), "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError as exc:
            raise ValueError(
                f"{path} boundary {entry['id']!r} has an unparseable date "
                f"{entry['date']!r}; expected YYYY-MM-DD (UTC)"
            ) from exc
        out.append(
            EraBoundary(
                ts_ms=int(ts.timestamp() * 1000),
                label=str(entry["label"]),
                scope=str(entry["scope"]),
                source="declared",
                ref=str(entry["id"]),
                why=str(entry["why"]),
            )
        )
    return sorted(out)


def load_boundaries(
    *,
    scopes: Sequence[str] = SCOPES,
    since_ms: int | None = None,
    eras_path: Path = DEFAULT_ERAS_PATH,
    repo_root: Path = REPO_ROOT,
    runner: GitRunner = _run_git,
) -> list[EraBoundary]:
    """Every boundary from both sources, filtered to ``scopes`` and sorted by date."""
    unknown = [s for s in scopes if s not in SCOPES]
    if unknown:
        raise ValueError(f"unknown scope(s) {unknown}; expected from {SCOPES}")

    out: list[EraBoundary] = []
    if "backtest" in scopes:
        out += git_boundaries(
            scope="backtest",
            paths=BACKTEST_PATHS,
            since_ms=since_ms,
            repo_root=repo_root,
            runner=runner,
        )
    if "ledger" in scopes:
        out += git_boundaries(
            scope="ledger",
            paths=LEDGER_PATHS,
            since_ms=since_ms,
            repo_root=repo_root,
            runner=runner,
        )
    out += [b for b in declared_boundaries(eras_path) if b.scope in scopes]
    if since_ms is not None:
        out = [b for b in out if b.ts_ms >= since_ms]
    return sorted(out)


def straddled(
    boundaries: Sequence[EraBoundary], start_ms: int, end_ms: int
) -> list[EraBoundary]:
    """Boundaries falling strictly inside ``(start_ms, end_ms)``.

    Strict on both ends on purpose: a boundary exactly at the window's start opens
    that window rather than splitting it, and one at the end closes it.
    """
    if start_ms > end_ms:
        raise ValueError(f"window start {start_ms} postdates end {end_ms}")
    return sorted(b for b in boundaries if start_ms < b.ts_ms < end_ms)


def split_by_era(
    boundaries: Sequence[EraBoundary], timestamps_ms: Sequence[int]
) -> list[EraSpan]:
    """Partition observations into the eras they belong to, newest boundary wins.

    Spans with no observations are omitted — a consumer wants the eras its sample
    actually covers, not the full boundary list re-printed.
    """
    if not timestamps_ms:
        return []
    ordered = sorted(boundaries)
    counts: dict[int, int] = {}
    for ts in timestamps_ms:
        idx = -1
        for i, b in enumerate(ordered):
            if b.ts_ms <= ts:
                idx = i
            else:
                break
        counts[idx] = counts.get(idx, 0) + 1

    spans: list[EraSpan] = []
    for idx in sorted(counts):
        opener = ordered[idx] if idx >= 0 else None
        start = opener.ts_ms if opener else None
        end = ordered[idx + 1].ts_ms if idx + 1 < len(ordered) else None
        spans.append(
            EraSpan(start_ms=start, end_ms=end, opened_by=opener, n_obs=counts[idx])
        )
    return spans


def straddle_report(
    boundaries: Sequence[EraBoundary],
    timestamps_ms: Sequence[int],
    *,
    max_listed: int = 8,
) -> list[str]:
    """Render the straddle warning. Pure — returns lines, prints nothing.

    Advisory by design. Every sample this repo holds straddles something, so a hard
    failure would fire on day one and be switched off; naming the blend and the
    largest clean sub-sample is what actually changes a decision.
    """
    if not timestamps_ms:
        return ["  era check: no observations to place."]
    lo, hi = min(timestamps_ms), max(timestamps_ms)
    crossed = straddled(boundaries, lo, hi)
    if not crossed:
        return [
            f"  era check: CLEAN — all {len(timestamps_ms):,} obs sit inside one era "
            f"({_fmt(lo)} to {_fmt(hi)})."
        ]

    spans = split_by_era(crossed, timestamps_ms)
    largest = max(spans, key=lambda s: s.n_obs)
    share = largest.n_obs / len(timestamps_ms)
    lines = [
        f"  ⚠ era check: this sample STRADDLES {len(crossed)} boundaries "
        f"({_fmt(lo)} to {_fmt(hi)}, n={len(timestamps_ms):,}).",
        f"    Largest single-era sub-sample: n={largest.n_obs:,} ({share:.0%}) "
        f"— {largest.describe()}",
        "    Pre- and post-change rows are pooled below; read every number as an "
        "average ACROSS rule changes, not a measurement of the current book.",
    ]
    for b in crossed[:max_listed]:
        lines.append(f"      · {b.describe()}")
    if len(crossed) > max_listed:
        lines.append(f"      · ... and {len(crossed) - max_listed} more")
    return lines


def _fmt(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d")

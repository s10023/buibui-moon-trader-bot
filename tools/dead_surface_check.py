"""Report (strategy × timeframe × direction) cells where declaration and output disagree.

Ported from the sister repo's PR #140 (`feat(ci): enforce silent surfaces`,
merged 2026-08-06). The defect class it names is **a surface that nothing
executes and nothing asserts about**, where emptiness is indistinguishable from
coverage. This checks both directions of that mismatch:

**Dead cells** — declared but silent. Walks the declared set, joins it against
what ``backtest_runs`` recorded, and reports any cell whose detector has *never
fired* across the whole history and universe. Not a thin sample: a declaration
the system cannot honour, costing detector work every scan cycle and returning
nothing.

**Orphaned ratings** — rated but undeclared, the exact inverse. ``recalibrate``
rebuilds ``confidence_ratings`` from historical ``backtest_runs`` and has no
notion of what the config currently declares, while the upsert only ever
inserts-or-replaces. A cell dropped from a config therefore keeps its stars and
collects a *fresh timestamp on a stale value* at every refresh.
``prune_stale_ratings`` prunes only on **day_filter** mismatch and has no
declaration check; ``prune_undeclared_ratings`` (2026-08-13) is the one that
removes these, and recalibrate calls it. Note the asymmetry with a dead cell: a
dead cell shows up as a zero and reads as absence, whereas an orphan shows up as
a *number* and reads as evidence — and orphans do **not** go stale, because
recalibrate keeps refreshing their timestamps (measured 2026-08-13: same newest
``updated_at`` as clean rows), so nothing about one looks wrong.

Three deliberate divergences from the sister repo's version, each forced by a
real difference here:

1. **Direction-aware.** All three of our configs carry
   ``strategy_timeframes_long`` / ``strategy_timeframes_short`` narrowing and
   ``confidence_ratings`` is keyed per direction, so a direction-blind check
   under-reports. A rating whose direction is not ``long``/``short`` (e.g. the
   legacy ``combined``) is judged against the base declaration.
2. **Orphans are reported in two tiers.** Our three configs partition the
   calendar by ``day_filter``, so a cell can be undeclared by the config that
   rates it while another config still declares it. Both are orphans *for that
   config* — that daemon will never scan the cell — but the diagnosis differs,
   and only the ``undeclared anywhere`` tier means nothing scans it at all.
3. **Report-only by default; ``--strict`` is what exits non-zero.** The sister
   repo landed this with an empty finding set, so failing by default cost it
   nothing. Here the population is large and *already published*. Failing by
   default would just be red. Land the measurement first, wire ``--strict``
   into CI once the population is clean.

**This is a measurement tool. It does not prune** — ``recalibrate --apply
--config <toml>`` does, via ``prune_undeclared_ratings``.

**⚠ This module's own directive was WRONG for a day and the correction is the
lesson.** It printed *"Do NOT prune by hand — `/db-update` re-runs recalibrate
and moves this set"* on every run. The full chain ran on 2026-08-13 and orphans
went **206 → 206**: recalibrate upserted declared cells and never deleted
undeclared ones, so they were structurally immune to the chain the message
named. A directive a tool prints on *every run* is believed far more readily
than a line in a doc, and grep cannot find this class of defect — only running
the thing it promises and measuring the result.

Usage:
    make buibui-dead-surface-check
    poetry run python tools/dead_surface_check.py [--config PATH ...] [--strict]

Exit codes: 0 = report printed (always, unless ``--strict``); 1 = a bad
invocation, or ``--strict`` with at least one unexpected dead cell or orphan.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(REPO_ROOT))

from analytics.recalibrate_lib import rating_direction_key  # noqa: E402
from analytics.signal_config import (  # noqa: E402
    SignalWatchConfig,
    declared_cells,
    load_signal_config,
)
from analytics.store import DEFAULT_DB_PATH  # noqa: E402

DEFAULT_CONFIGS = (
    "config/signal_watch.toml",
    "config/signal_watch_weekdays.toml",
    "config/signal_watch_all.toml",
)

# The direction keys a rating can be judged against. ``None`` is the base
# (direction-agnostic) declaration and is what a legacy ``combined`` row uses.
DIRECTION_KEYS: tuple[str | None, ...] = ("long", "short", None)

# Cells accepted as dead, kept visible rather than silently tolerated. Keyed
# (day_filter, strategy, timeframe) because a cell can be dead under one day
# filter and healthy under another. **This list should only ever shrink** — a
# new entry means a new dead surface was accepted, which is the thing this
# module exists to prevent.
#
# EMPTY on landing (2026-08-12) because the default run does not fail, so
# nothing here needs allowlisting to keep a gate green. Populate it only when
# --strict is wired into CI, and record a reason beside each entry.
#
# The caution the sister repo recorded, which transfers: a zero-signal cell
# looks identical whatever zeroed it (a broken detector, a conjunction of gates
# discarding ~100% of its output, genuine bar scarcity), so any reason written
# beside an entry is a hypothesis until it is traced. This check reliably finds
# dead cells; it cannot diagnose them.
_KNOWN_DEAD_CELLS: frozenset[tuple[str, str, str]] = frozenset()


@dataclass(frozen=True)
class DeadCell:
    """A declared cell whose detector never fired."""

    config: str
    day_filter: str
    strategy: str
    timeframe: str
    reason: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.day_filter, self.strategy, self.timeframe)

    def __str__(self) -> str:
        return f"{self.strategy} × {self.timeframe} [{self.day_filter}] — {self.reason}"


@dataclass(frozen=True)
class OrphanRating:
    """A ``confidence_ratings`` row for a cell its own config no longer declares."""

    config: str
    strategy: str
    timeframe: str
    direction: str
    stars: int
    avg_r: float | None
    declared_elsewhere: bool

    @property
    def tier(self) -> str:
        return (
            "declared by another config"
            if self.declared_elsewhere
            else "undeclared anywhere"
        )

    def __str__(self) -> str:
        avg_r = "—" if self.avg_r is None else f"{self.avg_r:+.4f}"
        return (
            f"{self.strategy} × {self.timeframe} [{self.direction}] — "
            f"{self.stars}★ avg_r={avg_r}, {self.tier}"
        )


def declared_by_direction(
    cfg: SignalWatchConfig,
) -> dict[str | None, set[tuple[str, str]]]:
    """The declared cell set for each direction key, for O(1) membership tests."""
    return {key: set(declared_cells(cfg, key)) for key in DIRECTION_KEYS}


def direction_key(direction: str) -> str | None:
    """Map a stored ``confidence_ratings.direction`` onto a declaration key.

    Delegates to :func:`analytics.recalibrate_lib.rating_direction_key` rather
    than restating the rule. The pruner there and this checker must agree
    exactly: a fork would let a cell read as declared to one and undeclared to
    the other, i.e. a silent deletion of a live rating. Kept as a named
    re-export because this module's tests and docstrings refer to it.
    """
    return rating_direction_key(direction)


def find_orphan_ratings(
    conn: duckdb.DuckDBPyConnection,
    cfg: SignalWatchConfig,
    config_name: str,
    elsewhere: dict[str | None, set[tuple[str, str]]] | None = None,
) -> list[OrphanRating]:
    """Rated cells this config does not declare, worst-first by displayed stars.

    ``config_name`` is the TOML stem (``signal_watch``), which is what
    ``confidence_ratings.config_name`` stores — not the path the CLI takes.
    ``elsewhere`` is the union of the *other* checked configs' declarations, used
    only to tier the result; omit it and every orphan reports as undeclared
    anywhere.
    """
    declared = declared_by_direction(cfg)
    rows = conn.execute(
        "SELECT strategy, tf, direction, stars, avg_r FROM confidence_ratings "
        "WHERE config_name = ? ORDER BY stars DESC, avg_r DESC",
        [config_name],
    ).fetchall()
    orphans: list[OrphanRating] = []
    for strategy, tf, direction, stars, avg_r in rows:
        cell = (str(strategy), str(tf))
        key = direction_key(str(direction))
        if cell in declared[key]:
            continue
        other = set() if elsewhere is None else elsewhere.get(key, set())
        orphans.append(
            OrphanRating(
                config=config_name,
                strategy=cell[0],
                timeframe=cell[1],
                direction=str(direction),
                stars=int(stars),
                avg_r=None if avg_r is None else float(avg_r),
                declared_elsewhere=cell in other,
            )
        )
    return orphans


def find_dead_cells(
    conn: duckdb.DuckDBPyConnection,
    cfg: SignalWatchConfig,
    config_name: str,
) -> list[DeadCell]:
    """Declared cells with no backtest runs, or runs that produced zero signals."""
    dead: list[DeadCell] = []
    for strategy, tf in declared_cells(cfg):
        row = conn.execute(
            "SELECT count(*), coalesce(sum(total_signals), 0) FROM backtest_runs "
            "WHERE strategy = ? AND timeframe = ? AND day_filter = ?",
            [strategy, tf, cfg.day_filter],
        ).fetchone()
        n_runs, n_signals = (0, 0) if row is None else (int(row[0]), int(row[1]))
        if n_runs == 0:
            reason = "never backtested (no runs)"
        elif n_signals == 0:
            reason = f"detector never fired ({n_runs} runs, 0 signals)"
        else:
            continue
        dead.append(DeadCell(config_name, cfg.day_filter, strategy, tf, reason))
    return dead


def unexpected(cells: list[DeadCell]) -> list[DeadCell]:
    """Dead cells that are not on the known-dead allowlist."""
    return [c for c in cells if c.key not in _KNOWN_DEAD_CELLS]


def union_declared(
    configs: dict[str, SignalWatchConfig],
) -> dict[str | None, set[tuple[str, str]]]:
    """Every cell declared by any of ``configs``, per direction key."""
    merged: dict[str | None, set[tuple[str, str]]] = {k: set() for k in DIRECTION_KEYS}
    for cfg in configs.values():
        for key, cells in declared_by_direction(cfg).items():
            merged[key] |= cells
    return merged


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--config",
        dest="configs",
        action="append",
        default=None,
        help=f"config TOML to check (repeatable; default: {', '.join(DEFAULT_CONFIGS)})",
    )
    p.add_argument("--db", default=str(DEFAULT_DB_PATH), help="analytics DB path")
    p.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 on any unexpected dead cell or orphaned rating (default: report only)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    config_paths = args.configs or list(DEFAULT_CONFIGS)

    db_path = Path(args.db)
    if not db_path.exists():
        print(
            f"ERROR: analytics DB not found at {db_path}. Run `make db-update` first."
        )
        return 1

    configs = {path: load_signal_config(path) for path in config_paths}

    all_dead: list[DeadCell] = []
    all_orphans: list[OrphanRating] = []
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
    except duckdb.IOException as exc:
        print(f"ERROR: cannot open {db_path} read-only: {exc}")
        print(
            "\nThe 15-minute signal-watch timer holds this lock while it runs. Retry "
            "off the :01/16/31/46 boundary, or point --db at a copy."
        )
        return 1
    with conn:
        for path, cfg in configs.items():
            others = {p: c for p, c in configs.items() if p != path}
            found = find_dead_cells(conn, cfg, path)
            all_dead.extend(found)
            orphans = find_orphan_ratings(
                conn, cfg, Path(path).stem, union_declared(others)
            )
            all_orphans.extend(orphans)
            n_declared = len(declared_cells(cfg))
            print(
                f"\n{path}  (day_filter={cfg.day_filter}, {n_declared} cells declared)"
            )
            if found:
                for cell in found:
                    mark = "  " if cell.key in _KNOWN_DEAD_CELLS else "❌"
                    known = " [known]" if cell.key in _KNOWN_DEAD_CELLS else ""
                    print(f"  {mark} {cell}{known}")
            else:
                print("  ✅ every declared cell produces signals")
            if orphans:
                nowhere = sum(1 for o in orphans if not o.declared_elsewhere)
                print(
                    f"  ❌ {len(orphans)} orphaned rating(s): {nowhere} undeclared "
                    f"anywhere, {len(orphans) - nowhere} declared by another config"
                )
                for orphan in orphans:
                    print(f"     {orphan}")
            else:
                print("  ✅ every rated cell is declared")

    failing = unexpected(all_dead)
    print()
    if failing:
        print(f"{len(failing)} dead cell(s) not on the known-dead allowlist:")
        for cell in failing:
            print(f"  - {cell.config}: {cell}")
        print(
            "\nA declared cell that never fires costs detector work every scan cycle "
            "and returns nothing.\nEither drop it from the config, or add it to "
            "_KNOWN_DEAD_CELLS with a reason."
        )
    if all_orphans:
        nowhere = sum(1 for o in all_orphans if not o.declared_elsewhere)
        print(
            f"{len(all_orphans)} orphaned confidence rating(s): {nowhere} undeclared "
            f"anywhere, {len(all_orphans) - nowhere} declared by another config."
        )
        print(
            "\nOrphans are INERT at runtime — both read sites are keyed lookups, so a\n"
            "cell nothing scans is never queried. The damage is to AGGREGATES: any\n"
            "population counted off `confidence_ratings` (the decay review's ★>=4 list\n"
            "among them) includes cells no daemon will ever scan.\n"
            "`recalibrate --apply --config <toml>` now prunes these; a run that reports\n"
            "no prune while this list is non-empty means the share guard refused —\n"
            "read its message, it is a resolver tripwire, not a policy knob."
        )
    if not failing and not all_orphans:
        n_known = len(all_dead)
        print(
            "OK: no unexpected dead cells and no orphaned ratings"
            + (f" ({n_known} known-dead, allowlisted)" if n_known else "")
        )
        return 0
    if args.strict:
        return 1
    print("\n(report only — pass --strict to exit non-zero on these)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    from utils.stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())

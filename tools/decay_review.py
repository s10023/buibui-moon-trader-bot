"""Weekly decay review — DSR-suspect list + gate reachability.

Read-only against ``analytics.db``. Rebuilds the exact pools
:func:`analytics.recalibrate_lib.compute_dsr_ratings` uses — via the shared
:func:`analytics.recalibrate_lib.select_rated_run_ids`, never a local copy of the
ranking — then answers two questions the stored ``confidence_ratings.dsr`` column
cannot:

1. Which rated (★>=4) cells are overfit-suspect (DSR < 0.95)?
2. **Is DSR >= 0.95 reachable at all under this trial family?** A gate no cell can
   clear reports "everything is suspect" as an artifact, not a finding — the H8
   structurally-unreachable defect class. Inverts the gate to the Sharpe a cell
   would need and compares it to the Sharpe actually observed.

**Headroom is measured at each cell's OWN n.** The required Sharpe falls as n
rises, so comparing the best Sharpe against a bar computed at the *median* n mixes
two different bars and can report a gate as reachable while every cell fails it.
The printed median-n bar is a scale stamp only — never a verdict.

One reading trap this tool cannot fix:

* Absence from the suspect list is **not** a clean bill. Only a cell in neither
  the suspect nor the unscoreable list has passed anything.

**The dispersion trap is CLOSED (ST66).** ``MIN_DSR_TRADES`` used to gate count
alone, so a cell whose trades all resolved at the same R cleared the floor and
joined the trial family carrying a Sharpe in the hundreds. ``MIN_DSR_SD`` now
gates dispersion beside it, and this tool inherits the fix because it imports
``_sharpe`` rather than re-deriving it. Measured on the ``off`` scope at the fix:
one cell (``bos/1d`` long, n=36, sd 0.00218, Sharpe −461.3) was taking the
long-scope family variance from **0.0181 to 3937.38** and zeroing all 18 scoreable
long DSRs — so a cell reading 0.0000 here was reporting the artifact, not the cell.

**Scope resolution is a third trap, fixed 2026-08-13.** ``day_filter`` and
``adr_suppress_threshold`` must travel together: a bare ``--day-filter`` leaves
the threshold at ``None``, which :func:`select_rated_run_ids` renders as
``adr_suppress_threshold IS NULL`` — a pool no live config writes (they use
0.75 / 0.65 / 0.70), frozen since 2026-04-09. That was the *default*, so every
review before the fix audited it; the verdict direction survived (0 cells clear
either way), which is why it went unnoticed. A bare run now resolves both halves
from every live config, and ``--day-filter`` alone exits non-zero.

Usage::

    PYTHONPATH=. poetry run python tools/decay_review.py
    PYTHONPATH=. poetry run python tools/decay_review.py --config config/signal_watch.toml
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import duckdb

from analytics.eras import load_boundaries, straddle_report
from analytics.recalibrate_lib import (
    MIN_DSR_TRADES,
    _sharpe,
    sample_run_times,
    select_rated_run_ids,
)
from analytics.research_guards import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
)
from analytics.signal_config import load_signal_config
from analytics.store import DEFAULT_DB_PATH, load_backtest_trades
from tools.dead_surface_check import (
    DEFAULT_CONFIGS,
    declared_by_direction,
    direction_key,
)

GATE = 0.95
SCOPES = ("combined", "long", "short")


@dataclass(frozen=True)
class ConfigScope:
    """One live config's `(day_filter, adr_suppress_threshold)` selection pair.

    Both halves are load-bearing and must travel together. Passing a
    `day_filter` alone leaves `adr_suppress_threshold` at `None`, which
    :func:`select_rated_run_ids` renders as `adr_suppress_threshold IS NULL` —
    selecting runs saved with **no** ADR gate. No live config saves that way
    (they use 0.75 / 0.65 / 0.70), so that pool is pre-May and frozen.
    """

    name: str
    day_filter: str | None
    adr_suppress_threshold: float | None


def resolve_config_scopes(configs: Sequence[str]) -> list[ConfigScope]:
    """Resolve each config to the selection pair `recalibrate` reads it with.

    Mirrors :mod:`analytics.recalibrate_runner`, which sets both fields from a
    single `--config`. Deriving them here from the same source is what keeps the
    review auditing the population the live gate actually rates.
    """
    return [
        ConfigScope(
            name=Path(path).stem,
            day_filter=cfg.day_filter,
            adr_suppress_threshold=cfg.bias.adr_suppress_threshold,
        )
        for path, cfg in ((p, load_signal_config(p)) for p in configs)
    ]


Pools = dict[tuple[str, str], list[float]]


@dataclass(frozen=True)
class CellHeadroom:
    """One (strategy, timeframe) cell scored against the gate at its own ``n``."""

    cell: tuple[str, str]
    sharpe: float
    n_obs: int
    required: float
    dsr: float

    @property
    def headroom(self) -> float:
        """Sharpe minus the bar **at this cell's own n**. Negative == short."""
        return self.sharpe - self.required

    @property
    def clears_gate(self) -> bool:
        return self.dsr >= GATE


@dataclass(frozen=True)
class ScopeReport:
    """Gate reachability for one direction scope (combined / long / short)."""

    scope: str
    family: tuple[float, ...]
    sr0: float
    n_median: int
    bar_at_median_n: float
    cells: tuple[CellHeadroom, ...]

    @property
    def passing(self) -> tuple[CellHeadroom, ...]:
        return tuple(c for c in self.cells if c.clears_gate)

    @property
    def closest(self) -> CellHeadroom:
        """The cell with the most headroom against its OWN bar."""
        return max(self.cells, key=lambda c: c.headroom)

    @property
    def reachable(self) -> bool:
        """True when at least one cell clears the bar computed at its own n."""
        return self.closest.headroom >= 0.0


def scopes_to_review(
    config: str | None,
    day_filter: str | None,
    adr: float | None,
    configs: Sequence[str] = DEFAULT_CONFIGS,
) -> list[ConfigScope]:
    """Decide which selection pairs this invocation audits.

    A bare invocation audits **every live config**, because that is the
    population the gate rates and the only invocation `SKILL.md` documents. The
    previous default left both halves at `None` and silently audited runs saved
    with no ADR gate — a pre-May pool, frozen since 2026-04-09.
    """
    if config is not None:
        return resolve_config_scopes([config])
    if day_filter is None and adr is None:
        return resolve_config_scopes(configs)
    if adr is None:
        raise SystemExit(
            "--day-filter without --adr-suppress-threshold selects runs saved with "
            "NO ADR gate (adr_suppress_threshold IS NULL). No live config saves "
            "that way — signal_watch 0.75, weekdays 0.65, all 0.70 — so that pool "
            "is pre-May and frozen. Pass --config, or supply both flags."
        )
    return [
        ConfigScope(name="ad-hoc", day_filter=day_filter, adr_suppress_threshold=adr)
    ]


def pools_by_scope(
    conn: duckdb.DuckDBPyConnection,
    day_filter: str | None = None,
    adr_suppress_threshold: float | None = None,
) -> dict[str, Pools]:
    """Rebuild ``compute_dsr_ratings``' pools: ``{scope: {(strategy, tf): [pnl_r]}}``."""
    out: dict[str, Pools] = {s: defaultdict(list) for s in SCOPES}
    run_ids = select_rated_run_ids(conn, day_filter, adr_suppress_threshold)
    if not run_ids:
        return out

    rows = load_backtest_trades(
        conn,
        columns=("strategy", "timeframe", "direction", "pnl_r"),
        run_ids=run_ids,
        closed_only=True,
        dedup_on=None,
    )
    for strategy, tf, direction, pnl_r in rows.itertuples(index=False, name=None):
        cell = (str(strategy), str(tf))
        out["combined"][cell].append(float(pnl_r))
        if direction in ("long", "short"):
            out[str(direction)][cell].append(float(pnl_r))
    return out


def required_sharpe(sr0: float, n_obs: int) -> float:
    """Bisect the per-trade Sharpe that would put DSR exactly at the gate."""
    lo, hi = sr0, sr0 + 5.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if probabilistic_sharpe_ratio(mid, n_obs, sr_benchmark=sr0) < GATE:
            lo = mid
        else:
            hi = mid
    return hi


def analyse_scope(
    scope: str, pools: Mapping[tuple[str, str], list[float]]
) -> ScopeReport | None:
    """Score every eligible cell in one scope. ``None`` when the family is too small.

    A family of one has no dispersion to deflate against, so ``expected_max_sharpe``
    is meaningless there — reporting nothing beats reporting a bar that is an
    artifact of a single trial.
    """
    sharpes = {
        cell: _sharpe(returns)
        for cell, returns in pools.items()
        if len(returns) >= MIN_DSR_TRADES
    }
    scored = [(cell, sr) for cell, sr in sharpes.items() if sr is not None]
    if len(scored) < 2:
        return None

    family = [sr for _, sr in scored]
    sr0 = expected_max_sharpe(len(family), statistics.variance(family))
    ns = sorted(len(pools[cell]) for cell, _ in scored)
    n_median = ns[len(ns) // 2]

    cells = tuple(
        CellHeadroom(
            cell=cell,
            sharpe=sr,
            n_obs=len(pools[cell]),
            required=required_sharpe(sr0, len(pools[cell])),
            dsr=deflated_sharpe_ratio(sr, len(pools[cell]), trial_srs=family),
        )
        for cell, sr in scored
    )
    return ScopeReport(
        scope=scope,
        family=tuple(family),
        sr0=sr0,
        n_median=n_median,
        bar_at_median_n=required_sharpe(sr0, n_median),
        cells=cells,
    )


def render_scope(report: ScopeReport) -> list[str]:
    """Format one scope report. Pure — returns lines, prints nothing."""
    best = max(report.cells, key=lambda c: c.sharpe)
    lines = [
        f"\n--- scope: {report.scope}",
        f"  family        : {len(report.family)} cells, n median {report.n_median}",
        f"  family Sharpe : mean {statistics.fmean(report.family):+.4f} "
        f"sd {statistics.stdev(report.family):.4f} "
        f"max {best.sharpe:+.4f} ({best.cell})",
        f"  benchmark sr0 : {report.sr0:+.4f} "
        f"(expected max of {len(report.family)} trials)",
        f"  bar at n median {report.n_median}: "
        f"Sharpe >= {report.bar_at_median_n:+.4f}  [scale stamp, NOT a verdict]",
        f"  cells clearing the gate: {len(report.passing)} / {len(report.cells)}",
    ]
    lines.extend(
        f"      PASS {c.cell} sharpe {c.sharpe:+.4f} n={c.n_obs} DSR {c.dsr:.4f}"
        for c in report.passing
    )
    close = report.closest
    lines += [
        f"  CLOSEST cell  : {close.cell} sharpe {close.sharpe:+.4f} "
        f"n={close.n_obs} DSR {close.dsr:.4f}",
        f"    its own bar : {close.required:+.4f} => {abs(close.headroom):.4f} "
        f"{'ABOVE' if close.headroom >= 0 else 'BELOW'}",
        f"  REACHABLE: {'yes' if report.reachable else 'NO'} "
        f"(measured at each cell's own n)",
    ]
    return lines


def stored_rating_lines(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """Summarise what the live gate actually reads from ``confidence_ratings``.

    Reports the ★>=4 population **twice**: as stored, and restricted to cells
    some config still declares. ``recalibrate`` rebuilds ratings from historical
    ``backtest_runs`` with no notion of what the configs currently declare, so a
    cell dropped from a config keeps its stars indefinitely. Those rows are inert
    at runtime — both read sites are keyed lookups — but they inflate any
    population counted here, and this population is published.
    ``tools/dead_surface_check.py`` enumerates them.
    """
    configs = {Path(path).stem: load_signal_config(path) for path in DEFAULT_CONFIGS}
    declared = {stem: declared_by_direction(cfg) for stem, cfg in configs.items()}

    def is_declared(config_name: str, strategy: str, tf: str, direction: str) -> bool:
        per_direction = declared.get(config_name)
        if per_direction is None:  # a config this tool does not know about
            return True
        return (strategy, tf) in per_direction[direction_key(direction)]

    rows = conn.execute(
        "SELECT config_name, stars, strategy, tf, direction, dsr "
        "FROM confidence_ratings WHERE stars >= 4 ORDER BY config_name, stars DESC"
    ).fetchall()

    groups: dict[tuple[str, int], list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0])
    for config_name, stars, strategy, tf, direction, dsr in rows:
        live = is_declared(str(config_name), str(strategy), str(tf), str(direction))
        counts = groups[(str(config_name), int(stars))]
        counts[0] += 1
        counts[1] += 1 if dsr is None else 0
        counts[2] += 1 if dsr is not None and dsr < GATE else 0
        counts[3] += 1 if dsr is not None and dsr >= GATE else 0
        counts[4] += 1 if live else 0

    lines = [
        f"{'config':24} {'*':>2} {'cells':>6} {'declared':>9} {'unscoreable':>12} "
        f"{'suspect':>8} {'clean':>6}"
    ]
    for (config_name, stars), c in sorted(
        groups.items(), key=lambda kv: (kv[0][0], -kv[0][1])
    ):
        lines.append(
            f"{config_name:24} {stars:>2} {c[0]:>6} {c[4]:>9} {c[1]:>12} "
            f"{c[2]:>8} {c[3]:>6}"
        )

    total = len(rows)
    unscoreable = sum(1 for r in rows if r[5] is None)
    clean = sum(1 for r in rows if r[5] is not None and r[5] >= GATE)
    live_rows = [
        r for r in rows if is_declared(str(r[0]), str(r[2]), str(r[3]), str(r[4]))
    ]
    live_clean = sum(1 for r in live_rows if r[5] is not None and r[5] >= GATE)
    lines.append(
        f"\nTOTAL *>=4: {total} cells | {unscoreable} unscoreable "
        f"(<{MIN_DSR_TRADES} trades) | {clean} clearing DSR {GATE}"
    )
    lines.append(
        f"  of which DECLARED by some config: {len(live_rows)} cells | "
        f"{live_clean} clearing DSR {GATE}  "
        f"({total - len(live_rows)} orphaned, see tools/dead_surface_check.py)"
    )
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    parser.add_argument(
        "--config",
        default=None,
        help="Audit one config, resolving day_filter AND adr threshold from it",
    )
    parser.add_argument(
        "--day-filter",
        default=None,
        help="Ad-hoc day_filter scope; requires --adr-suppress-threshold",
    )
    parser.add_argument(
        "--adr-suppress-threshold",
        type=float,
        default=None,
        help="Ad-hoc ADR-suppression scope",
    )
    args = parser.parse_args()

    config_scopes = scopes_to_review(
        args.config, args.day_filter, args.adr_suppress_threshold
    )

    # Resolved before the DB is opened so a git failure fails fast and loudly —
    # a review that silently skipped the era check would read as one that passed it.
    # Kept apart on purpose: leg 1 scores backtest RUNS, so a ratings-population
    # change does not segment it, and pooling the two scopes made a prune look like
    # the opener of the largest backtest sub-sample.
    boundaries = load_boundaries(scopes=("backtest",))
    ratings_boundaries = load_boundaries(scopes=("ratings",))

    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        print("=" * 78)
        print("LEG 1 - DSR-suspect list + gate reachability")
        print("=" * 78)
        for cfg_scope in config_scopes:
            print(
                f"\n### {cfg_scope.name} "
                f"(day_filter={cfg_scope.day_filter}, "
                f"adr={cfg_scope.adr_suppress_threshold})"
            )
            print(
                "\n".join(
                    straddle_report(
                        boundaries,
                        sample_run_times(
                            conn,
                            cfg_scope.day_filter,
                            cfg_scope.adr_suppress_threshold,
                        ),
                    )
                )
            )
            scopes = pools_by_scope(
                conn, cfg_scope.day_filter, cfg_scope.adr_suppress_threshold
            )
            for scope in SCOPES:
                report = analyse_scope(scope, scopes[scope])
                if report is None:
                    print(f"\n--- scope: {scope}: family too small to deflate")
                    continue
                print("\n".join(render_scope(report)))

        print()
        print("=" * 78)
        print("Stored ratings - what the live gate actually reads")
        print("=" * 78)
        for boundary in ratings_boundaries:
            print(f"  ⚠ rated population changed: {boundary.describe()}")
        print("\n".join(stored_rating_lines(conn)))
    finally:
        conn.close()


if __name__ == "__main__":
    from utils.stdio import utf8_stdio

    utf8_stdio()
    main()

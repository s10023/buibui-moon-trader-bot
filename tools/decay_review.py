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

Two reading traps this tool cannot fix, both live:

* ``MIN_DSR_TRADES`` gates **count, not dispersion** — ``_sharpe`` rejects only
  ``sd == 0.0`` exactly, so a degenerate cell can clear the floor and inflate the
  trial-family variance by orders of magnitude.
* Absence from the suspect list is **not** a clean bill. Only a cell in neither
  the suspect nor the unscoreable list has passed anything.

Usage::

    PYTHONPATH=. poetry run python tools/decay_review.py
    PYTHONPATH=. poetry run python tools/decay_review.py --day-filter tue_thu
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import duckdb

from analytics.recalibrate_lib import MIN_DSR_TRADES, _sharpe, select_rated_run_ids
from analytics.research_guards import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
)
from analytics.store import DEFAULT_DB_PATH

GATE = 0.95
SCOPES = ("combined", "long", "short")

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

    placeholders = ",".join("?" * len(run_ids))
    rows = conn.execute(
        f"SELECT strategy, timeframe, direction, pnl_r FROM backtest_trades "
        f"WHERE run_id IN ({placeholders}) AND outcome <> 'open' AND pnl_r IS NOT NULL",
        run_ids,
    ).fetchall()
    for strategy, tf, direction, pnl_r in rows:
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
    """Summarise what the live gate actually reads from ``confidence_ratings``."""
    rows = conn.execute(
        """
        SELECT config_name, stars,
               count(*)                                    AS cells,
               sum(CASE WHEN dsr IS NULL THEN 1 ELSE 0 END) AS unscoreable,
               sum(CASE WHEN dsr < 0.95 THEN 1 ELSE 0 END)  AS suspect,
               sum(CASE WHEN dsr >= 0.95 THEN 1 ELSE 0 END) AS clean
        FROM confidence_ratings WHERE stars >= 4
        GROUP BY 1, 2 ORDER BY 1, 2 DESC
        """
    ).fetchall()
    lines = [
        f"{'config':24} {'*':>2} {'cells':>6} {'unscoreable':>12} "
        f"{'suspect':>8} {'clean':>6}"
    ]
    lines.extend(
        f"{r[0]:24} {r[1]:>2} {r[2]:>6} {r[3]:>12} {r[4]:>8} {r[5]:>6}" for r in rows
    )

    total = conn.execute(
        """
        SELECT count(*), sum(CASE WHEN dsr IS NULL THEN 1 ELSE 0 END),
               sum(CASE WHEN dsr >= 0.95 THEN 1 ELSE 0 END)
        FROM confidence_ratings WHERE stars >= 4
        """
    ).fetchone()
    if total is not None:
        lines.append(
            f"\nTOTAL *>=4: {total[0]} cells | {total[1]} unscoreable "
            f"(<{MIN_DSR_TRADES} trades) | {total[2]} clearing DSR {GATE}"
        )
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    parser.add_argument(
        "--day-filter", default=None, help="Scope runs to one day_filter bucket"
    )
    parser.add_argument(
        "--adr-suppress-threshold",
        type=float,
        default=None,
        help="Scope runs to one ADR-suppression threshold",
    )
    args = parser.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        scopes = pools_by_scope(conn, args.day_filter, args.adr_suppress_threshold)

        print("=" * 78)
        print("LEG 1 - DSR-suspect list + gate reachability")
        print("=" * 78)
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
        print("\n".join(stored_rating_lines(conn)))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

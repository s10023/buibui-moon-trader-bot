"""ST104 — score the four saved `detect_eqh_eql` arms and emit the audit inputs.

Brief: `docs/superpowers/specs/2026-08-29-st104-eqh-eql-retune-prereg.md`.
Reads only rows the ST104 sweep itself wrote, keyed on the stored
`detector_params` blob (spec P2), so the ~5.29x `backtest_trades` duplication
factor is sidestepped by construction rather than deduped after the fact.

What each number is, and why it is computed the way it is:

- **Clustering, once.** `n_eff` comes from `cluster_stats` on `utc_day_keys`, and
  the bootstrap resamples whole days. `effective_independent_series` is NOT also
  applied: the two are one correction with two estimators (ST80,
  `n_eff = k / DEFF`), so applying both would deflate twice. The day key is a
  documented LOWER bound on a 24/7 tape, which makes every design effect here a
  floor and every surviving verdict conservative.

- **The gate is three legs**, evaluated by `research_guards.passes_gate`
  (DSR >= 0.95, PBO <= 0.5, boot_lo > 0). MinTRL is not a leg — it is printed as
  a stamp and gates nothing, because against a non-zero target it asks "can I
  confirm Sharpe >= 1", which the deploy core itself would fail.

- **DSR's trial family is the THREE pre-registered treatments**, and its
  `sr_variance` is derived from those three realised Sharpes rather than from the
  spec's 0.0181 plug — the spec declares that plug as provisional and requires it
  to be recomputed here. The baseline is scored against the same family for
  comparability and labelled a reference, not a trial: it was not searched.

- **No Sharpe is folded to `abs()`.** Folding is required when a NEGATIVE-direction
  verdict is gated on DSR or MinTRL, which would otherwise be structurally
  unreachable. Here every gate call asks whether an arm clears on the POSITIVE
  side, and every negative claim goes to `powered_null` (CI containment) instead,
  so folding would only make the gate more permissive for nothing.

- **`bar` is in R per trade** — the units of the observation, which nothing in the
  name says — and is read from the spec's pinned power table at the cell's
  EFFECTIVE n, interpolated in log n. The table is quoted, never re-derived: the
  spec priced it and pins it.

- **Every scored R is NET of modelled cost, and the drag is charged EXACTLY once.**
  `Trade.pnl_r` has subtracted the round-trip drag itself since cost parity, but
  only when the run carried a non-zero `fee_pct` — so this module subtracts
  `portfolio.sizing.round_trip_drag_r` only for runs whose STORED `fee_pct` is
  zero, and leaves an already-charged run alone. Getting this wrong is not a
  rounding error: the drag is `2(fee+slip)*entry/risk`, inversely proportional to
  stop width, and at this detector's structural stops it runs from 0.19R to over
  1R. A first pass of this study ran at zero cost and its CONTROL cleared the
  three-leg gate on the 15m short cell the repo files as a loser. ⚠ Costs are
  MODELLED rather than realised, so every net figure remains an optimistic bound
  whose error runs one way.

- **Tie-break exposure (ST56/ST57)** is counted, not corrected. A trade whose exit
  bar touches both its stop and its target is resolved adverse-first everywhere in
  this repo, so the arm holding more of them is penalised harder; the count states
  which way the bias runs before any cross-arm number is compared.

Usage:
    python3 tools/st104_score.py --db <db> --out docs/plans/st104-scores.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

# A bare `python3 tools/st104_score.py` puts `tools/` on sys.path rather than the
# repo root, so the `analytics.*` imports below die with ModuleNotFoundError. Per
# ST101 the guarantee is `test_bare_invocation_works`, not this line.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.audit_guard import powered_null  # noqa: E402
from analytics.research_guards import (  # noqa: E402
    cluster_bootstrap_ci,
    cluster_stats,
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
    passes_gate,
    utc_day_keys,
)
from analytics.store._common import DEFAULT_DB_PATH  # noqa: E402
from portfolio.sizing import round_trip_drag_r  # noqa: E402
from tools.st104_sweep import ARMS, STRATEGY, arm_params_json  # noqa: E402

if TYPE_CHECKING:
    import duckdb

TREATMENTS = ("T1_tol", "T2_look", "T3_swing")
BASELINE = "baseline"

# The spec's pinned power table: required effect in R PER TRADE at a given
# effective n, 3 trials, sd 1.42. Quoted, never re-derived — the spec priced it.
POWER_BAR: dict[int, float] = {
    250: 0.3127,
    500: 0.2684,
    1000: 0.2373,
    2000: 0.2155,
    4000: 0.2000,
}

# The spec's own screen threshold: a cell whose retuned n falls below this is
# declared UNREACHABLE and not scored.
UNREACHABLE_N = 500

# ST66: a cell whose trades all resolved at the same R clears any count floor and
# then earns a Sharpe in the hundreds off a ~0 denominator. That is not a signal.
MIN_SD = 0.05

SEED = 104


def bar_for(n_eff: float) -> float:
    """The required effect at `n_eff`, interpolated in log n between pinned rows.

    Clamped at both ends rather than extrapolated: below the smallest pinned n
    the bar is the hardest one the spec priced, above the largest it is the
    easiest. Extrapolating a power curve past the range someone actually priced
    is how a bar becomes a number nobody stands behind.
    """
    ns = sorted(POWER_BAR)
    if n_eff <= ns[0]:
        return POWER_BAR[ns[0]]
    if n_eff >= ns[-1]:
        return POWER_BAR[ns[-1]]
    for lo, hi in zip(ns, ns[1:], strict=False):
        if lo <= n_eff <= hi:
            t = (math.log(n_eff) - math.log(lo)) / (math.log(hi) - math.log(lo))
            return POWER_BAR[lo] + t * (POWER_BAR[hi] - POWER_BAR[lo])
    raise AssertionError("unreachable")  # pragma: no cover


def _verdict(cell: Cell, bar: float) -> str:
    """PASS / CONFIRMED-BAD / NO-EDGE / INSUFFICIENT, in that precedence.

    CONFIRMED-BAD is `boot_hi < 0` — the bootstrap CI excludes zero on the
    NEGATIVE side, which is the definition this repo already files verdicts
    under (the structural-touch audit calls five such cells "confirmed-bad
    rather than a powered null"). It ranks ABOVE the powered-null test on
    purpose: a CI that lies wholly below zero and also inside the bar is making
    the stronger of the two statements, and reporting it as NO-EDGE would say
    "no effect was found" about a cell that confidently loses money.

    Everything else negative is CI containment via `powered_null` — never a
    failure-to-clear, a sample-size floor or a p-value restated as a null, which
    is the family that reached six sites in this repo.
    """
    if (
        cell.dsr is not None
        and cell.pbo is not None
        and passes_gate(cell.dsr, cell.pbo, cell.boot_lo)
    ):
        return "PASS"
    if cell.boot_hi < 0.0:
        return "CONFIRMED-BAD"
    if powered_null(cell.boot_lo, cell.boot_hi, bar=bar):
        return "NO-EDGE"
    return "INSUFFICIENT"


def _sharpe(r: np.ndarray) -> float:
    """Per-trade Sharpe, with the ST66 dispersion floor applied.

    Returns nan below the floor so a degenerate cell is excluded from the trial
    family and left unscored, rather than contributing a Sharpe of several
    hundred that zeroes every DSR in its scope.
    """
    if r.size < 2:
        return float("nan")
    sd = float(r.std(ddof=1))
    if sd < MIN_SD:
        return float("nan")
    return float(r.mean() / sd)


@dataclass
class Cell:
    arm: str
    timeframe: str
    direction: str
    n: int
    n_clusters: int
    design_effect: float
    n_eff: float
    avg_r: float
    avg_r_gross: float
    avg_drag: float
    sd: float
    sharpe: float
    boot_lo: float
    boot_hi: float
    bar: float
    ambiguous: int
    ambiguous_pct: float
    dsr: float | None = None
    pbo: float | None = None
    min_trl: float | None = None
    verdict: str = ""
    notes: str = ""


def load_trades(
    conn: duckdb.DuckDBPyConnection, arm: str
) -> dict[tuple[str, str], tuple[np.ndarray, list[int], int, np.ndarray]]:
    """(net R, day keys, ambiguous count, drag) per (timeframe, direction).

    A trade is tie-break AMBIGUOUS when its exit bar's range spans both its stop
    and its target, so OHLC alone cannot say which came first. The join is on the
    exit bar in `ohlcv`, the view consumers read.
    """
    rows = conn.execute(
        """
        SELECT t.timeframe, t.direction, t.pnl_r, t.entry_time,
               t.entry_price, t.sl_price, r.fee_pct,
               CASE WHEN o.low IS NULL THEN 0
                    WHEN o.low <= LEAST(t.sl_price, t.tp_price)
                     AND o.high >= GREATEST(t.sl_price, t.tp_price) THEN 1
                    ELSE 0 END AS ambiguous
        FROM backtest_trades t
        JOIN backtest_runs r ON r.run_id = t.run_id
        LEFT JOIN ohlcv o
          ON o.symbol = t.symbol AND o.timeframe = t.timeframe
         AND o.open_time = t.exit_time
        WHERE r.strategy = ? AND r.detector_params = ?
          AND t.pnl_r IS NOT NULL
        """,
        [STRATEGY, arm_params_json(arm)],
    ).fetchall()

    grouped: dict[tuple[str, str], list[tuple[float, int, int, float, float]]] = {}
    for (
        tf,
        direction,
        pnl_r,
        entry_time,
        entry_price,
        sl_price,
        fee_pct,
        ambiguous,
    ) in rows:
        # A run with a non-zero fee_pct already had the drag taken out by the
        # engine; charging it again here would double-count it.
        # The modelled drag is ALWAYS computed, because it is the stop-geometry
        # story this study turns on: it is `2(fee+slip)*entry/risk`, so an arm
        # that finds levels further away pays less of it in R. It is only
        # SUBTRACTED when the engine has not already charged it.
        drag_r = round_trip_drag_r(float(entry_price), float(sl_price))
        already_net = float(fee_pct) > 0.0
        grouped.setdefault((str(tf), str(direction)), []).append(
            (
                float(pnl_r),
                int(entry_time),
                int(ambiguous),
                drag_r,
                0.0 if already_net else drag_r,
            )
        )

    out: dict[tuple[str, str], tuple[np.ndarray, list[int], int, np.ndarray]] = {}
    for key, vals in grouped.items():
        gross = np.asarray([v[0] for v in vals], dtype=np.float64)
        drag = np.asarray([v[3] for v in vals], dtype=np.float64)
        keys = utc_day_keys([v[1] for v in vals])
        charged = np.asarray([v[4] for v in vals], dtype=np.float64)
        out[key] = (gross - charged, keys, sum(v[2] for v in vals), drag)
    return out


def daily_matrix(
    per_arm: dict[str, tuple[np.ndarray, list[int]]],
) -> np.ndarray | None:
    """A (days, trials) mean-R matrix over the days every trial has in common.

    PBO needs one time axis shared by every trial. Restricting to the common days
    is what makes the columns comparable; an arm that fires on days another never
    sees would otherwise contribute rows of its own.
    """
    if len(per_arm) < 2:
        return None
    day_sets = [set(keys) for _, keys in per_arm.values()]
    common = sorted(set.intersection(*day_sets))
    if len(common) < 28:  # cscv_pbo default needs >= 2 rows in each of 14 blocks
        return None
    index = {d: i for i, d in enumerate(common)}
    cols = []
    for arr, keys in per_arm.values():
        sums = np.zeros(len(common))
        counts = np.zeros(len(common))
        for value, day in zip(arr, keys, strict=True):
            i = index.get(day)
            if i is not None:
                sums[i] += value
                counts[i] += 1
        cols.append(np.divide(sums, np.maximum(counts, 1.0)))
    return np.column_stack(cols)


def score(conn: duckdb.DuckDBPyConnection) -> list[Cell]:
    per_arm = {arm: load_trades(conn, arm) for arm in ARMS}
    cells: list[Cell] = []

    keys = sorted({k for arm in ARMS for k in per_arm[arm]})
    for tf, direction in keys:
        # The trial family for this cell: the three pre-registered treatments.
        family: dict[str, tuple[np.ndarray, list[int]]] = {}
        for arm in TREATMENTS:
            got = per_arm[arm].get((tf, direction))
            if got is not None:
                family[arm] = (got[0], got[1])
        trial_srs = [
            s for s in (_sharpe(a) for a, _ in family.values()) if math.isfinite(s)
        ]
        matrix = daily_matrix(family)
        pbo_value: float | None = None
        if matrix is not None and matrix.shape[1] >= 2:
            try:
                pbo_value = float(cscv_pbo(matrix).pbo)
            except ValueError:
                pbo_value = None

        for arm in (BASELINE, *TREATMENTS):
            got = per_arm[arm].get((tf, direction))
            if got is None:
                continue
            arr, day_keys, ambiguous, drag = got
            stats = cluster_stats(arr, day_keys)
            ci = cluster_bootstrap_ci(
                arr, day_keys, lambda x: float(x.mean()), seed=SEED
            )
            sd = float(arr.std(ddof=1)) if arr.size > 1 else 0.0
            sr = _sharpe(arr)
            bar = bar_for(stats.n_eff)
            cell = Cell(
                arm=arm,
                timeframe=tf,
                direction=direction,
                n=int(arr.size),
                n_clusters=int(stats.n_clusters),
                design_effect=round(float(stats.design_effect), 4),
                n_eff=round(float(stats.n_eff), 1),
                avg_r=round(float(arr.mean()), 4),
                avg_r_gross=round(float((arr + drag).mean()), 4),
                avg_drag=round(float(drag.mean()), 4),
                sd=round(sd, 4),
                sharpe=round(sr, 4) if math.isfinite(sr) else float("nan"),
                boot_lo=round(float(ci.lo), 4),
                boot_hi=round(float(ci.hi), 4),
                bar=round(bar, 4),
                ambiguous=ambiguous,
                ambiguous_pct=round(100.0 * ambiguous / arr.size, 2) if arr.size else 0,
            )

            if arm != BASELINE and int(arr.size) < UNREACHABLE_N:
                cell.verdict = "UNREACHABLE"
                cell.notes = f"n {arr.size} < {UNREACHABLE_N} screen floor"
                cells.append(cell)
                continue
            if not math.isfinite(sr):
                cell.verdict = "INSUFFICIENT"
                cell.notes = f"sd {sd:.4f} below the {MIN_SD} dispersion floor"
                cells.append(cell)
                continue

            if len(trial_srs) >= 2:
                cell.dsr = round(
                    deflated_sharpe_ratio(sr, int(arr.size), trial_srs=trial_srs), 4
                )
            cell.pbo = round(pbo_value, 4) if pbo_value is not None else None
            cell.min_trl = round(
                min_track_record_length(sr, target_sr=0.0, skew=0.0, kurtosis=3.0), 1
            )

            # Called unconditionally: a missing gate leg cannot yield PASS, but
            # the CI-containment branches still apply, and the spec's success
            # metric is a NAMED verdict per arm. Guarding this call left one
            # cell with an empty verdict, which reads as an omission rather than
            # as the INSUFFICIENT it is.
            cell.verdict = _verdict(cell, bar)
            if cell.dsr is None or cell.pbo is None:
                cell.notes = "no trial family / PBO — gate leg unavailable"
            cells.append(cell)
    return cells


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    import duckdb  # noqa: PLC0415 - deferred so --help works without the venv

    with duckdb.connect(str(args.db), read_only=True) as conn:
        cells = score(conn)

    header = (
        f"{'arm':<9} {'tf':>3} {'dir':>5} {'n':>7} {'days':>5} {'DEFF':>6} "
        f"{'n_eff':>7} {'avg_r':>8} {'sharpe':>7} {'boot_lo':>8} {'boot_hi':>8} "
        f"{'bar':>6} {'DSR':>6} {'PBO':>5} {'amb%':>5}  verdict"
    )
    print(header)
    print("-" * len(header))
    for c in sorted(cells, key=lambda x: (x.timeframe, x.direction, x.arm)):
        dsr = f"{c.dsr:.3f}" if c.dsr is not None else "  -  "
        pbo = f"{c.pbo:.3f}" if c.pbo is not None else "  -  "
        print(
            f"{c.arm:<9} {c.timeframe:>3} {c.direction:>5} {c.n:>7} "
            f"{c.n_clusters:>5} {c.design_effect:>6.3f} {c.n_eff:>7.1f} "
            f"{c.avg_drag:>6.3f} {c.avg_r:>8.4f} {c.sharpe:>7.3f} {c.boot_lo:>8.4f} {c.boot_hi:>8.4f} "
            f"{c.bar:>6.3f} {dsr:>6} {pbo:>5} {c.ambiguous_pct:>5.2f}  {c.verdict}"
            + (f"  ({c.notes})" if c.notes else "")
        )

    if args.out is not None:
        payload: dict[str, Any] = {
            "arms": {a: ARMS[a] for a in ARMS},
            "cells": [asdict(c) for c in cells],
        }
        args.out.write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())

"""Price the power of a multi-regime detector validation BEFORE designing it.

Convention matched to ``recalibrate_lib._scope_dsr``: the observation is ONE
TRADE, ``sr = mean(pnl_r) / std(pnl_r)`` per trade, ``n_obs`` = trades in the
cell, and the trial family is the set of cell Sharpes. Everything below is in
per-trade units.

The result that shaped the design, and that generalises past it: **trial count
dominates n, and it is not close.** A 21× range of n moves the DSR bar ~10%,
while 1 → 320 trials moves it 21× (+0.049R → +1.035R against a corpus best of
+1.196R). A per-cell scan is therefore structurally unreachable, and designs
must be built around trial count rather than sample size.

Two measurement traps it also surfaces, both live:

* ``backtest_trades`` carries a **~5.29× duplication factor** from repeated runs
  (857,740 rows → 162,263 distinct). Dedup on
  ``(symbol, timeframe, strategy, direction, entry_time)`` or every ``n``
  inflates ~5× and every ``t`` ~2.3×.
* One cell (``bos/1d/long``, 36 trades all ≈ −1.0076R, sd 0.0022) carries a
  per-trade Sharpe of −461 and inflates this panel's trial-family variance
  0.0348 → 1729.89. It is excluded here, and the exclusion is **disclosed rather
  than silent**. Since ST66 ``recalibrate_lib.MIN_DSR_SD`` excludes it in
  production too, so this is no longer a divergence between the two — but the
  exclusion stays explicit here because this panel builds its own family and must
  not inherit the floor by accident.

Promoted out of ``docs/plans/scratch/`` 2026-08-14: it imports production code
(``analytics.research_guards``), and an untracked consumer of a shared function
drifts silently when that function's semantics change.

Usage::

    PYTHONPATH=. poetry run python tools/multi_regime_power.py
"""

from __future__ import annotations

import argparse
import math
import statistics
from statistics import NormalDist

import duckdb

from analytics.research_guards import GATE_DSR, expected_max_sharpe, required_sharpe
from analytics.store import DEFAULT_DB_PATH, load_backtest_trades

NORM = NormalDist()
Z_GATE = NORM.inv_cdf(GATE_DSR)

DISP_FLOOR = 0.05
MIN_CELL_TRADES = 30
TRIAL_COUNTS = (1, 4, 16, 40, 80, 320)
GRAINS = ("15m", "1h", "4h", "1d")
# Symbols available per grain in one regime leg; 15m is the 3-symbol panel.
SYMS = {"15m": 3, "1h": 14, "4h": 14, "1d": 14}
LEG_DAYS = 365


def required_sr(n_obs: int, sr0: float, skew: float, kurt: float) -> float | None:
    """Smallest per-trade Sharpe clearing ``DSR >= GATE_DSR`` at ``n_obs``, given ``sr0``.

    Delegates to :func:`analytics.research_guards.required_sharpe`, which
    inverts the production ``probabilistic_sharpe_ratio`` rather than a
    hand-derived closed form. Returns ``None`` when the gate is unreachable
    at any Sharpe, which is a finding rather than an error: a bar no cell can
    clear reports "everything is suspect" as an artifact.

    Keeps the pre-promotion ``sr0 + 5.0`` unreachable-window guard: measured
    disagreement (n_obs=3, sr0 in {1.0, 2.0}) shows the promoted search's
    wider ``1e6`` window finds a finite answer beyond the old boundary, so
    the old boundary is preserved here rather than silently widening the
    reported values.
    """
    if n_obs < 2:
        return None

    def z_of(sr: float) -> float:
        var = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
        if var <= 0.0:
            return float("inf")
        return (sr - sr0) * math.sqrt(n_obs - 1) / math.sqrt(var)

    if z_of(sr0 + 5.0) < Z_GATE:
        return None
    got = required_sharpe(n_obs, benchmark_sr=sr0, skew=skew, kurtosis=kurt)
    return None if math.isinf(got) else got


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="analytics DB path")
    args = parser.parse_args()

    conn = duckdb.connect(args.db, read_only=True)

    # ---- 1. Duplication factor -------------------------------------------
    cols = ("symbol", "timeframe", "strategy", "direction", "entry_time", "pnl_r")
    raw = load_backtest_trades(conn, columns=cols, not_null=(), dedup_on=None)
    if raw.empty:
        raise RuntimeError("backtest_trades is empty — nothing to price.")
    pool = load_backtest_trades(conn, columns=cols, not_null=())
    tot, uniq = len(raw), len(pool)
    print(
        f"=== dedup check ===\n  rows={tot:,}  distinct(sym,tf,strat,dir,entry)={uniq:,}"
        f"  dup_factor={tot / uniq:.2f}x"
    )

    # ---- 2. Real per-trade R distribution ---------------------------------
    print("\n=== pooled per-trade R distribution (deduped) ===")
    closed = pool[pool["pnl_r"].notna()]
    rs = [float(r) for r in closed["pnl_r"]]
    mean_r, sd_r = statistics.fmean(rs), statistics.stdev(rs)
    skew = sum((x - mean_r) ** 3 for x in rs) / len(rs) / sd_r**3
    kurt = sum((x - mean_r) ** 4 for x in rs) / len(rs) / sd_r**4
    print(f"  n={len(rs):,}  mean_r={mean_r:+.4f}  std_r={sd_r:.4f}")
    print(f"  skew={skew:+.3f}  kurtosis(non-excess)={kurt:.3f}")
    print(f"  => per-trade Sharpe of the POOLED book = {mean_r / sd_r:+.4f}")
    print(f"  => 1 unit of per-trade Sharpe == {sd_r:.3f} R of avg_r")

    # ---- 3. Trial-Sharpe dispersion (drives the deflation sr0) -------------
    print("\n=== cell family: per-trade Sharpe dispersion ===")
    agg = (
        closed.groupby(["strategy", "timeframe", "direction"])["pnl_r"]
        .agg(["count", "mean", "std"])
        .reset_index()
    )
    agg = agg[(agg["count"] >= MIN_CELL_TRADES) & (agg["std"] > 0)]
    cells = [
        (s, tf, d, int(n), float(mu), float(sd))
        for s, tf, d, n, mu, sd in agg.itertuples(index=False, name=None)
    ]
    degenerate = [r for r in cells if r[5] < DISP_FLOOR]
    cells = [r for r in cells if r[5] >= DISP_FLOOR]
    cell_srs = [r[4] / r[5] for r in cells]
    sr_var = statistics.variance(cell_srs)
    print(
        f"  excluded {len(degenerate)} degenerate cell(s) (std_r < {DISP_FLOOR}): "
        + ", ".join(f"{r[0]}/{r[1]}/{r[2]}" for r in degenerate)
    )
    print(f"  scoreable cells (n>={MIN_CELL_TRADES}): {len(cells)}")
    print(
        f"  cell Sharpe: mean={statistics.fmean(cell_srs):+.4f} sd={math.sqrt(sr_var):.4f}"
        f"  min={min(cell_srs):+.3f} max={max(cell_srs):+.3f}"
    )

    # ---- 4. Trade RATE per grain -> projected n per regime window ----------
    print("\n=== projected trades per cell per regime window ===")
    rate = [
        (
            str(tf),
            len(g)
            / (
                g["symbol"].nunique()
                * (g["entry_time"].max() - g["entry_time"].min())
                / 86400000.0
            ),
        )
        for tf, g in pool.groupby("timeframe")
    ]
    print("  (trades per symbol-day, pooled over strategies+directions)")
    proj: dict[str, float] = {}
    for tf, per_symbol_day in sorted(rate):
        if tf not in SYMS:
            continue
        n_strat = int(raw.loc[raw["timeframe"] == tf, "strategy"].nunique())
        cell_rate = per_symbol_day / max(n_strat * 2, 1)
        proj[tf] = cell_rate * SYMS[tf] * LEG_DAYS
        print(
            f"  {tf:>4}  pooled={per_symbol_day:6.3f}/sym-day  strategies={n_strat:>2}"
            f"  => per-cell n in ONE {LEG_DAYS}d leg ({SYMS[tf]} syms) = {proj[tf]:,.0f}"
        )

    # ---- 5. THE PRICE: required avg_r at the real n ------------------------
    print("\n=== POWER PRICE: avg_r required to clear DSR >= 0.95 ===")
    print(
        f"  (skew={skew:+.2f}, kurt={kurt:.2f} from the real R distribution; 1 Sharpe = {sd_r:.3f} R)"
    )
    for n_trials in TRIAL_COUNTS:
        sr0 = expected_max_sharpe(n_trials, sr_var) if n_trials >= 2 else 0.0
        print(
            f"\n  --- {n_trials} trial(s):  deflation benchmark sr0 = {sr0:+.4f}"
            f" (= {sr0 * sd_r:+.3f} R) ---"
        )
        for tf in GRAINS:
            n = int(proj.get(tf, 0))
            if n < 2:
                continue
            need = required_sr(n, sr0, skew, kurt)
            if need is None:
                print(f"    {tf:>4}  n={n:>6,}  UNREACHABLE at any Sharpe")
            else:
                print(
                    f"    {tf:>4}  n={n:>6,}  need sr>={need:.4f}  => avg_r >= {need * sd_r:+.4f} R"
                )

    # ---- 6. What do cells actually achieve? --------------------------------
    print("\n=== reality check: observed cell avg_r (n>=30 cells) ===")
    mus = sorted((r[4] for r in cells), reverse=True)
    print(
        f"  best={mus[0]:+.4f}R  p90={mus[int(len(mus) * 0.1)]:+.4f}R"
        f"  median={statistics.median(mus):+.4f}R"
    )
    print(f"  cells with avg_r > 0: {sum(1 for m in mus if m > 0)} of {len(mus)}")


if __name__ == "__main__":
    from utils.stdio import utf8_stdio

    utf8_stdio()
    main()

"""Recompute the t-deflator for the ACTUAL multi-regime panels.

Pooling symbol-days across a correlated universe inflates every t-stat: the
filed **2.92×** belongs to the 25-symbol forecast panel, whose mean pairwise
correlation of net returns is 0.315 (25 perps carry the noise reduction of 2.92
effective independent series). The multi-regime bull leg runs 14 symbols and the
15m grain runs 3, so both must be recomputed — **reusing 2.92× here would be the
H15 portable-number trap in a new location**, a bare number that looks portable
and silently changes meaning with the panel.

The counter-intuitive result this tool exists to produce: breadth buys almost
nothing once the cross-section is nearly one asset — 14 perps carry an n_eff of
about 2, barely above the 3-symbol panel's. **Run the tool for the pair; do not
quote one from prose, this docstring included.** A deflator is meaningless away
from the panel it was computed on, and the arithmetic that catches a carried one
is ``n_eff × deflator² == k`` — it must recover the symbol count, and the
deflator RISES with k, so a pair that falls as k rises has been crossed between
panels. This paragraph named 1.628× / 3.331× until 2026-08-25 and both failed
that check (5.2 and 15.8 against 14 and 3): they are real published figures from
the *other* study's panels, pasted onto this one's n_eff values.

Also prices the paired regime-difference MDE, which is the statistic the
validation needs (does a cell's edge PERSIST), as opposed to a per-leg DSR scan.
**The MDE is a planning and scale number only** — it is a function of SE alone,
so it can say how large an effect would have been visible and never whether a
small one was ruled out. Verdicts belong to
:func:`analytics.audit_guard.powered_null`; see ``tools/multi_regime_study.py``.

Promoted out of ``docs/plans/scratch/`` 2026-08-14: it imports production code
(:func:`analytics.forecast.attribution.effective_independent_series`), and an
untracked consumer of a shared function drifts silently when that function's
semantics change.

Usage::

    PYTHONPATH=. poetry run python tools/multi_regime_deflator.py
"""

from __future__ import annotations

import argparse
import math
from datetime import UTC, datetime

import duckdb
import pandas as pd

from analytics.forecast.attribution import effective_independent_series
from analytics.store import DEFAULT_DB_PATH

# Pooled per-trade R std, measured on the deduplicated backtest_trades corpus.
SD_R = 1.850
# Trades per cell per leg, measured. Used only to price the MDE.
N_CELL = {"15m": 3741, "1h": 4394, "4h": 1141, "1d": 176}
# 80% power, two-sided 95% => MDE = (1.960 + 0.842) * SE
MDE_Z = 2.802
MIN_OBS_PER_SERIES = 30

LEGS = [("bull_2021", 2021), ("bear_2022", 2022)]
GRAINS = ("15m", "1h", "4h", "1d")


def ms(year: int) -> int:
    """UTC epoch-ms for Jan 1 of ``year`` — computed in Python, never in SQL.

    DuckDB's session TimeZone is ``Asia/Kuala_Lumpur`` here, so a SQL-side
    boundary would land 8h off and silently reassign bars between legs.
    """
    return int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000)


def panel_deflator(
    conn: duckdb.DuckDBPyConnection, tf: str, lo: int, hi: int
) -> tuple[int, float, float]:
    """``(k symbols, n_eff, t_deflator)`` from daily returns of each symbol's closes.

    Series with fewer than ``MIN_OBS_PER_SERIES`` observations are dropped: a
    correlation estimated on a handful of points is noise, and it enters the
    deflator with the same weight as a full one.
    """
    df = conn.execute(
        "select symbol, open_time, close from ohlcv "
        "where timeframe=? and open_time>=? and open_time<? order by 2",
        [tf, lo, hi],
    ).fetchdf()
    if df.empty:
        return 0, 0.0, 1.0
    df["day"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.floor("D")
    daily = df.groupby(["symbol", "day"])["close"].last().unstack(0)
    rets = daily.pct_change().dropna(how="all")
    series = {
        s: rets[s].dropna()
        for s in rets.columns
        if rets[s].notna().sum() > MIN_OBS_PER_SERIES
    }
    if len(series) < 2:
        return len(series), float(len(series)), 1.0
    n_eff, deflator = effective_independent_series(series)
    return len(series), n_eff, deflator


def paired_mde(
    n_per_leg: int, deflator: float, *, sd_r: float = SD_R
) -> tuple[float, float]:
    """``(SE, MDE)`` in R for a paired two-leg difference at ``n_per_leg`` each."""
    se = sd_r * math.sqrt(2.0 / n_per_leg) * deflator
    return se, MDE_Z * se


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="analytics DB path")
    args = parser.parse_args()

    conn = duckdb.connect(args.db, read_only=True)

    print(
        "=== t-deflator per ACTUAL panel (the filed 2.92x is the 25-symbol number) ==="
    )
    deflators: dict[tuple[str, str], float] = {}
    for tf in GRAINS:
        for name, year in LEGS:
            k, n_eff, deflator = panel_deflator(conn, tf, ms(year), ms(year + 1))
            deflators[(tf, name)] = deflator
            print(
                f"  {tf:>4} {name:<10} k={k:>3}  n_eff={n_eff:5.2f}  t_deflator={deflator:.3f}x"
            )

    print(
        f"\n=== MDE of a PAIRED regime difference (bull avg_r − bear avg_r), sd_r={SD_R} ==="
    )
    print(f"  80% power, two-sided 95%  =>  MDE = {MDE_Z} * SE")
    for tf, n in N_CELL.items():
        # Conservative: the worse (larger) of the two legs' deflators for that grain.
        deflator = max(deflators.get((tf, name), 1.0) for name, _ in LEGS)
        se, mde = paired_mde(n, deflator)
        se_naive = SD_R * math.sqrt(2.0 / n)
        print(
            f"  {tf:>4}  n/leg={n:>5,}  SE_naive={se_naive:.4f}R  x{deflator:.2f}"
            f"  => SE={se:.4f}R   MDE={mde:+.3f}R"
        )
    print("\nMDE is a PLANNING number. A verdict needs the CI sized against the bar —")
    print("see analytics.audit_guard.powered_null and tools/multi_regime_study.py.")


if __name__ == "__main__":
    main()

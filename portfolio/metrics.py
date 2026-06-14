"""Risk-adjusted metrics on a daily equity curve + sized-trade attribution.

Pure functions over a pandas daily curve (Series indexed by UTC day) and the
`SizedTrade` list from `portfolio.book`. Annualization defaults to 365 days
(crypto trades every day). Degenerate inputs (flat / single-point curves)
return 0.0 rather than NaN.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from portfolio.book import BookResult, SizedTrade

_PPY = 365.0


def daily_returns(curve: pd.Series) -> pd.Series:
    return curve.pct_change().dropna()


def sharpe(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    r = daily_returns(curve)
    sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    # Guard: treat machine-epsilon variance (pure drift, no noise) as degenerate.
    if sd < 1e-10:
        return 0.0
    return float(r.mean() / sd * math.sqrt(periods_per_year))


def sortino(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    r = daily_returns(curve)
    if len(r) < 2:
        return 0.0
    downside = r[r < 0.0]
    dd = float(math.sqrt(float((downside**2).mean()))) if len(downside) > 0 else 0.0
    if dd <= 0.0:
        return 0.0
    return float(r.mean() / dd * math.sqrt(periods_per_year))


def max_drawdown(curve: pd.Series) -> float:
    """Worst peak-to-trough return (≤ 0.0)."""
    if len(curve) < 2:
        return 0.0
    roll_max = curve.cummax()
    dd = (curve - roll_max) / roll_max
    return float(dd.min())


def annual_return(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    if len(curve) < 2 or curve.iloc[0] <= 0.0:
        return 0.0
    total = curve.iloc[-1] / curve.iloc[0]
    if total <= 0.0:
        return -1.0
    return float(total ** (periods_per_year / len(curve)) - 1.0)


def annual_vol(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    r = daily_returns(curve)
    sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    return sd * math.sqrt(periods_per_year)


def calmar(curve: pd.Series, periods_per_year: float = _PPY) -> float:
    mdd = abs(max_drawdown(curve))
    if mdd <= 0.0:
        return 0.0
    return annual_return(curve, periods_per_year) / mdd


def avg_exposure(result: BookResult) -> float:
    """Mean daily gross open-risk fraction across the curve."""
    n = len(result.daily_index)
    if n == 0:
        return 0.0
    open_risk = np.zeros(n)
    for t in result.sized:
        open_risk[t.entry_idx : max(t.exit_idx, t.entry_idx + 1)] += t.r_eff
    return float(open_risk.mean())


def risk_turnover(result: BookResult) -> float:
    """Σ risk-capital deployed ÷ mean fixed-basis equity (dimensionless)."""
    equity = result.capital + result.pnl_fixed
    mean_eq = float(equity.mean()) if len(equity) else result.capital
    deployed = sum(t.rc_fixed for t in result.sized)
    return deployed / mean_eq if mean_eq > 0.0 else 0.0


def attribution(
    sized: list[SizedTrade],
    by: tuple[str, ...] = ("strategy", "tf", "direction"),
) -> pd.DataFrame:
    """Per-bucket realized P&L (fixed basis) + trade count + total/avg R."""
    if not sized:
        return pd.DataFrame()
    rows = [
        {
            "strategy": t.strategy,
            "tf": t.tf,
            "direction": t.direction,
            "pnl_fixed": t.pnl_fixed,
            "realized_r": t.realized_r,
        }
        for t in sized
    ]
    df = pd.DataFrame(rows)
    agg = (
        df.groupby(list(by))
        .agg(
            n=("pnl_fixed", "size"),
            total_pnl=("pnl_fixed", "sum"),
            total_r=("realized_r", "sum"),
            avg_r=("realized_r", "mean"),
        )
        .reset_index()
        .sort_values("total_pnl", ascending=False)
        .reset_index(drop=True)
    )
    return agg

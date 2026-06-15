"""Render a replay result into a terminal report (pure string builder)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from portfolio import metrics
from portfolio.book import BookResult
from portfolio.sizing import SizingConfig


def _curve(values: np.ndarray, index: np.ndarray) -> pd.Series:
    return pd.Series(values, index=pd.to_datetime(index, unit="ms", utc=True))


def _fmt(x: float) -> str:
    return f"{x:+.2f}"


def format_report(res: BookResult, cfg: SizingConfig) -> str:
    if not res.sized:
        return "P1 paper portfolio: no resolved ledger rows to replay."
    fixed = cfg.capital + res.pnl_fixed
    comp = cfg.capital + res.pnl_comp
    fixed_curve = _curve(fixed, res.daily_index)
    comp_curve = _curve(comp, res.daily_index)
    ppy = cfg.annualization_days

    lines: list[str] = []
    lines.append("=== P1 Paper Portfolio — policy #0 (today's exits) ===")
    lines.append(
        f"trades sized={len(res.sized)}  skipped={len(res.skipped)}  "
        f"days={len(res.daily_index)}  capital={cfg.capital:,.0f}"
    )
    lines.append("")
    lines.append("-- HEADLINE: fixed-notional / constant-R --")
    lines.append(f"  Sharpe        {metrics.sharpe(fixed_curve, ppy):+.2f}")
    lines.append(f"  Sortino       {metrics.sortino(fixed_curve, ppy):+.2f}")
    lines.append(f"  Calmar        {metrics.calmar(fixed_curve, ppy):+.2f}")
    lines.append(f"  Max drawdown  {metrics.max_drawdown(fixed_curve):+.1%}")
    lines.append(f"  Ann. return   {metrics.annual_return(fixed_curve, ppy):+.1%}")
    lines.append(
        f"  Ann. vol      {metrics.annual_vol(fixed_curve, ppy):.1%} "
        f"(target {cfg.vol_target_annual:.0%})"
    )
    lines.append(f"  Avg exposure  {metrics.avg_exposure(res):.2%} gross open risk")
    lines.append(f"  Risk turnover {metrics.risk_turnover(res):.1f}x")
    lines.append(f"  Final equity  {fixed[-1]:,.0f}")
    lines.append("")
    lines.append("-- compounding curve (governor basis) --")
    lines.append(f"  Sharpe        {metrics.sharpe(comp_curve, ppy):+.2f}")
    lines.append(f"  Max drawdown  {metrics.max_drawdown(comp_curve):+.1%}")
    lines.append(f"  Final equity  {comp[-1]:,.0f}")
    lines.append("")
    lines.append("-- Attribution (fixed basis, by strategy×tf×direction) --")
    attr = metrics.attribution(res.sized)
    lines.append(attr.to_string(index=False, float_format=_fmt))
    return "\n".join(lines)

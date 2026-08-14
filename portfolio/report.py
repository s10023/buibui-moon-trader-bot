"""Render a replay result into a terminal report (pure string builder)."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from analytics.eras import EraBoundary, straddle_report
from portfolio import metrics
from portfolio.book import BookResult
from portfolio.sizing import SizingConfig


def _curve(values: np.ndarray, index: np.ndarray) -> pd.Series:
    return pd.Series(values, index=pd.to_datetime(index, unit="ms", utc=True))


def _fmt(x: float) -> str:
    return f"{x:+.2f}"


def _era_lines(res: BookResult, boundaries: Sequence[EraBoundary] | None) -> list[str]:
    """Era check for the replayed ledger, or an explicit statement that it did not run.

    ``boundaries=None`` prints NOT RUN rather than nothing. A silently-skipped check
    reads exactly like a passed one, which is the failure this whole module family
    exists to prevent — so the absence has to be visible in the output.

    The era key here is the trade's ENTRY time, and that is correct for a ledger
    sample specifically: an alert fired under whatever code was live at that moment.
    Do not copy this to a backtest sample, where entry time is simulated market time
    (see ``analytics.eras``).

    ``SizedTrade`` stores ``entry_idx`` — a position in ``daily_index`` — not a
    timestamp, so the index is resolved here rather than assumed.
    """
    if boundaries is None:
        return [
            "  era check: NOT RUN — no boundaries supplied, so this report makes no "
            "claim about whether the sample spans a rule change."
        ]
    entries = [
        int(res.daily_index[t.entry_idx])
        for t in res.sized
        if 0 <= t.entry_idx < len(res.daily_index)
    ]
    return straddle_report(boundaries, entries)


def format_report(
    res: BookResult,
    cfg: SizingConfig,
    boundaries: Sequence[EraBoundary] | None = None,
) -> str:
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
    lines.extend(_era_lines(res, boundaries))
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

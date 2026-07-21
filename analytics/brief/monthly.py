"""Monthly context for the brief panel (M5) — descriptive, NOT a cone.

Conn-free: consumes the completed-1d frame the bundle already fetched.
Deliberately three numbers: ~90 months of history split by direction leaves
~45 per cell, too thin for percentile bands (spec §2.1).

The month-to-date return is compared against COMPLETED prior months, which is
an apples-to-oranges comparison by construction; `mtd_elapsed_frac` is carried
so the renderer can disclose it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from analytics.brief.types import MonthlyContext


def build_monthly_context(
    *,
    completed_1d: pd.DataFrame,
    as_of_ms: int,
) -> tuple[MonthlyContext | None, list[str]]:
    """(context, notes). None whenever there is nothing meaningful to say."""
    if completed_1d.empty or "open_time" not in completed_1d:
        return None, ["monthly context: no completed 1d bars"]

    df = completed_1d.copy()
    ts = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["ym"] = ts.dt.strftime("%Y-%m")
    as_of = datetime.fromtimestamp(as_of_ms / 1000, tz=UTC)
    current_ym = as_of.strftime("%Y-%m")

    current = df[df["ym"] == current_ym]
    if current.empty:
        return None, ["monthly context: no bars in the current month"]

    month_open = float(current.iloc[0]["open"])
    if month_open <= 0:
        return None, ["monthly context: non-positive month open"]
    price = float(current.iloc[-1]["close"])
    mtd_return_pct = (price - month_open) / month_open * 100.0

    month_high = float(current["high"].max())
    month_low = float(current["low"].min())
    range_position: float | None = None
    if month_high > month_low:
        range_position = (price - month_low) / (month_high - month_low)

    prior_returns: list[float] = []
    for _ym, grp in df[df["ym"] != current_ym].groupby("ym", sort=True):
        o = float(grp.iloc[0]["open"])
        if o > 0:
            prior_returns.append((float(grp.iloc[-1]["close"]) - o) / o * 100.0)

    n_months = len(prior_returns)
    if n_months == 0:
        pct_of_months = 50.0
    else:
        below = sum(1 for r in prior_returns if r < mtd_return_pct)
        pct_of_months = below / n_months * 100.0

    days_in_month = pd.Period(current_ym, freq="M").days_in_month
    elapsed_frac = min(as_of.day / days_in_month, 1.0)

    return (
        MonthlyContext(
            mtd_return_pct=mtd_return_pct,
            mtd_elapsed_frac=elapsed_frac,
            pct_of_months=pct_of_months,
            n_months=n_months,
            range_position=range_position,
        ),
        [],
    )

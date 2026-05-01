"""Seasonality summary stats helper.

Extracted from `analytics/indicators_lib.py` in strat-1. No behaviour change.

Note: this is the analytics helper consumed by `analytics/backtest_runner.py`
and the `seasonality` strategy detector. It is NOT a `detect_*` function.
"""

import pandas as pd

SEASONALITY_COLUMNS: list[str] = [
    "period_type",
    "period_value",
    "avg_return_pct",
    "win_rate",
    "count",
]


def seasonality_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Compute average returns by day-of-week, hour-of-day, and week-of-month.

    df must have columns: open_time (Unix ms BIGINT), open (float), close (float).
    Returns a DataFrame with SEASONALITY_COLUMNS.
    """
    if df.empty or len(df) < 2:
        return pd.DataFrame(columns=SEASONALITY_COLUMNS)

    work = df[["open_time", "open", "close"]].copy()
    work["ts"] = pd.to_datetime(work["open_time"].astype("int64"), unit="ms", utc=True)
    work["return_pct"] = (work["close"] - work["open"]) / work["open"] * 100.0
    work["is_win"] = work["return_pct"] > 0.0

    rows: list[dict[str, object]] = []

    for dow, group in work.groupby(work["ts"].dt.dayofweek):
        rows.append(
            {
                "period_type": "day_of_week",
                "period_value": int(dow),
                "avg_return_pct": float(group["return_pct"].mean()),
                "win_rate": float(group["is_win"].mean()),
                "count": int(len(group)),
            }
        )

    for hour, group in work.groupby(work["ts"].dt.hour):
        rows.append(
            {
                "period_type": "hour_of_day",
                "period_value": int(hour),
                "avg_return_pct": float(group["return_pct"].mean()),
                "win_rate": float(group["is_win"].mean()),
                "count": int(len(group)),
            }
        )

    work["week_of_month"] = (work["ts"].dt.day - 1) // 7 + 1
    for wom, group in work.groupby(work["week_of_month"]):
        rows.append(
            {
                "period_type": "week_of_month",
                "period_value": int(wom),
                "avg_return_pct": float(group["return_pct"].mean()),
                "win_rate": float(group["is_win"].mean()),
                "count": int(len(group)),
            }
        )

    return pd.DataFrame(rows, columns=SEASONALITY_COLUMNS)


__all__ = [
    "SEASONALITY_COLUMNS",
    "seasonality_stats",
]

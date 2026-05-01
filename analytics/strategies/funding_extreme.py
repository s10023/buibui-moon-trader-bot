"""Detector: Funding Extreme — extracted from `analytics/indicators_lib.py` in strat-2.

No behaviour change. Function body byte-identical to pre-split source.
"""

import pandas as pd

from analytics.strategies._shared import _empty_signals, _signals_to_df


def detect_funding_extreme(
    ohlcv_df: pd.DataFrame,
    funding_df: pd.DataFrame,
    threshold: float = 0.0001,
) -> pd.DataFrame:
    """Detect extreme funding rate conditions as contrarian signals.

    Extreme positive funding (rate > threshold) → short signal.
    Extreme negative funding (rate < -threshold) → long signal.

    Default threshold is 0.0001 (0.01%) — Binance Futures caps standard funding
    at 0.01% per 8h period; use this floor to catch any above-average extreme.

    funding_df must have columns: funding_time (Unix ms), funding_rate (float).
    Funding data is joined to OHLCV by the nearest prior funding_time.
    """
    if ohlcv_df.empty or funding_df.empty:
        return _empty_signals()

    ohlcv_sorted = ohlcv_df.sort_values("open_time").reset_index(drop=True)
    funding_sorted = funding_df.sort_values("funding_time").reset_index(drop=True)

    left = ohlcv_sorted[["open_time"]].rename(columns={"open_time": "ts"})

    # Keep funding_time as a separate column so we can deduplicate by it.
    right_with_ft = funding_sorted[["funding_time", "funding_rate"]].copy()
    right_with_ft["ts"] = right_with_ft["funding_time"]

    merged = pd.merge_asof(left, right_with_ft, on="ts", direction="backward")
    merged["open_time"] = ohlcv_sorted["open_time"].values
    merged["rate"] = pd.to_numeric(merged["funding_rate"], errors="coerce")

    # Only emit on the FIRST candle after each unique funding_time (one signal per period).
    valid = (
        merged[merged["rate"].notna()]
        .drop_duplicates(subset=["funding_time"])
        .reset_index(drop=True)
    )

    signals: list[dict[str, object]] = []

    for _, row in valid.iterrows():
        rate = float(row["rate"])
        open_time = int(row["open_time"])

        if rate > threshold:
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "short",
                    "reason": f"funding_long_extreme@{rate:.4f}",
                    "sl_price": 0.0,
                    "context": "",
                }
            )
        elif rate < -threshold:
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "long",
                    "reason": f"funding_short_extreme@{rate:.4f}",
                    "sl_price": 0.0,
                    "context": "",
                }
            )

    return _signals_to_df(signals)

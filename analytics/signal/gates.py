"""Signal gates: ADR consumption filter and per-strategy ADR exemption."""

import pandas as pd

from analytics.signal_config import StrategyOverride


def _filter_signals_by_adr(
    ohlcv_df: pd.DataFrame,
    signals_df: pd.DataFrame,
    threshold: float,
) -> pd.DataFrame:
    """Return signals where the ADR consumed at signal time is below threshold.

    For each signal candle, computes:
      consumed_ratio = (cumulative intraday range up to that candle) / (14-day ADR)

    Signals where consumed_ratio >= threshold are dropped — the daily move was
    already mostly done when the signal fired.  Signals whose candle is not found
    in ohlcv_df pass through untouched (safe-default: don't suppress unknown data).
    """
    if signals_df.empty or ohlcv_df.empty:
        return signals_df

    df = ohlcv_df.copy()
    df["_date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.date
    df = df.sort_values("open_time")

    # Day open = first candle's open price within each calendar day
    day_opens: pd.Series = df.groupby("_date")["open"].first()

    # Cumulative intraday high/low up to each candle (inclusive)
    df["_cum_high"] = df.groupby("_date")["high"].cummax()
    df["_cum_low"] = df.groupby("_date")["low"].cummin()
    df["_day_open"] = df["_date"].map(day_opens)

    # Today's range as a fraction of day_open (avoid div/0)
    df["_today_range"] = (df["_cum_high"] - df["_cum_low"]) / df["_day_open"].where(
        df["_day_open"] > 0
    )

    # 14-day rolling ADR from daily extremes
    daily = (
        df.groupby("_date")
        .agg(_dh=("high", "max"), _dl=("low", "min"), _do=("open", "first"))
        .sort_index()
    )
    daily["_dr"] = (daily["_dh"] - daily["_dl"]) / daily["_do"].where(daily["_do"] > 0)
    daily["_adr14"] = daily["_dr"].rolling(14, min_periods=1).mean()

    df["_adr14"] = df["_date"].map(daily["_adr14"])

    # Consumed ratio at each candle; NaN when adr_14 is zero or unknown
    df["_consumed"] = df["_today_range"] / df["_adr14"].where(df["_adr14"] > 0)

    # Direction: close in upper half of today's range → move was upward
    df["_mid"] = (df["_cum_high"] + df["_cum_low"]) / 2
    df["_move_up"] = (df["close"] > df["_mid"]).astype(float)

    consumed_map: dict[int, float] = dict(
        zip(df["open_time"].astype(int), df["_consumed"].astype(float), strict=False)
    )
    move_up_map: dict[int, float] = dict(
        zip(df["open_time"].astype(int), df["_move_up"], strict=False)
    )

    signal_ratios = signals_df["open_time"].astype(int).map(consumed_map)
    signal_move_up = signals_df["open_time"].astype(int).map(move_up_map)

    # Suppress only the chasing direction: LONGs when move was up, SHORTs when down.
    # NaN move_up (candle not found) → neither condition fires → safe pass-through.
    chasing = ((signal_move_up == 1.0) & (signals_df["direction"] == "long")) | (
        (signal_move_up == 0.0) & (signals_df["direction"] == "short")
    )
    keep = signal_ratios.isna() | (signal_ratios < threshold) | ~chasing
    return signals_df[keep].reset_index(drop=True)


def _is_adr_exempt(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
) -> bool:
    if not strategy_params:
        return False
    override = strategy_params.get(strategy)
    return override.adr_exempt if override is not None else False

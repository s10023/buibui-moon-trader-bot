"""Volume + day-of-week gate helpers — self-contained (no sibling imports)."""

import pandas as pd


def _is_low_volume(
    ohlcv: pd.DataFrame,
    idx: int,
    multiplier: float = 1.5,
    lookback: int = 20,
) -> bool:
    """Return True if the candle at idx has volume below multiplier × rolling mean.

    Uses the lookback candles *before* idx (no lookahead). Returns False when
    volume data is unavailable (safe default — no false suppression).
    """
    if "volume" not in ohlcv.columns or idx < 1:
        return False
    start = max(0, idx - lookback)
    prior_vols = ohlcv["volume"].iloc[start:idx].astype(float)
    if prior_vols.empty:
        return False
    avg = float(prior_vols.mean())
    if avg == 0.0:
        return False
    return float(ohlcv["volume"].iloc[idx]) < multiplier * avg


def _is_volume_spike(
    ohlcv: pd.DataFrame,
    idx: int,
    multiplier: float = 3.0,
    lookback: int = 20,
) -> bool:
    """Return True if the candle at idx has volume above multiplier × rolling mean.

    Uses the lookback candles *before* idx (no lookahead). Returns False when
    volume data is unavailable (safe default — no false boost).
    """
    if "volume" not in ohlcv.columns or idx < 1:
        return False
    start = max(0, idx - lookback)
    prior_vols = ohlcv["volume"].iloc[start:idx].astype(float)
    if prior_vols.empty:
        return False
    avg = float(prior_vols.mean())
    if avg == 0.0:
        return False
    return float(ohlcv["volume"].iloc[idx]) > multiplier * avg


def filter_signals_by_day(
    signals: pd.DataFrame, allowed_weekdays: list[int] | None = None
) -> pd.DataFrame:
    """Filter signals to only those whose open_time falls on allowed weekdays (UTC).

    allowed_weekdays: list of Python weekday ints (Mon=0 … Sun=6).
    None means no filter (all days pass).
    """
    if signals.empty or allowed_weekdays is None:
        return signals
    weekdays = pd.to_datetime(signals["open_time"], unit="ms", utc=True).dt.weekday
    return signals[weekdays.isin(allowed_weekdays)].reset_index(drop=True)

"""Pure indicator math: anchored VWAP, Bollinger read, ER / PA character.

M1 primitives (spec docs/superpowers/specs/2026-07-09-m1-indicator-state-design.md).
No DB, no brief imports — reusable outside the brief (F2, research). All
thresholds are a-priori display constants, never fitted.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class BollingerRead:
    pct_b: float
    bandwidth: float
    bw_pctile: float | None
    squeeze: bool | None


@dataclass(frozen=True)
class PaRead:
    label: str  # impulse_up | impulse_down | grind_up | grind_down | chop
    er: float
    speed_atr: float


def anchored_vwap(hourly_df: pd.DataFrame, anchor_ms: int) -> float | None:
    """Volume-weighted typical price over bars with open_time >= anchor_ms."""
    if hourly_df.empty:
        return None
    window = hourly_df[hourly_df["open_time"] >= anchor_ms]
    if window.empty:
        return None
    vol = window["volume"].astype(float)
    total = float(vol.sum())
    if total <= 0.0:
        return None
    typical = (
        window["high"].astype(float)
        + window["low"].astype(float)
        + window["close"].astype(float)
    ) / 3.0
    return float((typical * vol).sum() / total)


def bollinger_state(
    close: pd.Series,
    ref_price: float,
    period: int = 20,
    k: float = 2.0,
    pctile_window: int = 180,
    pctile_min: int = 60,
) -> BollingerRead | None:
    """%B of ref_price + bandwidth + bandwidth percentile vs trailing window.

    Population std (ddof=0), the trading-platform BB convention. Percentile =
    fraction of the trailing ``pctile_window`` bandwidth values (including the
    current one) that are <= current; None under ``pctile_min`` valid values.
    """
    series = close.astype(float)
    if len(series) < period:
        return None
    mid = series.rolling(period).mean()
    sd = series.rolling(period).std(ddof=0)
    middle = float(mid.iloc[-1])
    dev = float(sd.iloc[-1])
    if dev <= 0.0 or middle == 0.0:
        return None
    upper = middle + k * dev
    lower = middle - k * dev
    pct_b = (ref_price - lower) / (upper - lower)
    bw_series = (2.0 * k * sd / mid).dropna()
    bandwidth = float(bw_series.iloc[-1])
    tail = bw_series.tail(pctile_window)
    if len(tail) < pctile_min:
        return BollingerRead(
            pct_b=float(pct_b), bandwidth=bandwidth, bw_pctile=None, squeeze=None
        )
    pctile = float((tail <= bandwidth).mean())
    return BollingerRead(
        pct_b=float(pct_b),
        bandwidth=bandwidth,
        bw_pctile=pctile,
        squeeze=pctile <= 0.10,
    )


def efficiency_ratio(close: pd.Series, n: int) -> float:
    """Kaufman ER over the last ``n`` steps; 0.0 when undefined."""
    series = close.astype(float)
    if len(series) < n + 1:
        return 0.0
    window = series.iloc[-(n + 1) :]
    gross = float(window.diff().abs().sum())
    if gross <= 0.0:
        return 0.0
    net = abs(float(window.iloc[-1]) - float(window.iloc[0]))
    return net / gross


def pa_character(
    close: pd.Series,
    atr14: float,
    n: int = 10,
    er_threshold: float = 0.40,
    speed_threshold: float = 0.8,
) -> PaRead | None:
    """Impulse/grind/chop label from ER x ATR-normalised speed (spec §5)."""
    series = close.astype(float)
    if len(series) < n + 1 or atr14 <= 0.0:
        return None
    window = series.iloc[-(n + 1) :]
    er = efficiency_ratio(series, n)
    speed = float(window.diff().abs().mean()) / atr14
    if er < er_threshold:
        label = "chop"
    else:
        direction = "up" if float(window.iloc[-1]) >= float(window.iloc[0]) else "down"
        kind = "impulse" if speed >= speed_threshold else "grind"
        label = f"{kind}_{direction}"
    return PaRead(label=label, er=er, speed_atr=speed)

"""Per-symbol indicator states for the brief panel (M1 adapter).

Thin adapter over the pure primitives (analytics/indicators.py,
analytics/volume_profile.py) + existing helpers (compute_ema,
reference_levels, regime series). Every sub-block computes independently:
one failure -> that sub-block None + an UNPREFIXED note (the bundle adds
the symbol); only all sub-blocks None collapses the whole state to None.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import pandas as pd

from analytics.brief._common import day_ahead_dow
from analytics.brief.types import (
    BbState,
    CandleHit,
    EmaState,
    IndicatorState,
    MondayState,
    PaState,
    ProfileState,
    RangeState,
    VwapState,
)
from analytics.reference_levels import compute_levels
from analytics.strategies._shared import compute_ema

logger = logging.getLogger(__name__)

_EMA_SPANS = (20, 50, 200)
_SLOPE_LOOKBACK = 5


def _ema_value(close: pd.Series, span: int) -> pd.Series | None:
    """EMA series when history covers the span, else None (spec §1)."""
    if len(close) < span:
        return None
    return compute_ema(close, span)


def _ema_state(completed_1d: pd.DataFrame, ref_close: float) -> EmaState | None:
    if completed_1d.empty:
        return None
    close = completed_1d["close"].astype(float)
    series = {span: _ema_value(close, span) for span in _EMA_SPANS}
    values = {
        span: (float(s.iloc[-1]) if s is not None else None)
        for span, s in series.items()
    }
    above = {
        span: (ref_close > v if v is not None else None) for span, v in values.items()
    }
    e20, e50, e200 = values[20], values[50], values[200]
    stack: str | None = None
    if e20 is not None and e50 is not None and e200 is not None:
        if e20 > e50 > e200:
            stack = "bullish"
        elif e200 > e50 > e20:
            stack = "bearish"
        else:
            stack = "mixed"
    slope: str | None = None
    s200 = series[200]
    if s200 is not None and len(s200) > _SLOPE_LOOKBACK:
        slope = (
            "rising"
            if float(s200.iloc[-1]) > float(s200.iloc[-1 - _SLOPE_LOOKBACK])
            else "falling"
        )
    return EmaState(
        above_20=above[20],
        above_50=above[50],
        above_200=above[200],
        stack=stack,
        slope_200=slope,
    )


def _range_state(
    completed_1d: pd.DataFrame, regime_series_1d: pd.Series, ref_close: float
) -> RangeState | None:
    if regime_series_1d.empty or completed_1d.empty:
        return None
    if len(regime_series_1d) != len(completed_1d):
        return None
    labels = [str(v) for v in regime_series_1d.tolist()]
    label = labels[-1]
    bars = 1
    for prev in reversed(labels[:-1]):
        if prev != label:
            break
        bars += 1
    since_ms = int(completed_1d["open_time"].iloc[len(completed_1d) - bars])
    if label != "range":
        return RangeState(
            label=label,
            since_ms=since_ms,
            bars=bars,
            range_low=None,
            range_high=None,
            pos=None,
        )
    window = completed_1d.iloc[-bars:]
    low = float(window["low"].astype(float).min())
    high = float(window["high"].astype(float).max())
    pos: float | None = None
    if high > low:
        pos = min(max((ref_close - low) / (high - low), 0.0), 1.0)
    return RangeState(
        label=label,
        since_ms=since_ms,
        bars=bars,
        range_low=low,
        range_high=high,
        pos=pos,
    )


def _monday_state(
    completed_1d: pd.DataFrame, ref_close: float, as_of_ms: int
) -> MondayState | None:
    levels = compute_levels(completed_1d, as_of_ms)
    mon_high = levels.get("MonH")
    mon_low = levels.get("MonL")
    if mon_high is None or mon_low is None:
        if day_ahead_dow(as_of_ms) == "Mon":
            return MondayState(state="forming", pos=None)
        return None
    if ref_close > mon_high:
        return MondayState(state="above", pos=None)
    if ref_close < mon_low:
        return MondayState(state="below", pos=None)
    pos: float | None = None
    if mon_high > mon_low:
        pos = (ref_close - mon_low) / (mon_high - mon_low)
    return MondayState(state="inside", pos=pos)


def _candle_hits(completed_1d: pd.DataFrame) -> list[CandleHit]:
    raise NotImplementedError  # Task 4


def _pa_state(completed_1d: pd.DataFrame, atr14: float) -> PaState | None:
    raise NotImplementedError  # Task 4


def _bb_state(completed_1d: pd.DataFrame, ref_close: float) -> BbState | None:
    raise NotImplementedError  # Task 5


def _vwap_state(
    completed_1h: pd.DataFrame, ref_close: float, atr14: float, as_of_ms: int
) -> VwapState | None:
    raise NotImplementedError  # Task 5


def _profile_state(
    completed_1h: pd.DataFrame, ref_close: float, atr14: float, as_of_ms: int
) -> ProfileState | None:
    raise NotImplementedError  # Task 5


def build_indicator_state(
    completed_1d: pd.DataFrame,
    completed_1h: pd.DataFrame,
    regime_series_1d: pd.Series,
    ref_close: float,
    atr14: float,
    as_of_ms: int,
) -> tuple[IndicatorState | None, list[str]]:
    """(IndicatorState | None, notes) — independent sub-blocks (spec).

    Notes are unprefixed ("indicator ema failed (...)"); the bundle adds
    the symbol. NotImplementedError from a not-yet-built sub-block (staged
    Tasks 4-5) is treated as "absent", not "failed" — no note.
    """
    notes: list[str] = []

    def run(name: str, fn: Callable[[], object]) -> object:
        try:
            return fn()
        except NotImplementedError:
            return None
        except Exception as exc:  # independence contract
            logger.warning("brief indicators: %s failed: %s", name, exc)
            notes.append(f"indicator {name} failed ({exc})")
            return None

    ema = run("ema", lambda: _ema_state(completed_1d, ref_close))
    range_state = run(
        "range", lambda: _range_state(completed_1d, regime_series_1d, ref_close)
    )
    monday = run("monday", lambda: _monday_state(completed_1d, ref_close, as_of_ms))
    candles = run("candle", lambda: _candle_hits(completed_1d))
    pa = run("pa", lambda: _pa_state(completed_1d, atr14))
    bb = run("bb", lambda: _bb_state(completed_1d, ref_close))
    vwap = run("vwap", lambda: _vwap_state(completed_1h, ref_close, atr14, as_of_ms))
    profile = run(
        "profile", lambda: _profile_state(completed_1h, ref_close, atr14, as_of_ms)
    )
    state = IndicatorState(
        ema=ema if isinstance(ema, EmaState) else None,
        range_state=range_state if isinstance(range_state, RangeState) else None,
        monday=monday if isinstance(monday, MondayState) else None,
        candles=candles if isinstance(candles, list) else None,
        pa=pa if isinstance(pa, PaState) else None,
        bb=bb if isinstance(bb, BbState) else None,
        vwap=vwap if isinstance(vwap, VwapState) else None,
        profile=profile if isinstance(profile, ProfileState) else None,
    )
    if all(
        v is None
        for v in (
            state.ema,
            state.range_state,
            state.monday,
            state.candles,
            state.pa,
            state.bb,
            state.vwap,
            state.profile,
        )
    ):
        return None, notes
    return state, notes

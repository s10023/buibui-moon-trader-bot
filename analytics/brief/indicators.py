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

from analytics.brief._common import DAY_MS, day_ahead_dow
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
from analytics.indicators import anchored_vwap, bollinger_state, pa_character
from analytics.reference_levels import compute_levels
from analytics.strategies._shared import compute_ema
from analytics.strategies.doji import detect_doji
from analytics.strategies.engulfing import detect_engulfing
from analytics.strategies.hammer_hanging_man import detect_hammer_hanging_man
from analytics.strategies.inside_bar import detect_inside_bar
from analytics.strategies.morning_evening_star import detect_morning_evening_star
from analytics.strategies.pin_bar import detect_pin_bar
from analytics.volume_profile import build_profile, value_area

logger = logging.getLogger(__name__)

_EMA_SPANS = (20, 50, 200)
_SLOPE_LOOKBACK = 5

# marubozu_retest deliberately excluded — it fires on the retest bar, not
# the pattern bar.
_CANDLE_DETECTORS: tuple[tuple[str, Callable[[pd.DataFrame], pd.DataFrame]], ...] = (
    ("doji", detect_doji),
    ("engulfing", detect_engulfing),
    ("hammer_hanging_man", detect_hammer_hanging_man),
    ("inside_bar", detect_inside_bar),
    ("morning_evening_star", detect_morning_evening_star),
    ("pin_bar", detect_pin_bar),
)

_PA_LOOKBACK = 10
_PROFILE_DAYS = 60


def _week_anchor_ms(as_of_ms: int) -> int:
    """Monday 00:00 UTC of the week containing as_of (reference_levels rule)."""
    ts = pd.Timestamp(as_of_ms, unit="ms", tz="UTC").normalize()
    monday = ts - pd.Timedelta(days=int(ts.weekday()))
    return int(monday.value // 1_000_000)


def _month_anchor_ms(as_of_ms: int) -> int:
    """First of the month, 00:00 UTC."""
    ts = pd.Timestamp(as_of_ms, unit="ms", tz="UTC").normalize().replace(day=1)
    return int(ts.value // 1_000_000)


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
    """Anatomy-detector signals landing on the LAST completed 1d bar."""
    if completed_1d.empty:
        return []
    last_open = int(completed_1d["open_time"].iloc[-1])
    hits: list[CandleHit] = []
    for name, detect in _CANDLE_DETECTORS:
        signals = detect(completed_1d)
        if signals.empty:
            continue
        on_last = signals[signals["open_time"] == last_open]
        for direction in on_last["direction"]:
            hits.append(CandleHit(pattern=name, direction=str(direction)))
    return sorted(hits, key=lambda h: (h.pattern, h.direction))


def _pa_state(completed_1d: pd.DataFrame, atr14: float) -> PaState | None:
    read = pa_character(
        completed_1d["close"].astype(float), atr14=atr14, n=_PA_LOOKBACK
    )
    if read is None:
        return None
    return PaState(label=read.label, er=read.er, speed_atr=read.speed_atr)


def _bb_state(completed_1d: pd.DataFrame, ref_close: float) -> BbState | None:
    if completed_1d.empty:
        return None
    read = bollinger_state(completed_1d["close"].astype(float), ref_price=ref_close)
    if read is None:
        return None
    return BbState(
        pct_b=read.pct_b,
        bandwidth=read.bandwidth,
        bw_pctile=read.bw_pctile,
        squeeze=read.squeeze,
    )


def _vwap_state(
    completed_1h: pd.DataFrame, ref_close: float, atr14: float, as_of_ms: int
) -> VwapState | None:
    if atr14 <= 0.0:
        return None
    weekly = anchored_vwap(completed_1h, _week_anchor_ms(as_of_ms))
    monthly = anchored_vwap(completed_1h, _month_anchor_ms(as_of_ms))
    if weekly is None and monthly is None:
        return None
    return VwapState(
        weekly_price=weekly,
        weekly_dist_atr=((ref_close - weekly) / atr14) if weekly is not None else None,
        monthly_price=monthly,
        monthly_dist_atr=(
            (ref_close - monthly) / atr14 if monthly is not None else None
        ),
    )


def _profile_state(
    completed_1h: pd.DataFrame, ref_close: float, atr14: float, as_of_ms: int
) -> ProfileState | None:
    if atr14 <= 0.0 or completed_1h.empty:
        return None
    window = completed_1h[
        completed_1h["open_time"] >= as_of_ms - _PROFILE_DAYS * DAY_MS
    ]
    profile = build_profile(window)
    if profile is None:
        return None
    poc, vah, val = value_area(profile)
    if ref_close > vah:
        vs_value = "above"
    elif ref_close < val:
        vs_value = "below"
    else:
        vs_value = "inside"
    return ProfileState(
        poc=poc,
        vah=vah,
        val=val,
        vs_value=vs_value,
        poc_dist_atr=(poc - ref_close) / atr14,
    )


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
    the symbol.
    """
    notes: list[str] = []

    def run(name: str, fn: Callable[[], object]) -> object:
        try:
            return fn()
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

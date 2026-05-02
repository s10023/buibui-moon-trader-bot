"""Tests for `detect_ema` (Variant A pullback continuation strategy).

Each test builds a hand-crafted OHLCV series long enough to satisfy the
`slow_period` warm-up and exercises one branch of the spec §3 logic.
"""

import pandas as pd

from analytics.strategies import SIGNAL_COLUMNS
from analytics.strategies.ema import detect_ema
from tests.conftest import _candle, _make_ohlcv

_MS_PER_HOUR = 3_600_000
_BASE_TIME = 1_700_000_000_000


def _bar(
    i: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
) -> dict[str, object]:
    return _candle(
        _BASE_TIME + i * _MS_PER_HOUR,
        open_,
        high,
        low,
        close,
        volume=volume,
    )


def _ramp_long_setup(
    n: int = 80,
    pullback_at: int = 75,
    trigger_at: int = 78,
) -> pd.DataFrame:
    """Build an uptrend with a wick-into-EMA pullback then a strong bullish trigger.

    Bars 0..n-1 march up by ~1.0 each so EMA20/50 climb steadily and slope > 0.3 %.
    `pullback_at` injects a deep wick that touches the (ascending) fast EMA from
    above and closes back above it. `trigger_at` plants a tall bullish marubozu
    a few bars later that satisfies §3.4.
    """
    rows: list[dict[str, object]] = []
    price = 100.0
    for i in range(n):
        if i == pullback_at:
            # Deep lower wick to ~ema_fast (which trails by ~10 on a 1-step ramp)
            o = price
            c = price + 0.5
            h = c + 0.2
            lo = price - 12.0  # well below fast EMA
            rows.append(_bar(i, o, h, lo, c))
        elif i == trigger_at:
            # Tall bullish body, full marubozu (no wicks)
            o = price
            c = price + 4.0
            rows.append(_bar(i, o, c, o, c))
        else:
            o = price
            c = price + 1.0
            h = c + 0.1
            lo = o - 0.1
            rows.append(_bar(i, o, h, lo, c))
        price += 1.0
    return _make_ohlcv(rows)


def _ramp_short_setup(
    n: int = 80,
    pullback_at: int = 75,
    trigger_at: int = 78,
) -> pd.DataFrame:
    """Mirror of the long setup: a clean downtrend with a wick-up pullback + bearish trigger."""
    rows: list[dict[str, object]] = []
    price = 200.0
    for i in range(n):
        if i == pullback_at:
            o = price
            c = price - 0.5
            lo = c - 0.2
            h = price + 12.0
            rows.append(_bar(i, o, h, lo, c))
        elif i == trigger_at:
            o = price
            c = price - 4.0
            rows.append(_bar(i, o, o, c, c))
        else:
            o = price
            c = price - 1.0
            lo = c - 0.1
            h = o + 0.1
            rows.append(_bar(i, o, h, lo, c))
        price -= 1.0
    return _make_ohlcv(rows)


class TestDetectEmaSchema:
    def test_returns_signal_columns(self) -> None:
        df = _ramp_long_setup()
        out = detect_ema(df)
        assert list(out.columns) == SIGNAL_COLUMNS

    def test_returns_empty_on_short_input(self) -> None:
        rows = [_bar(i, 100, 101, 99, 100) for i in range(10)]
        df = _make_ohlcv(rows)
        out = detect_ema(df)
        assert out.empty

    def test_returns_empty_when_fast_period_not_less_than_slow(self) -> None:
        df = _ramp_long_setup()
        assert detect_ema(df, fast_period=50, slow_period=20).empty


class TestDetectEmaTrendLong:
    def test_uptrend_pullback_fires_long(self) -> None:
        df = _ramp_long_setup()
        out = detect_ema(df)
        assert not out.empty
        assert (out["direction"] == "long").any()
        long_row = out[out["direction"] == "long"].iloc[0]
        # SL must be below entry; TP must be above entry; both numeric.
        sl = float(long_row["sl_price"])
        tp = float(long_row["tp_price"])
        assert sl < tp
        assert long_row["reason"].startswith("ema_pullback_long@")


class TestDetectEmaTrendShort:
    def test_downtrend_pullback_fires_short(self) -> None:
        df = _ramp_short_setup()
        out = detect_ema(df)
        assert not out.empty
        assert (out["direction"] == "short").any()
        short_row = out[out["direction"] == "short"].iloc[0]
        sl = float(short_row["sl_price"])
        tp = float(short_row["tp_price"])
        assert sl > tp  # for shorts SL is above entry, TP below
        assert short_row["reason"].startswith("ema_pullback_short@")


class TestDetectEmaSuppression:
    def test_range_chop_blocked_by_regime_gate(self) -> None:
        # Sawtooth: cross_count blows past max_crosses AND slope ~0.
        rows: list[dict[str, object]] = []
        for i in range(80):
            mid = 100.0 + (3.0 if i % 2 == 0 else -3.0)
            rows.append(_bar(i, mid, mid + 0.2, mid - 0.2, mid))
        df = _make_ohlcv(rows)
        assert detect_ema(df).empty

    def test_no_pullback_no_signal(self) -> None:
        # Smooth uptrend with no wick into fast EMA → pullback rule fails.
        rows: list[dict[str, object]] = []
        price = 100.0
        for i in range(80):
            o = price
            c = price + 1.0
            rows.append(_bar(i, o, c + 0.05, o - 0.05, c))
            price += 1.0
        df = _make_ohlcv(rows)
        out = detect_ema(df)
        # Either nothing fires, or any fire would still satisfy direction sanity.
        # The intent: with no deep wick the long-side pullback rule has nothing
        # to anchor SL on, so output is empty.
        assert out.empty

    def test_flat_slope_blocked(self) -> None:
        # Constant price → slope = 0 → trend filter fails before the gate runs.
        rows = [_bar(i, 100, 100.5, 99.5, 100) for i in range(80)]
        df = _make_ohlcv(rows)
        assert detect_ema(df).empty

    def test_small_body_trigger_rejected(self) -> None:
        # Same uptrend + pullback, but neuter the trigger candle into a tiny body
        # inside a wide range — body / range < 0.5.
        df = _ramp_long_setup()
        # Replace the trigger candle (idx 78) with a wide-range / small-body bar.
        i = 78
        ts = _BASE_TIME + i * _MS_PER_HOUR
        df.loc[df["open_time"] == ts, ["open", "high", "low", "close"]] = [
            178.0,
            190.0,
            170.0,
            178.5,
        ]
        out = detect_ema(df)
        # No long signal at the trigger bar; pullback wick is gone too so output
        # should not contain a long ROW with trigger time = ts.
        if not out.empty:
            assert (out["open_time"] != ts).all()

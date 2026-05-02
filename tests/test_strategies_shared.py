"""Tests for the shared strategy helpers in `analytics.strategies._shared`.

Covers the EMA-strategy helpers (`compute_ema`, `ema_cross_count`,
`is_trending`) added alongside `detect_ema`. The pre-existing helpers
(`volume_confirm`, `_find_bos_swing`) are exercised indirectly via the
per-detector tests in `tests/test_strategies.py`.
"""

import pandas as pd

from analytics.strategies._shared import (
    compute_ema,
    ema_cross_count,
    is_trending,
)


class TestComputeEma:
    def test_constant_series_returns_constant(self) -> None:
        s = pd.Series([100.0] * 50)
        ema = compute_ema(s, span=20)
        assert ema.iloc[-1] == 100.0
        assert (ema == 100.0).all()

    def test_first_value_equals_input(self) -> None:
        # adjust=False seeds the EMA with the first observation.
        s = pd.Series([10.0, 20.0, 30.0, 40.0])
        ema = compute_ema(s, span=3)
        assert ema.iloc[0] == 10.0

    def test_ema_lags_a_rising_series(self) -> None:
        s = pd.Series([float(i) for i in range(1, 51)])
        ema = compute_ema(s, span=20)
        # EMA of an arithmetic ramp must trail the input but climb monotonically.
        assert ema.iloc[-1] < s.iloc[-1]
        assert ema.is_monotonic_increasing

    def test_known_smoothing(self) -> None:
        # alpha = 2/(3+1) = 0.5; EMA recursion: e[t] = 0.5*x[t] + 0.5*e[t-1]
        s = pd.Series([1.0, 5.0, 9.0])
        ema = compute_ema(s, span=3)
        assert ema.iloc[0] == 1.0
        assert ema.iloc[1] == 3.0  # 0.5*5 + 0.5*1
        assert ema.iloc[2] == 6.0  # 0.5*9 + 0.5*3


class TestEmaCrossCount:
    def test_no_crosses_when_price_above_ema_throughout(self) -> None:
        close = pd.Series([110.0] * 30)
        ema = pd.Series([100.0] * 30)
        assert ema_cross_count(close, ema, idx=29, lookback=20) == 0

    def test_counts_each_sign_flip(self) -> None:
        # close - ema sequence: + - + - + → 4 sign flips
        close = pd.Series([105, 95, 105, 95, 105], dtype=float)
        ema = pd.Series([100.0] * 5)
        assert ema_cross_count(close, ema, idx=4, lookback=5) == 4

    def test_zero_diff_does_not_inflate_count(self) -> None:
        # Touch (diff = 0) should be ignored; effective sequence is + + - .
        close = pd.Series([105, 100, 105, 95], dtype=float)
        ema = pd.Series([100.0] * 4)
        assert ema_cross_count(close, ema, idx=3, lookback=4) == 1

    def test_lookback_window_clipped(self) -> None:
        # Only the last 3 bars are considered; earlier flips do not contribute.
        close = pd.Series([105, 95, 105, 105, 105], dtype=float)
        ema = pd.Series([100.0] * 5)
        assert ema_cross_count(close, ema, idx=4, lookback=3) == 0


class TestIsTrending:
    def _ramp(self, n: int, start: float = 100.0, step: float = 1.0) -> pd.Series:
        return pd.Series([start + step * i for i in range(n)])

    def test_strong_uptrend_passes(self) -> None:
        close = self._ramp(60)
        ema_fast = compute_ema(close, span=20)
        ema_slow = compute_ema(close, span=50)
        # Slope across last 10 bars on a 1.0/bar ramp on a ~150 base ≈ ~7 % > 0.3 %.
        assert is_trending(close, ema_fast, ema_slow, idx=59) is True

    def test_flat_market_blocked(self) -> None:
        close = pd.Series([100.0] * 60)
        ema_fast = compute_ema(close, span=20)
        ema_slow = compute_ema(close, span=50)
        assert is_trending(close, ema_fast, ema_slow, idx=59) is False

    def test_oscillating_market_blocked(self) -> None:
        # Saw-tooth around 100: slope stays ~0 AND crosses pile up.
        close = pd.Series([100.0 + (5.0 if i % 2 == 0 else -5.0) for i in range(60)])
        ema_fast = compute_ema(close, span=20)
        ema_slow = compute_ema(close, span=50)
        assert is_trending(close, ema_fast, ema_slow, idx=59) is False

    def test_returns_false_when_idx_too_small(self) -> None:
        close = self._ramp(15)
        ema_fast = compute_ema(close, span=20)
        ema_slow = compute_ema(close, span=50)
        assert is_trending(close, ema_fast, ema_slow, idx=5) is False

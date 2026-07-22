"""Unit tests for the ST9/H11 SL-horizon audit library."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analytics.sl_horizon import (
    BASELINE_ARM,
    DEFAULT_MULTIPLIERS,
    SLGridConfig,
    arm_label,
    atr_by_open_time,
    baseline_levels,
    counterfactual_levels,
)


def test_default_grid_is_the_a_priori_one() -> None:
    assert DEFAULT_MULTIPLIERS == (0.5, 1.0, 1.5, 2.0, 3.0)


def test_config_defaults_match_live_hold_horizons() -> None:
    cfg = SLGridConfig()
    assert cfg.max_hold_bars_by_tf["15m"] == 96
    assert cfg.max_hold_bars_by_tf["1h"] == 48
    assert cfg.max_hold_bars_by_tf["4h"] == 30
    assert cfg.max_hold_bars_by_tf["1d"] == 14
    assert cfg.baseline_pct == 0.02


def test_config_rejects_empty_grid() -> None:
    with pytest.raises(ValueError, match="multipliers"):
        SLGridConfig(multipliers=())


def test_config_rejects_non_positive_multiplier() -> None:
    with pytest.raises(ValueError, match="multipliers"):
        SLGridConfig(multipliers=(1.0, 0.0))


def test_arm_label_is_stable_and_distinct() -> None:
    # `:g` drops the trailing zero, so 1.0 -> "atr_1". Task 5's _k_from_arm
    # inverts this, and Task 4's fixtures use these exact strings.
    assert arm_label(1.0) == "atr_1"
    assert arm_label(0.5) == "atr_0.5"
    assert arm_label(2.0) == "atr_2"
    assert arm_label(1.0) != BASELINE_ARM


def test_baseline_levels_long_is_two_percent_below_entry() -> None:
    sl, tp = baseline_levels(100.0, "long", baseline_pct=0.02, tp_r=3.0)
    assert sl == pytest.approx(98.0)
    assert tp == pytest.approx(106.0)


def test_baseline_levels_short_mirrors_long() -> None:
    sl, tp = baseline_levels(100.0, "short", baseline_pct=0.02, tp_r=3.0)
    assert sl == pytest.approx(102.0)
    assert tp == pytest.approx(94.0)


def test_counterfactual_levels_scale_with_atr_and_k() -> None:
    sl, tp = counterfactual_levels(100.0, "long", atr=2.0, k=1.5, tp_r=3.0)
    assert sl == pytest.approx(97.0)  # 100 - 1.5 * 2.0
    assert tp == pytest.approx(109.0)  # 100 + 3.0 * 3.0


def test_counterfactual_tp_tracks_tp_r_times_sl_distance() -> None:
    for tp_r in (1.0, 2.5, 4.0):
        sl, tp = counterfactual_levels(100.0, "short", atr=1.0, k=2.0, tp_r=tp_r)
        sl_dist = abs(100.0 - sl)
        assert abs(100.0 - tp) == pytest.approx(tp_r * sl_dist)


def test_counterfactual_rejects_unknown_direction() -> None:
    with pytest.raises(ValueError, match="direction"):
        counterfactual_levels(100.0, "sideways", atr=1.0, k=1.0, tp_r=3.0)


def test_counterfactual_rejects_non_positive_risk() -> None:
    with pytest.raises(ValueError, match="sl_dist"):
        counterfactual_levels(100.0, "long", atr=0.0, k=1.0, tp_r=3.0)


def _ramp_ohlcv(n: int = 40, step: float = 1.0) -> pd.DataFrame:
    """Deterministic OHLCV with a constant 2.0-wide bar and a `step` drift."""
    open_times = [1_000 + i * 100 for i in range(n)]
    closes = [100.0 + i * step for i in range(n)]
    return pd.DataFrame(
        {
            "open_time": open_times,
            "open": [c - 0.5 for c in closes],
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [10.0] * n,
        }
    )


def _varying_range_ohlcv(n: int = 40, tr_step: float = 10.0) -> pd.DataFrame:
    """OHLCV whose true range grows every bar, so ATR14 is index-sensitive.

    Close is held constant so the high/low-vs-prev-close legs of the true-range
    formula are always exactly half the high-low leg, which means true range at
    bar i (i >= 1) is exactly ``i * tr_step`` with no ambiguity. Unlike
    `_ramp_ohlcv` (constant 2.0 true range -> constant ATR14 everywhere, so an
    off-by-one lookup would go undetected), this fixture makes ATR14 strictly
    increasing with idx: adjacent indices differ by a fixed, large step.
    """
    open_times = [1_000 + i * 100 for i in range(n)]
    close = 100.0
    widths = [i * tr_step for i in range(n)]
    return pd.DataFrame(
        {
            "open_time": open_times,
            "open": [close] * n,
            "high": [close + w / 2.0 for w in widths],
            "low": [close - w / 2.0 for w in widths],
            "close": [close] * n,
            "volume": [10.0] * n,
        }
    )


def test_atr_by_open_time_returns_a_value_per_known_bar() -> None:
    df = _ramp_ohlcv()
    got = atr_by_open_time(df, [1_000 + 20 * 100, 1_000 + 30 * 100])
    assert set(got) == {3_000, 4_000}
    assert all(v is not None and v > 0.0 for v in got.values())


def test_atr_by_open_time_is_none_for_unknown_open_time() -> None:
    df = _ramp_ohlcv()
    assert atr_by_open_time(df, [999_999]) == {999_999: None}


def test_atr_by_open_time_is_none_at_the_first_bar() -> None:
    # _compute_atr14 needs a prior close, so idx 0 has no ATR.
    df = _ramp_ohlcv()
    assert atr_by_open_time(df, [1_000]) == {1_000: None}


def test_atr_by_open_time_matches_the_engine_primitive() -> None:
    from analytics.backtest.engine import _compute_atr14

    df = _ramp_ohlcv()
    idx = 25
    expected = _compute_atr14(
        df["high"].to_numpy(dtype=np.float64),
        df["low"].to_numpy(dtype=np.float64),
        df["close"].to_numpy(dtype=np.float64),
        idx,
    )
    got = atr_by_open_time(df, [int(df["open_time"].iloc[idx])])
    assert got[int(df["open_time"].iloc[idx])] == pytest.approx(expected)


def test_atr_by_open_time_is_index_sensitive() -> None:
    """`_ramp_ohlcv`'s constant true range means every index yields the same
    ATR14, so a silent off-by-one in the open_time -> position lookup would
    still pass `test_atr_by_open_time_matches_the_engine_primitive` above (it
    would just quietly match the wrong row). This fixture's true range grows
    every bar, so neighbouring indices give measurably different ATR14 —
    proving the lookup actually resolves to the right row, not a nearby one.
    """
    from analytics.backtest.engine import _compute_atr14

    df = _varying_range_ohlcv()
    idx = 25
    open_times = [int(df["open_time"].iloc[i]) for i in (idx - 1, idx, idx + 1)]
    got = atr_by_open_time(df, open_times)

    highs = df["high"].to_numpy(dtype=np.float64)
    lows = df["low"].to_numpy(dtype=np.float64)
    closes = df["close"].to_numpy(dtype=np.float64)
    expected = {
        ot: _compute_atr14(highs, lows, closes, i)
        for ot, i in zip(open_times, (idx - 1, idx, idx + 1), strict=True)
    }

    for ot in open_times:
        assert got[ot] == pytest.approx(expected[ot])

    before, at, after = (got[ot] for ot in open_times)
    assert before is not None and at is not None and after is not None
    assert abs(at - before) > 1.0
    assert abs(at - after) > 1.0


def test_atr_by_open_time_handles_empty_frame() -> None:
    empty = pd.DataFrame(
        columns=["open_time", "open", "high", "low", "close", "volume"]
    )
    assert atr_by_open_time(empty, [1_000]) == {1_000: None}

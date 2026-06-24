"""Tests for analytics.reference_levels (look-ahead-safe calendar levels)."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from analytics.reference_levels import (
    LEVEL_NAMES,
    compute_levels,
    compute_levels_table,
    nearest_level,
    sweep_flag,
)


def _day_ms(date_str: str) -> int:
    return int(pd.Timestamp(date_str, tz="UTC").timestamp() * 1000)


def _daily_df() -> pd.DataFrame:
    """June 2024 daily candles; OHLC encode the day for unambiguous assertions.

    For day d: open=100+d, high=105+d, low=95+d, close=101+d.
    2024-06-01 is a Saturday; ISO weeks: 06-03..06-09 (Mon..Sun),
    06-10..06-16 (Mon..Sun).
    """
    rows = []
    for d in range(1, 13):  # 2024-06-01 .. 2024-06-12
        rows.append(
            {
                "symbol": "BTCUSDT",
                "timeframe": "1d",
                "open_time": _day_ms(f"2024-06-{d:02d}"),
                "open": float(100 + d),
                "high": float(105 + d),
                "low": float(95 + d),
                "close": float(101 + d),
                "volume": 1000.0,
                "taker_buy_volume": 500.0,
            }
        )
    return pd.DataFrame(rows)


def test_compute_levels_midweek_entry() -> None:
    # Entry Wednesday 2024-06-12 12:00 UTC.
    df = _daily_df()
    entry = int(pd.Timestamp("2024-06-12 12:00", tz="UTC").timestamp() * 1000)
    lv = compute_levels(df, entry)
    assert lv["DO"] == 112  # 06-12 open
    assert lv["PDH"] == 116  # 06-11 high
    assert lv["PDL"] == 106  # 06-11 low
    assert lv["WO"] == 110  # 06-10 (Monday) open
    assert lv["MonH"] == 115  # 06-10 high (Monday completed)
    assert lv["MonL"] == 105  # 06-10 low
    assert lv["MO"] == 101  # 06-01 open
    assert lv["PWH"] == 114  # max high 06-03..06-09
    assert lv["PWL"] == 98  # min low 06-03..06-09


def test_monday_entry_excludes_monday_range() -> None:
    # Entry on Monday 2024-06-10 — this week's Monday range is still forming.
    df = _daily_df()
    entry = int(pd.Timestamp("2024-06-10 09:00", tz="UTC").timestamp() * 1000)
    lv = compute_levels(df, entry)
    assert lv["MonH"] is None
    assert lv["MonL"] is None
    # Opens + previous-period levels still resolve.
    assert lv["WO"] == 110  # Monday open known at Monday start
    assert lv["DO"] == 110
    assert lv["PDH"] == 114  # 06-09 (Sunday) high
    assert lv["PDL"] == 104  # 06-09 low


def test_current_day_high_low_never_leak() -> None:
    # The entry day's high/low must never appear in any level (no look-ahead).
    df = _daily_df()
    mask = df["open_time"] == _day_ms("2024-06-12")
    df.loc[mask, "high"] = 9999.0
    df.loc[mask, "low"] = 0.01
    entry = int(pd.Timestamp("2024-06-12 18:00", tz="UTC").timestamp() * 1000)
    lv = compute_levels(df, entry)
    present = [v for v in lv.values() if v is not None]
    assert 9999.0 not in present
    assert 0.01 not in present
    assert lv["PDH"] == 116  # yesterday's, not today's 9999
    assert lv["DO"] == 112  # today's OPEN is fixed at day start — fine


def test_compute_levels_empty_df() -> None:
    lv = compute_levels(pd.DataFrame(), _day_ms("2024-06-12"))
    assert all(v is None for v in lv.values())


def test_levels_table_matches_scalar() -> None:
    # The vectorized per-day table must equal the scalar compute_levels row-for-row.
    df = _daily_df()
    table = compute_levels_table(df)
    for date_str in ("2024-06-01", "2024-06-05", "2024-06-10", "2024-06-12"):
        entry = int(pd.Timestamp(f"{date_str} 12:00", tz="UTC").timestamp() * 1000)
        scalar = compute_levels(df, entry)
        row = table.loc[pd.Timestamp(date_str, tz="UTC")]
        for name in LEVEL_NAMES:
            s = scalar[name]
            t = row[name]
            if s is None:
                assert pd.isna(t), f"{date_str} {name}: scalar None vs table {t}"
            else:
                assert t == pytest.approx(s), (
                    f"{date_str} {name}: table {t} vs scalar {s}"
                )


def test_levels_table_empty_df() -> None:
    table = compute_levels_table(pd.DataFrame())
    assert list(table.columns) == list(LEVEL_NAMES)
    assert table.empty


def test_nearest_level_picks_closest() -> None:
    levels = {"PDL": 106.0, "PDH": 116.0, "DO": 112.0, "MonH": None}
    name, dist = nearest_level(111.5, levels)
    assert name == "DO"
    assert dist == pytest.approx(0.5)


def test_nearest_level_all_none() -> None:
    name, dist = nearest_level(100.0, {"PDL": None, "PDH": None})
    assert name == ""
    assert math.isinf(dist)


def test_sweep_flag_long_reclaim_true() -> None:
    # level=100: a recent bar wicks below, entry candle closes back above.
    df = pd.DataFrame(
        [
            {"low": 100.5, "high": 102, "close": 101},
            {"low": 98.0, "high": 101, "close": 99.5},  # wick below
            {"low": 99.0, "high": 102, "close": 101.0},  # entry: reclaim
        ]
    )
    assert sweep_flag(df, 2, 100.0, "long", lookback=3) is True


def test_sweep_flag_long_no_reclaim_false() -> None:
    df = pd.DataFrame(
        [
            {"low": 98.0, "high": 101, "close": 99.5},  # wick below
            {"low": 99.0, "high": 99.8, "close": 99.0},  # entry: still below
        ]
    )
    assert sweep_flag(df, 1, 100.0, "long", lookback=3) is False


def test_sweep_flag_short_reject_true() -> None:
    df = pd.DataFrame(
        [
            {"low": 98, "high": 102.0, "close": 100.5},  # wick above
            {"low": 97, "high": 100.2, "close": 99.0},  # entry: reject below
        ]
    )
    assert sweep_flag(df, 1, 100.0, "short", lookback=3) is True


def test_sweep_flag_lookback_excludes_old_wick() -> None:
    # The only wick-below is at idx 0; lookback=2 from idx 3 excludes it.
    df = pd.DataFrame(
        [
            {"low": 98.0, "high": 101, "close": 99.5},  # idx 0: old wick
            {"low": 100.5, "high": 102, "close": 101},
            {"low": 100.6, "high": 102, "close": 101},
            {"low": 100.7, "high": 103, "close": 102},  # idx 3: entry
        ]
    )
    assert sweep_flag(df, 3, 100.0, "long", lookback=2) is False


def test_sweep_flag_ignores_future_bars() -> None:
    # A future bar wicks below, but it must not count for an idx-0 entry.
    df = pd.DataFrame(
        [
            {"low": 100.5, "high": 102, "close": 101},  # idx 0: entry, no wick
            {"low": 98.0, "high": 101, "close": 99.5},  # idx 1: future wick
        ]
    )
    assert sweep_flag(df, 0, 100.0, "long", lookback=3) is False

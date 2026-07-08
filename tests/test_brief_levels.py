"""Level gauge: ATR, ADR, distance/side split, sweep flags."""

import pandas as pd

from analytics.brief._common import DAY_MS
from analytics.brief.levels import (
    _swept_current_period,
    _window_start_ms,
    adr_pct_14,
    atr14_wilder,
    build_level_rows,
)

AS_OF = 1_704_067_200_000 + 30 * DAY_MS  # 2024-01-31 00:00 UTC (Wednesday)
AS_OF_MID = AS_OF + 12 * 3_600_000  # 2024-01-31 12:00 UTC
MON_OPEN = AS_OF - 2 * DAY_MS  # Monday 2024-01-29 00:00 UTC
TUE_OPEN = AS_OF - 1 * DAY_MS


def _daily(n: int, base: float = 100.0) -> pd.DataFrame:
    start = AS_OF - n * DAY_MS
    rows = []
    for i in range(n):
        rows.append(
            {
                "open_time": start + i * DAY_MS,
                "open": base,
                "high": base + 2.0,
                "low": base - 2.0,
                "close": base + 1.0,
            }
        )
    return pd.DataFrame(rows)


def test_atr14_wilder_flat_series() -> None:
    df = _daily(30)
    atr = atr14_wilder(df)
    assert 3.9 < atr <= 4.0  # TR is constant 4.0 (high-low dominates)


def test_atr14_insufficient_rows() -> None:
    assert atr14_wilder(_daily(1)) == 0.0


def test_adr_pct_14() -> None:
    adr = adr_pct_14(_daily(30))
    assert adr is not None
    assert abs(adr - 0.04) < 1e-9  # (102-98)/100


def test_build_level_rows_sides_and_cap() -> None:
    df = _daily(30)
    above, below = build_level_rows(
        daily_df=df,
        as_of_ms=AS_OF,
        ref_price=101.0,
        atr14=4.0,
        max_per_side=4,
    )
    assert len(above) <= 4 and len(below) <= 4
    assert [r.dist_atr for r in above] == sorted(r.dist_atr for r in above)
    dists_below = [r.dist_atr for r in below]
    assert dists_below == sorted(dists_below, reverse=True)
    assert any(r.name == "PDH" for r in above)
    for r in above:
        assert r.dist_atr > 0
    for r in below:
        assert r.dist_atr <= 0


def test_build_level_rows_zero_atr_returns_empty() -> None:
    df = _daily(30)
    above, below = build_level_rows(df, AS_OF, 101.0, 0.0, 4)
    assert above == [] and below == []


def _with_bar(
    df: pd.DataFrame, open_time: int, high: float, low: float, close: float
) -> pd.DataFrame:
    row = {
        "open_time": open_time,
        "open": float(df.iloc[-1]["close"]),
        "high": high,
        "low": low,
        "close": close,
    }
    return pd.concat([df, pd.DataFrame([row])], ignore_index=True)


def _downtrend_frame() -> pd.DataFrame:
    """27 flat bars then 3 completed lower-high days: 110, 108, 106 (=PDH)."""
    df = _daily(30)
    df.loc[len(df) - 3, "high"] = 110.0
    df.loc[len(df) - 2, "high"] = 108.0
    df.loc[len(df) - 1, "high"] = 106.0
    return df


def test_window_start_ms() -> None:
    assert _window_start_ms(AS_OF_MID, "day") == AS_OF
    assert _window_start_ms(AS_OF_MID, "week") == MON_OPEN
    assert _window_start_ms(AS_OF_MID, "week_after_monday") == TUE_OPEN


def test_pdh_not_swept_in_lower_high_downtrend() -> None:
    """Regression: the shipped bug flagged PDH swept on any 3-day lower-high run."""
    df = _with_bar(_downtrend_frame(), AS_OF, high=105.0, low=100.0, close=104.0)
    assert _swept_current_period(df, AS_OF_MID, "PDH", 106.0, 104.0) is False


def test_pdh_swept_pierce_and_reclaim_today() -> None:
    df = _with_bar(_downtrend_frame(), AS_OF, high=107.0, low=100.0, close=105.0)
    assert _swept_current_period(df, AS_OF_MID, "PDH", 106.0, 105.0) is True


def test_pdh_breakout_in_progress_not_swept() -> None:
    df = _with_bar(_downtrend_frame(), AS_OF, high=107.0, low=100.0, close=106.5)
    assert _swept_current_period(df, AS_OF_MID, "PDH", 106.0, 106.5) is False


def test_pdl_mirror_long_sweep() -> None:
    df = _with_bar(_daily(30), AS_OF, high=100.0, low=97.0, close=99.0)
    assert _swept_current_period(df, AS_OF_MID, "PDL", 98.0, 99.0) is True
    assert _swept_current_period(df, AS_OF_MID, "PDL", 96.0, 99.0) is False


def _week_frame(
    mon_high: float, tue_high: float, wed_high: float, wed_close: float
) -> pd.DataFrame:
    """History ending Sunday + explicit Mon/Tue/forming-Wed bars of this week.

    ``_daily(n)`` always ends at AS_OF - 1d, so truncate two rows to end on
    Sunday before appending this week's bars (no duplicate open_times).
    """
    df = _daily(30).iloc[:-2].reset_index(drop=True)  # ends Sunday (AS_OF - 3d)
    df = _with_bar(df, MON_OPEN, high=mon_high, low=99.0, close=110.0)
    df = _with_bar(df, TUE_OPEN, high=tue_high, low=105.0, close=112.0)
    return _with_bar(df, AS_OF, high=wed_high, low=110.0, close=wed_close)


def test_monh_excludes_defining_monday_bar() -> None:
    # Monday high 120 defines MonH; nothing after Monday pierces 120.
    df = _week_frame(mon_high=120.0, tue_high=118.0, wed_high=119.0, wed_close=117.0)
    assert _swept_current_period(df, AS_OF_MID, "MonH", 120.0, 117.0) is False


def test_monh_swept_by_post_monday_pierce() -> None:
    df = _week_frame(mon_high=120.0, tue_high=121.0, wed_high=118.0, wed_close=117.0)
    assert _swept_current_period(df, AS_OF_MID, "MonH", 120.0, 117.0) is True


def test_pwh_window_is_this_week_only() -> None:
    # PWH := 115 (defined last week); this week's max high 114 → no sweep...
    no_pierce = _week_frame(114.0, 113.0, 114.0, 113.0)
    assert _swept_current_period(no_pierce, AS_OF_MID, "PWH", 115.0, 113.0) is False
    # ...but a forming-Wednesday pierce to 116 with price back at 113 sweeps.
    pierce = _week_frame(114.0, 113.0, 116.0, 113.0)
    assert _swept_current_period(pierce, AS_OF_MID, "PWH", 115.0, 113.0) is True


def test_sweep_unknown_level_or_empty_window() -> None:
    df = _daily(30)  # no bars at/after Wednesday 00:00 → empty "day" window
    assert _swept_current_period(df, AS_OF_MID, "PDH", 102.0, 101.0) is False
    assert _swept_current_period(df, AS_OF_MID, "DO", 100.0, 101.0) is False

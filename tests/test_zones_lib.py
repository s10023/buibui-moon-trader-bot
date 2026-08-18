"""Tests for analytics.zones_lib — additive ``max_zones=None`` return-all path.

The public extractors trim to recent active zones + a few inactive ones (for
chart overlays). ``max_zones=None`` short-circuits to the full chronological
list — needed by the structural touch-decay audit, which must see every zone
ever formed (including mitigated ones). Default behaviour is unchanged.
"""

from __future__ import annotations

import pandas as pd

from analytics.zones_lib import extract_fvg_zones


def _staircase_then_drop() -> pd.DataFrame:
    """A monotonic gapped up-staircase (8 bullish FVGs) then a deep drop that
    mitigates every one of them (and itself forms 1 active bearish FVG) — so
    9 zones total, 8 inactive bullish + 1 active bearish."""
    highs = [105.0 + 10 * k for k in range(10)]
    lows = [100.0 + 10 * k for k in range(10)]
    highs.append(90.0)  # deep drop bar
    lows.append(10.0)
    n = len(highs)
    return pd.DataFrame(
        {
            "open_time": [1_000 + i for i in range(n)],
            "open": lows,
            "high": highs,
            "low": lows,
            "close": highs,
        }
    )


def test_extract_fvg_zones_max_zones_none_returns_all_untrimmed() -> None:
    df = _staircase_then_drop()
    capped = extract_fvg_zones(df, max_zones=3)
    all_zones = extract_fvg_zones(df, max_zones=None)

    # Default trim keeps only active + last-5 inactive, then caps at max_zones.
    assert len(capped) == 3
    # Return-all surfaces every FVG (8 mitigated bullish + 1 active bearish),
    # in chronological formation order.
    assert len(all_zones) == 9
    assert all(z["zone_type"] == "fvg" for z in all_zones)
    starts = [z["start_ms"] for z in all_zones]
    assert starts == sorted(starts)


def test_extract_fvg_zones_default_unchanged() -> None:
    df = _staircase_then_drop()
    # Default call (max_zones omitted): 1 active bearish + last-5 inactive = 6.
    assert len(extract_fvg_zones(df)) == 6


def _two_equal_swing_highs() -> pd.DataFrame:
    """Ramp with exactly two near-equal swing highs at bars 6 and 20 (EQH pair)."""
    n = 30
    highs = [100.0 + 0.01 * i for i in range(n)]
    lows = [90.0 + 0.01 * i for i in range(n)]
    highs[6] = 110.0
    highs[20] = 110.1
    mids = [(h + lo) / 2 for h, lo in zip(highs, lows, strict=True)]
    return pd.DataFrame(
        {
            "open_time": [1_000 + i for i in range(n)],
            "open": mids,
            "high": highs,
            "low": lows,
            "close": mids,
        }
    )


def _bearish_ob() -> pd.DataFrame:
    """Bullish candle at bar 1, then a bearish displacement candle at bar 2."""
    return pd.DataFrame(
        {
            "open_time": [1_000 + i for i in range(5)],
            "open": [100.0, 100.0, 103.0, 96.0, 96.0],
            "high": [101.0, 103.5, 103.5, 97.0, 97.0],
            "low": [99.0, 99.5, 95.0, 95.0, 95.0],
            "close": [100.0, 103.0, 95.5, 96.0, 96.0],
        }
    )


def test_extract_fvg_zones_confirm_ms_is_the_bar_that_completes_the_gap() -> None:
    """A gap needs candle i+1 to exist; start_ms is candle i-1, so confirm is +2 bars."""
    df = _staircase_then_drop()
    ot = df["open_time"].tolist()
    zones = extract_fvg_zones(df, max_zones=None)
    assert zones
    for z in zones:
        i_form = ot.index(z["start_ms"])
        assert z["confirm_ms"] == ot[i_form + 2]


def test_extract_bos_zones_confirm_ms_lags_the_swing_by_swing_lookback() -> None:
    from analytics.zones_lib import extract_bos_zones

    df = _two_equal_swing_highs()
    ot = df["open_time"].tolist()
    zones = extract_bos_zones(df, swing_lookback=5, max_zones=None)
    assert zones
    for z in zones:
        i_swing = ot.index(z["start_ms"])
        assert z["confirm_ms"] == ot[i_swing + 5]


def test_extract_eqh_eql_zones_confirm_ms_follows_the_second_swing() -> None:
    """start_ms is the FIRST swing of the pair; the pool is knowable only once the
    SECOND swing (bar 20) is itself confirmed, i.e. 5 bars later."""
    from analytics.zones_lib import extract_eqh_eql_zones

    df = _two_equal_swing_highs()
    ot = df["open_time"].tolist()
    zones = [
        z for z in extract_eqh_eql_zones(df, max_zones=None) if z["zone_type"] == "eqh"
    ]
    assert len(zones) == 1
    z = zones[0]
    assert z["start_ms"] == ot[6]
    assert z["confirm_ms"] == ot[25]


def test_extract_order_block_zones_confirm_ms_is_the_displacement_bar() -> None:
    from analytics.zones_lib import extract_order_block_zones

    df = _bearish_ob()
    ot = df["open_time"].tolist()
    zones = extract_order_block_zones(df, max_zones=None)
    assert zones
    for z in zones:
        i_form = ot.index(z["start_ms"])
        assert z["confirm_ms"] == ot[i_form + 1]

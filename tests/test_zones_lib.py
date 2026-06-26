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

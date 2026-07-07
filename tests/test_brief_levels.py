"""Level gauge: ATR, ADR, distance/side split, sweep flags."""

import pandas as pd

from analytics.brief._common import DAY_MS
from analytics.brief.levels import adr_pct_14, atr14_wilder, build_level_rows

AS_OF = 1_704_067_200_000 + 30 * DAY_MS  # 2024-01-31 00:00 UTC (Wednesday)


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
        completed_1d=df,
        as_of_ms=AS_OF,
        ref_close=101.0,
        atr14=4.0,
        max_per_side=4,
    )
    assert len(above) <= 4 and len(below) <= 4
    # nearest-first ordering on both sides
    assert [r.dist_atr for r in above] == sorted(r.dist_atr for r in above)
    dists_below = [r.dist_atr for r in below]
    assert dists_below == sorted(dists_below, reverse=True)
    # PDH exists (prev day high = 102) and sits above ref_close=101
    assert any(r.name == "PDH" for r in above)
    for r in above:
        assert r.dist_atr > 0
    for r in below:
        assert r.dist_atr <= 0


def test_build_level_rows_zero_atr_returns_empty() -> None:
    df = _daily(30)
    above, below = build_level_rows(df, df, AS_OF, 101.0, 0.0, 4)
    assert above == [] and below == []

"""Tests for analytics.structural_touch — structural level-hold touch-decay kill-test.

Pure helpers over synthetic OHLCV; no DB / network. See plan
`docs/superpowers/specs` + `tools/structural_touch_decay_audit.py`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pandas.testing as pdt

from analytics import zones_lib
from analytics.structural_touch import (
    TOUCH_TABLE_COLUMNS,
    Zone,
    _zone_from_dict,
    build_touch_table,
    evaluate_touch_decay,
    extract_zones,
    index_touches,
    touch_excursion,
    touch_held,
)


def _staircase_then_drop() -> pd.DataFrame:
    """Gapped up-staircase (8 bullish FVGs) + a deep drop (1 bearish FVG)."""
    highs = [105.0 + 10 * k for k in range(10)]
    lows = [100.0 + 10 * k for k in range(10)]
    highs.append(90.0)
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


def _bars(
    highs: list[float],
    lows: list[float],
    *,
    start_ms: int = 0,
    step_ms: int = 3_600_000,
) -> pd.DataFrame:
    """Synthetic OHLCV frame; open/close set to the bar midpoint."""
    mids = [(h + lo) / 2 for h, lo in zip(highs, lows, strict=True)]
    return pd.DataFrame(
        {
            "open_time": [start_ms + i * step_ms for i in range(len(highs))],
            "open": mids,
            "high": highs,
            "low": lows,
            "close": mids,
        }
    )


def test_touch_excursion_long_measures_forward_atr_excursion() -> None:
    # Touch at idx 1 (close == 100); ATR == 10. Forward bars 2,3 reach
    # high 120 (favorable +2 ATR) and dip to low 95 (adverse +0.5 ATR).
    bars = _bars([101, 100, 110, 120], [99, 100, 98, 95])
    mfe, mae = touch_excursion(bars, 1, bias="long", atr=10.0, window=3)
    assert mfe == 2.0  # (120 - 100) / 10
    assert mae == 0.5  # (100 - 95) / 10


def test_index_touches_counts_first_and_repeat_touches() -> None:
    # Zone band [100, 110] formed at idx 0. Only bars after start_ms count.
    # idx1 inside -> touch 1; idx2 inside (contiguous) -> same touch;
    # idx3 outside (the gap); idx4 inside -> touch 2.
    bars = _bars(
        highs=[200, 105, 108, 125, 107],
        lows=[195, 101, 102, 120, 103],
    )
    zone = Zone(
        zone_type="fvg",
        bias="long",
        zone_low=100.0,
        zone_high=110.0,
        start_ms=int(bars["open_time"].iloc[0]),
    )
    touches = index_touches(zone, bars, min_gap_bars=1)
    assert [t.touch_index for t in touches] == [1, 2]
    assert [t.bar_idx for t in touches] == [1, 4]


def test_index_touches_requires_min_gap_outside_bars() -> None:
    # Same inside/outside pattern, but a single outside bar is NOT enough to
    # separate touches when min_gap_bars=2 -> the idx4 re-entry is suppressed.
    bars = _bars(
        highs=[200, 105, 108, 125, 107],
        lows=[195, 101, 102, 120, 103],
    )
    zone = Zone(
        zone_type="fvg",
        bias="long",
        zone_low=100.0,
        zone_high=110.0,
        start_ms=int(bars["open_time"].iloc[0]),
    )
    touches = index_touches(zone, bars, min_gap_bars=2)
    assert [t.touch_index for t in touches] == [1]


def test_zone_from_dict_band_zone_maps_bias_and_bounds() -> None:
    z = {
        "zone_type": "fvg",
        "direction": "bull",
        "zone_low": 100.0,
        "zone_high": 110.0,
        "start_ms": 5,
    }
    zone = _zone_from_dict(z, atr=10.0, band_atr_frac=0.25)
    assert zone.bias == "long"
    assert (zone.zone_low, zone.zone_high) == (100.0, 110.0)
    assert zone.zone_type == "fvg"
    assert zone.start_ms == 5


def test_zone_from_dict_line_zone_wraps_band_with_atr() -> None:
    # Line zone (bos/eqh) carries a single `price`; wrap to price ± frac*ATR.
    z = {"zone_type": "bos", "direction": "bear", "price": 200.0, "start_ms": 7}
    zone = _zone_from_dict(z, atr=8.0, band_atr_frac=0.25)
    assert zone.bias == "short"  # bear -> short
    assert (zone.zone_low, zone.zone_high) == (198.0, 202.0)  # 200 ± 0.25*8


def test_extract_zones_fvg_returns_normalized_zones() -> None:
    zones = extract_zones(_staircase_then_drop(), "fvg", band_atr_frac=0.25)
    assert len(zones) == 9  # matches zones_lib max_zones=None
    assert all(isinstance(z, Zone) for z in zones)
    assert sum(z.bias == "long" for z in zones) == 8  # 8 bullish
    assert sum(z.bias == "short" for z in zones) == 1  # 1 bearish (drop bar)


def test_extract_zones_fib_walks_forward_and_dedups(monkeypatch: object) -> None:
    # Stub the single-zone fib extractor: it "discovers" zone A early, then a
    # new zone B later. Walk-forward must collect both, once each (dedup).
    calls = {"n": 0}

    def fake_fib(df: pd.DataFrame, **kw: object) -> list[dict[str, object]]:
        calls["n"] += 1
        if len(df) < 30:
            return [
                {
                    "zone_type": "fib_zone",
                    "direction": "bull",
                    "zone_low": 100.0,
                    "zone_high": 105.0,
                    "start_ms": 10,
                }
            ]
        return [
            {
                "zone_type": "fib_zone",
                "direction": "bear",
                "zone_low": 200.0,
                "zone_high": 205.0,
                "start_ms": 50,
            }
        ]

    monkeypatch.setattr(zones_lib, "extract_fib_golden_zones", fake_fib)  # type: ignore[attr-defined]
    df = _bars([1.0] * 60, [0.5] * 60)
    zones = extract_zones(df, "fib", band_atr_frac=0.25, step=5)
    keys = sorted((z.start_ms, z.bias) for z in zones)
    assert keys == [(10, "long"), (50, "short")]


def test_touch_held_true_when_favorable_reaches_target_first() -> None:
    # long, entry close=100, ATR=10. Bar 1 favorable +1.1 ATR, adverse only 0.1.
    bars = _bars([100, 111, 105], [100, 99, 95])
    assert touch_held(
        bars, 0, bias="long", atr=10.0, window=2, hold_thr=1.0, adv_thr=1.0
    )


def test_touch_held_false_when_adverse_reaches_target_first() -> None:
    # Bar 1 adverse -1.2 ATR (stop) before any favorable -> did not hold.
    bars = _bars([100, 101, 120], [100, 88, 90])
    assert not touch_held(
        bars, 0, bias="long", atr=10.0, window=2, hold_thr=1.0, adv_thr=1.0
    )


_TOUCH_COLS = [
    "symbol",
    "tf",
    "zone_type",
    "direction",
    "zone_id",
    "touch_index",
    "mfe_atr",
    "mae_atr",
    "held",
    "ts_ms",
]


def _touched_fvg_then_pad() -> pd.DataFrame:
    # 7-bar block forming bullish FVG(s) touched at idx 4 and 6, then a long
    # constant pad far above the zone (no further touches).
    block_high = [100.0, 106.0, 115.0, 130.0, 112.0, 140.0, 108.0]
    block_low = [95.0, 101.0, 110.0, 120.0, 105.0, 130.0, 102.0]
    pad_high = [2000.0] * 15
    pad_low = [1990.0] * 15
    return _bars(block_high + pad_high, block_low + pad_low)


def test_build_touch_table_has_expected_shape() -> None:
    table = build_touch_table(
        {("BTCUSDT", "1h"): _touched_fvg_then_pad()}, ["fvg"], window=3
    )
    assert list(table.columns) == _TOUCH_COLS
    assert not table.empty
    assert (table["zone_type"] == "fvg").all()
    assert (table["direction"] == "long").all()
    assert table["touch_index"].min() >= 1


def test_build_touch_table_is_causal_no_lookahead() -> None:
    bars = _touched_fvg_then_pad()
    table1 = build_touch_table({("BTCUSDT", "1h"): bars}, ["fvg"], window=3)

    # Perturb only the FINAL bar (far beyond any early touch + window).
    bars2 = bars.copy()
    last = bars2.index[-1]
    bars2.loc[last, "high"] = bars2.loc[last, "high"] * 5
    bars2.loc[last, "low"] = bars2.loc[last, "low"] / 5
    table2 = build_touch_table({("BTCUSDT", "1h"): bars2}, ["fvg"], window=3)

    cut = int(bars["open_time"].iloc[10])
    keys = ["zone_id", "ts_ms"]
    e1 = table1[table1["ts_ms"] <= cut].sort_values(keys).reset_index(drop=True)
    e2 = table2[table2["ts_ms"] <= cut].sort_values(keys).reset_index(drop=True)
    pdt.assert_frame_equal(e1, e2)


def _decay_table(
    first_vals: np.ndarray,
    repeat_vals: np.ndarray,
    *,
    zone_type: str = "fvg",
    direction: str = "long",
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for i, v in enumerate(first_vals):
        rows.append(
            {
                "symbol": "S",
                "tf": "1h",
                "zone_type": zone_type,
                "direction": direction,
                "zone_id": f"z{i}",
                "touch_index": 1,
                "mfe_atr": float(v),
                "mae_atr": 0.0,
                "held": False,
                "ts_ms": i,
            }
        )
    for i, v in enumerate(repeat_vals):
        rows.append(
            {
                "symbol": "S",
                "tf": "1h",
                "zone_type": zone_type,
                "direction": direction,
                "zone_id": f"z{i}",
                "touch_index": 2,
                "mfe_atr": float(v),
                "mae_atr": 0.0,
                "held": False,
                "ts_ms": i,
            }
        )
    return pd.DataFrame(rows, columns=TOUCH_TABLE_COLUMNS)


def test_evaluate_touch_decay_confirms_clear_decay() -> None:
    rng = np.random.default_rng(0)
    first = rng.normal(2.0, 0.3, 150)
    repeat = rng.normal(0.6, 0.3, 150)
    verdicts = evaluate_touch_decay(
        _decay_table(first, repeat),
        min_n=30,
        bar=0.1,
        alpha=0.05,
        n_boot=2000,
        seed=1,
    )
    v = verdicts[0]
    assert v.decision == "DECAY-CONFIRMED"
    assert v.lift is not None and v.lift > 0
    assert v.ci_lo is not None and v.ci_lo > 0.1
    assert v.split_ok


def test_evaluate_touch_decay_no_decay_when_equal() -> None:
    rng = np.random.default_rng(2)
    first = rng.normal(1.0, 0.3, 150)
    repeat = rng.normal(1.0, 0.3, 150)
    v = evaluate_touch_decay(
        _decay_table(first, repeat),
        min_n=30,
        bar=0.1,
        alpha=0.05,
        n_boot=2000,
        seed=1,
    )[0]
    assert v.decision == "NO-DECAY"


def test_evaluate_touch_decay_insufficient_when_thin() -> None:
    v = evaluate_touch_decay(
        _decay_table(np.full(10, 2.0), np.full(10, 0.5)),
        min_n=30,
    )[0]
    assert v.decision == "INSUFFICIENT"
    assert v.n_first == 10

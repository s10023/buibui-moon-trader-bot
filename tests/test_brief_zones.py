"""Zone selection: active filter, price-key normalisation, signed distance."""

from typing import Any
from unittest.mock import patch

import pandas as pd

from analytics.brief.zones import build_zone_rows


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [0, 1, 2],
            "open": [100.0, 100.0, 100.0],
            "high": [101.0, 101.0, 101.0],
            "low": [99.0, 99.0, 99.0],
            "close": [100.0, 100.0, 100.0],
        }
    )


def _fake_zones(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
    return [
        {
            "zone_type": "fvg",
            "direction": "bull",
            "zone_low": 90.0,
            "zone_high": 92.0,
            "active": True,
        },
        {
            "zone_type": "fvg",
            "direction": "bear",
            "zone_low": 110.0,
            "zone_high": 112.0,
            "active": True,
        },
        {
            "zone_type": "fvg",
            "direction": "bull",
            "zone_low": 95.0,
            "zone_high": 96.0,
            "active": False,
        },  # inactive -> dropped
        {
            "zone_type": "bos",
            "direction": "bull",
            "price": 98.0,
            "active": True,
        },  # single-price zone
        {
            "zone_type": "eqh",
            "direction": "bear",
            "price": 99.5,
            "active": True,
        },  # ref inside? no: 99.5 < 100 -> below
    ]


def _empty(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
    return []


def test_build_zone_rows_normalises_and_signs() -> None:
    with (
        patch("analytics.brief.zones.extract_fvg_zones", _fake_zones),
        patch("analytics.brief.zones.extract_order_block_zones", _empty),
        patch("analytics.brief.zones.extract_eqh_eql_zones", _empty),
        patch("analytics.brief.zones.extract_bos_zones", _empty),
    ):
        above, below = build_zone_rows({"4h": _frame()}, 100.0, 4.0, 2)
    # above: only the 110-112 bear fvg -> dist (110-100)/4 = +2.5
    assert len(above) == 1 and abs(above[0].dist_atr - 2.5) < 1e-9
    # below: nearest-first -> eqh @99.5 (-0.125) then bos @98 (-0.5); cap=2
    assert [z.zone_type for z in below] == ["eqh", "bos"]
    assert below[0].zone_low == below[0].zone_high == 99.5
    # the inactive zone never appears
    assert all(z.zone_low != 95.0 for z in above + below)


def test_build_zone_rows_inside_marker() -> None:
    def one_zone(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "zone_type": "ob",
                "direction": "bull",
                "zone_low": 99.0,
                "zone_high": 101.0,
                "active": True,
            }
        ]

    with (
        patch("analytics.brief.zones.extract_fvg_zones", _empty),
        patch("analytics.brief.zones.extract_order_block_zones", one_zone),
        patch("analytics.brief.zones.extract_eqh_eql_zones", _empty),
        patch("analytics.brief.zones.extract_bos_zones", _empty),
    ):
        above, below = build_zone_rows({"1d": _frame()}, 100.0, 4.0, 2)
    assert above == []
    assert len(below) == 1 and below[0].inside and below[0].dist_atr == 0.0


def test_build_zone_rows_zero_atr() -> None:
    above, below = build_zone_rows({"4h": _frame()}, 100.0, 0.0, 2)
    assert above == [] and below == []

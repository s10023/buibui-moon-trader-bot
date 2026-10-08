"""Scoping of the H8 audit driver's entry frame (#943 sensitivity legs)."""

from __future__ import annotations

import pandas as pd

from tools.indicator_condition_audit import build_parser, scope_entries

_DAY_MS = 86_400_000
_CUT_MS = int(pd.Timestamp("2026-08-13", tz="UTC").timestamp() * 1000)


def _entries() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["BTCUSDT"] * 4,
            "tf": ["4h", "4h", "1h", "4h"],
            "strategy": ["bos", "fvg", "fvg", "liquidity_sweep"],
            "direction": ["short"] * 4,
            "entry_time": [_CUT_MS - _DAY_MS, _CUT_MS - 1, _CUT_MS - _DAY_MS, _CUT_MS],
            "pnl_r": [1.0, -1.0, 0.5, 2.0],
        }
    )


def test_no_scope_keeps_every_row() -> None:
    assert len(scope_entries(_entries())) == 4


def test_until_is_exclusive_at_midnight() -> None:
    out = scope_entries(_entries(), until_ms=_CUT_MS)
    assert out["entry_time"].max() == _CUT_MS - 1
    assert len(out) == 3


def test_exclude_strategies_drops_only_named() -> None:
    out = scope_entries(_entries(), exclude_strategies=["bos", "liquidity_sweep"])
    assert set(out["strategy"]) == {"fvg"}
    assert len(out) == 2


def test_legs_compose() -> None:
    out = scope_entries(
        _entries(),
        timeframes=["4h"],
        exclude_strategies=["bos"],
        until_ms=_CUT_MS,
    )
    assert out[["tf", "strategy"]].values.tolist() == [["4h", "fvg"]]


def test_parser_until_parses_to_utc_midnight() -> None:
    args = build_parser().parse_args(
        ["--until", "2026-08-13", "--exclude-strategies", "bos", "liquidity_sweep"]
    )
    assert int(pd.Timestamp(args.until, tz="UTC").timestamp() * 1000) == _CUT_MS
    assert args.exclude_strategies == ["bos", "liquidity_sweep"]

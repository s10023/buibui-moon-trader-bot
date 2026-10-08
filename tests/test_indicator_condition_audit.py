"""The H8 audit driver's entry frame: live as-of (#952) and scoping legs (#943)."""

from __future__ import annotations

import pandas as pd

from tools.indicator_condition_audit import build_parser, normalize_live, scope_entries

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


def test_live_entry_is_the_signal_candle_close() -> None:
    """``candle_ts_ms`` is the signal candle's OPEN; the alert fires at its close.

    The as-of must be ``candle_ts_ms + tf`` (#952), the instant the backtest's
    ``entry_time`` already denotes. The candle open let the tagger see a 1d bar
    and, on 15m, a 1h bar that both close after the alert.
    """
    raw = pd.DataFrame(
        {
            "symbol": ["BTCUSDT"] * 3,
            "tf": ["15m", "4h", "1d"],
            "strategy": ["fvg"] * 3,
            "direction": ["short"] * 3,
            "candle_ts_ms": [_CUT_MS] * 3,
            "outcome_r": [1.0, -1.0, 0.5],
        }
    )
    out = normalize_live(raw)
    assert out["entry_time"].tolist() == [
        _CUT_MS + 900_000,
        _CUT_MS + 14_400_000,
        _CUT_MS + _DAY_MS,
    ]

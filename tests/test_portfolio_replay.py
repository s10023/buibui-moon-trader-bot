"""End-to-end: replay a seeded in-memory ledger through the paper book."""

import duckdb
import pandas as pd
import pytest

from analytics.store import init_schema, upsert_signal_outcome
from portfolio.replay import replay_ledger
from portfolio.report import format_report
from portfolio.sizing import SizingConfig

_DAY = 86_400_000


def _seed_ohlcv_1d(conn: duckdb.DuckDBPyConnection, symbol: str, n_days: int) -> None:
    df = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "timeframe": "1d",
                "open_time": d * _DAY,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1.0,
                "taker_buy_volume": 0.5,
            }
            for d in range(n_days)
        ]
    )
    conn.register("_o", df)
    conn.execute(
        "INSERT INTO ohlcv (symbol, timeframe, open_time, open, high, low, close, volume, taker_buy_volume)"
        " SELECT symbol, timeframe, open_time, open, high, low, close, volume, taker_buy_volume FROM _o"
    )
    conn.unregister("_o")


def _seed_resolved(
    conn: duckdb.DuckDBPyConnection,
    *,
    signal_id: str,
    symbol: str,
    entry_day: int,
    exit_day: int,
    outcome: str,
    outcome_r: float,
    direction: str = "long",
    entry: float = 100.0,
    sl: float = 95.0,
) -> None:
    upsert_signal_outcome(
        conn,
        {
            "signal_id": signal_id,
            "symbol": symbol,
            "tf": "15m",
            "strategy": "fvg",
            "direction": direction,
            "fired_at_ms": entry_day * _DAY,
            "candle_ts_ms": entry_day * _DAY,
            "entry_price": entry,
            "sl_price": sl,
            "tp_price": entry + 10.0,
            "rr_ratio": 2.0,
            "confidence_at_fire": 3,
            "tags": "",
            "outcome": outcome,
            "outcome_r": outcome_r,
            "outcome_filled_at_ms": exit_day * _DAY,
        },
    )


def test_replay_ledger_produces_curves_and_trades() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed_ohlcv_1d(conn, "BTCUSDT", 12)
    _seed_resolved(
        conn,
        signal_id="a",
        symbol="BTCUSDT",
        entry_day=1,
        exit_day=1,
        outcome="win",
        outcome_r=2.0,
    )
    _seed_resolved(
        conn,
        signal_id="b",
        symbol="BTCUSDT",
        entry_day=3,
        exit_day=3,
        outcome="loss",
        outcome_r=-1.0,
    )
    cfg = SizingConfig(apply_high_vol_halving=False)  # isolate sizing from regime
    res = replay_ledger(conn, cfg)
    assert len(res.sized) == 2
    # net realized = 25*2 - 25*1 = +25 on the fixed basis by the end
    assert res.pnl_fixed[-1] == pytest.approx(25.0)


def test_replay_skips_unscoreable_and_null_r() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed_ohlcv_1d(conn, "BTCUSDT", 6)
    _seed_resolved(
        conn,
        signal_id="ok",
        symbol="BTCUSDT",
        entry_day=1,
        exit_day=1,
        outcome="win",
        outcome_r=2.0,
    )
    # NULL outcome_r -> excluded by the query
    upsert_signal_outcome(
        conn,
        {
            "signal_id": "null_r",
            "symbol": "BTCUSDT",
            "tf": "15m",
            "strategy": "fvg",
            "direction": "long",
            "fired_at_ms": 2 * _DAY,
            "candle_ts_ms": 2 * _DAY,
            "entry_price": 100.0,
            "sl_price": 95.0,
            "tp_price": 110.0,
            "rr_ratio": 2.0,
            "confidence_at_fire": 3,
            "tags": "",
            "outcome": "open",
            "outcome_r": None,
            "outcome_filled_at_ms": None,
        },
    )
    res = replay_ledger(conn, SizingConfig(apply_high_vol_halving=False))
    assert [t.signal_id for t in res.sized] == ["ok"]


def test_replay_empty_ledger_returns_empty_result() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    res = replay_ledger(conn, SizingConfig())
    assert res.sized == [] and len(res.daily_index) == 0


def test_format_report_renders_headline() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed_ohlcv_1d(conn, "BTCUSDT", 12)
    for i, (ed, oc, r) in enumerate(
        [(1, "win", 2.0), (3, "loss", -1.0), (5, "win", 2.0)]
    ):
        _seed_resolved(
            conn,
            signal_id=f"s{i}",
            symbol="BTCUSDT",
            entry_day=ed,
            exit_day=ed,
            outcome=oc,
            outcome_r=r,
        )
    cfg = SizingConfig(apply_high_vol_halving=False)
    res = replay_ledger(conn, cfg)
    text = format_report(res, cfg)
    assert "Sharpe" in text
    assert "fixed-notional" in text.lower()
    assert "Attribution" in text

"""Integration tests for the exit-replay driver (analytics.exits.audit).

Builds a tiny in-memory ledger + OHLCV and checks that the same alert
re-resolves differently under policy #0 (fixed) vs the composite, and that the
A/B wiring through the P1 paper book returns one row per policy.
"""

import duckdb
import pandas as pd
import pytest

from analytics.exits.audit import (
    resolve_ledger_under_policy,
    run_exit_ab,
)
from analytics.store import init_schema, upsert_signal_outcome
from portfolio.sizing import SizingConfig

_HOUR = 3_600_000
_MH = {"1h": 5}
_TS = {"1h": 2}


def _insert_ohlcv(conn: duckdb.DuckDBPyConnection) -> None:
    # 1h BTCUSDT: signal candle @0, then a rally to +1R that fades to entry.
    # risk = 2 (entry 100 / sl 98); 1R = 102, tp@3R = 106 (never reached).
    bars = [
        (0, 100, 100, 100),
        (_HOUR, 102, 100, 101),
        (2 * _HOUR, 101, 100, 100),
        (3 * _HOUR, 101, 99, 100),
        (4 * _HOUR, 101, 99, 100),
        (5 * _HOUR, 101, 99, 100),
    ]
    df = pd.DataFrame(
        [
            {
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "open_time": ot,
                "open": c,
                "high": h,
                "low": lo,
                "close": c,
                "volume": 1.0,
                "taker_buy_volume": 0.5,
            }
            for ot, h, lo, c in bars
        ]
    )
    conn.register("_o", df)
    conn.execute("INSERT INTO ohlcv SELECT * FROM _o")
    conn.unregister("_o")


def _insert_alert(conn: duckdb.DuckDBPyConnection) -> None:
    upsert_signal_outcome(
        conn,
        {
            "signal_id": "a1",
            "symbol": "BTCUSDT",
            "tf": "1h",
            "strategy": "fvg",
            "direction": "long",
            "fired_at_ms": 0,
            "candle_ts_ms": 0,
            "entry_price": 100.0,
            "sl_price": 98.0,
            "tp_price": 106.0,
            "rr_ratio": 3.0,
            "confidence_at_fire": 3,
            "tags": "",
        },
    )
    conn.execute(
        "UPDATE signal_alert_outcomes "
        "SET outcome = 'expired', outcome_r = 0.0, outcome_filled_at_ms = ? "
        "WHERE signal_id = 'a1'",
        [5 * _HOUR],
    )


def _db() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _insert_ohlcv(conn)
    _insert_alert(conn)
    return conn


class TestResolveUnderPolicy:
    def test_fixed_expires_at_time_cap(self) -> None:
        conn = _db()
        pr = resolve_ledger_under_policy(
            conn, "fixed", max_hold_by_tf=_MH, time_stop_by_tf=_TS
        )
        assert pr.n == 1
        t = pr.trades[0]
        assert t.outcome == "expired"
        assert t.realized_r == pytest.approx(0.0)
        assert t.exit_ts_ms == 5 * _HOUR  # marked at the cap bar
        assert pr.expiry_rate == 1.0

    def test_composite_locks_partial_then_breakeven(self) -> None:
        conn = _db()
        pr = resolve_ledger_under_policy(
            conn, "composite", max_hold_by_tf=_MH, time_stop_by_tf=_TS
        )
        assert pr.n == 1
        t = pr.trades[0]
        assert t.outcome == "breakeven"
        assert t.realized_r == pytest.approx(0.5)  # 0.5*1R + 0.5*BE
        assert t.exit_ts_ms == 2 * _HOUR  # BE stop the bar after arming
        assert pr.expiry_rate == 0.0
        assert pr.win_rate == 0.0

    def test_policies_diverge(self) -> None:
        conn = _db()
        f = resolve_ledger_under_policy(
            conn, "fixed", max_hold_by_tf=_MH, time_stop_by_tf=_TS
        )
        c = resolve_ledger_under_policy(
            conn, "composite", max_hold_by_tf=_MH, time_stop_by_tf=_TS
        )
        assert f.avg_r != c.avg_r
        assert c.avg_hold_bars < f.avg_hold_bars

    def test_unknown_kind_raises(self) -> None:
        conn = _db()
        with pytest.raises(ValueError):
            resolve_ledger_under_policy(
                conn, "nope", max_hold_by_tf=_MH, time_stop_by_tf=_TS
            )


class TestRunExitAb:
    def test_returns_one_row_per_policy(self) -> None:
        conn = _db()
        rows = run_exit_ab(
            conn, SizingConfig(), max_hold_by_tf=_MH, time_stop_by_tf=_TS
        )
        assert [r.name for r in rows] == ["fixed", "composite"]
        assert all(r.n_sized + r.n_skipped == 1 for r in rows)

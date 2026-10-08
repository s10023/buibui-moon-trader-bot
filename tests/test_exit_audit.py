"""Integration tests for the exit-replay driver (analytics.exits.audit).

Builds a tiny in-memory ledger + OHLCV and checks that the same alert
re-resolves differently under policy #0 (fixed) vs the composite, and that the
A/B wiring through the P1 paper book returns one row per policy.
"""

from collections.abc import Sequence
from typing import Any

import duckdb
import pandas as pd
import pytest

from analytics.exits.audit import (
    PolicyResult,
    paired_delta_ci,
    resolve_ledger_under_policy,
    run_exit_ab,
)
from analytics.store import init_schema, upsert_signal_outcome
from portfolio.sizing import SizingConfig, round_trip_drag_r

_HOUR = 3_600_000
_MH = {"1h": 5}
_TS = {"1h": 2}


def _insert_ohlcv(conn: duckdb.DuckDBPyConnection) -> None:
    # 1h BTCUSDT: signal candle @0, then a rally to +1R that fades to entry.
    # risk = 2 (entry 100 / sl 98); 1R = 102, tp@3R = 106 (never reached).
    _insert_bars(
        conn,
        "1h",
        [
            (0, 100, 100, 100),
            (_HOUR, 102, 100, 101),
            (2 * _HOUR, 101, 100, 100),
            (3 * _HOUR, 101, 99, 100),
            (4 * _HOUR, 101, 99, 100),
            (5 * _HOUR, 101, 99, 100),
        ],
    )


def _insert_bars(
    conn: duckdb.DuckDBPyConnection,
    tf: str,
    bars: Sequence[tuple[int, float, float, float]],
) -> None:
    df = pd.DataFrame(
        [
            {
                "symbol": "BTCUSDT",
                "timeframe": tf,
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
    conn.execute("INSERT INTO ohlcv_all SELECT 'binance', * FROM _o")
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


_Q = 900_000
# The 1h bar after the signal spans SL (98) and TP (106); its 15m bars hit TP first.
_TIE_1H = [
    (0, 100, 100, 100),
    (_HOUR, 106.5, 97.5, 101),
    (2 * _HOUR, 101, 99, 100),
    (3 * _HOUR, 101, 99, 100),
    (4 * _HOUR, 101, 99, 100),
    (5 * _HOUR, 101, 99, 100),
]
_TIE_15M = [
    (_HOUR, 103, 100, 103),
    (_HOUR + _Q, 106.5, 102, 106),
    (_HOUR + 2 * _Q, 104, 97.5, 98.5),
    (_HOUR + 3 * _Q, 101.5, 98.5, 101),
]


def _tie_db(
    fine: Sequence[tuple[int, float, float, float]],
) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _insert_bars(conn, "1h", _TIE_1H)
    _insert_bars(conn, "15m", fine)
    _insert_alert(conn)
    return conn


class TestTieResolution:
    def _fixed(self, conn: duckdb.DuckDBPyConnection, **kw: Any) -> PolicyResult:
        return resolve_ledger_under_policy(
            conn, "fixed", max_hold_by_tf=_MH, time_stop_by_tf=_TS, **kw
        )

    def test_adverse_first_without_fine_tf(self) -> None:
        pr = self._fixed(_tie_db(_TIE_15M))
        assert pr.trades[0].outcome == "loss"
        assert (pr.ambiguous_bars, pr.resolved_bars) == (1, 0)

    def test_fine_tf_resolves_tp_first(self) -> None:
        pr = self._fixed(_tie_db(_TIE_15M), fine_tf="15m")
        assert pr.trades[0].outcome == "win"
        assert pr.trades[0].realized_r == pytest.approx(3.0)
        assert (pr.ambiguous_bars, pr.resolved_bars) == (1, 1)

    def test_incomplete_fine_bars_stay_adverse_first(self) -> None:
        pr = self._fixed(_tie_db(_TIE_15M[:3]), fine_tf="15m")
        assert pr.trades[0].outcome == "loss"
        assert (pr.ambiguous_bars, pr.resolved_bars) == (1, 0)

    def test_fine_tf_not_finer_is_ignored(self) -> None:
        pr = self._fixed(_tie_db(_TIE_15M), fine_tf="1h")
        assert pr.trades[0].outcome == "loss"

    def test_tfs_filter_excludes_other_timeframes(self) -> None:
        assert self._fixed(_tie_db(_TIE_15M), tfs=("4h",)).n == 0

    def test_net_subtracts_round_trip_drag(self) -> None:
        gross = self._fixed(_tie_db(_TIE_15M), fine_tf="15m")
        net = self._fixed(_tie_db(_TIE_15M), fine_tf="15m", net=True)
        drag = round_trip_drag_r(100.0, 98.0)
        assert drag > 0.0
        assert net.trades[0].realized_r == pytest.approx(
            gross.trades[0].realized_r - drag
        )


class TestPairedDelta:
    def test_mean_of_per_signal_differences(self) -> None:
        conn = _db()
        f = resolve_ledger_under_policy(
            conn, "fixed", max_hold_by_tf=_MH, time_stop_by_tf=_TS
        )
        c = resolve_ledger_under_policy(
            conn, "composite", max_hold_by_tf=_MH, time_stop_by_tf=_TS
        )
        ci = paired_delta_ci(f, c)
        assert ci.point == pytest.approx(0.5)  # composite 0.5 vs fixed 0.0

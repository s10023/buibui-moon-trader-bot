"""Tests for analytics/data_store.py."""

import time
from typing import Any

import duckdb
import pandas as pd
import pytest

from analytics.backtest_lib import BacktestResult, Trade
from analytics.data_store import (
    BacktestSnapshot,
    _backtest_run_id,
    _make_bt_cache_key,
    get_backtest_cache,
    get_confidence_ratings,
    get_latest_open_time,
    get_ohlcv,
    get_signals_history,
    get_win_rate_by_strategy,
    init_schema,
    list_backtest_runs,
    prune_backtest_cache,
    put_backtest_cache,
    upsert_backtest_run,
    upsert_backtest_trades,
    upsert_confidence_ratings,
    upsert_funding_rates,
    upsert_ohlcv,
    upsert_open_interest,
    upsert_signal_outcome,
    upsert_signals,
)

_OHLCV_ROW: dict[str, object] = {
    "symbol": "BTCUSDT",
    "timeframe": "1h",
    "open_time": 1_700_000_000_000,
    "open": 30000.0,
    "high": 31000.0,
    "low": 29500.0,
    "close": 30500.0,
    "volume": 100.0,
    "taker_buy_volume": 55.0,
}


def _one(conn: duckdb.DuckDBPyConnection, sql: str) -> tuple[Any, ...]:
    """Execute a query and return the single result row, asserting it exists."""
    row = conn.execute(sql).fetchone()
    assert row is not None
    return row


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


_SIGNAL_ROW: dict[str, object] = {
    "symbol": "BTCUSDT",
    "timeframe": "1h",
    "strategy": "fvg",
    "open_time": 1_700_000_000_000,
    "direction": "long",
    "entry_price": 30500.0,
    "sl_price": 29000.0,
    "reason": "FVG filled",
    "confidence": 4,
    "fired_at": 1_700_000_001_000,
}


class TestInitSchema:
    def test_creates_tables(self, conn: duckdb.DuckDBPyConnection) -> None:
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert {
            "ohlcv",
            "ohlcv_all",
            "db_meta",
            "funding_rates",
            "open_interest",
            "venue_spot_daily",
            "spot_ohlcv",
            "symbol_lifecycle",
            "signals",
            "signal_alert_outcomes",
            "backtest_runs",
            "backtest_trades",
            "backtest_combos",
            "backtest_cross_tf_combos",
            "backtest_cache",
            "stats_cache",
            "confidence_ratings",
        } == tables

    def test_idempotent(self, conn: duckdb.DuckDBPyConnection) -> None:
        init_schema(conn)
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert "ohlcv" in tables

    def test_confidence_ratings_has_dsr_column(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        cols = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'confidence_ratings'"
            ).fetchall()
        }
        assert "dsr" in cols

    def test_migrates_dsr_onto_legacy_confidence_ratings(self) -> None:
        """A confidence_ratings table created before the dsr column gains it,
        preserving existing rows with a NULL dsr."""
        c = duckdb.connect(":memory:")
        c.execute(
            "CREATE TABLE confidence_ratings ("
            "config_name TEXT NOT NULL, strategy TEXT NOT NULL, tf TEXT NOT NULL, "
            "direction TEXT NOT NULL, stars INTEGER NOT NULL, avg_r REAL, "
            "win_rate REAL, updated_at_ms BIGINT, day_filter TEXT, "
            "PRIMARY KEY (config_name, strategy, tf, direction))"
        )
        c.execute(
            "INSERT INTO confidence_ratings VALUES "
            "('signal_watch', 'fvg', '1h', 'combined', 3, 0.3, 0.5, 1, 'off')"
        )
        init_schema(c)
        cols = {
            row[0]
            for row in c.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'confidence_ratings'"
            ).fetchall()
        }
        assert "dsr" in cols
        assert c.execute("SELECT stars, dsr FROM confidence_ratings").fetchone() == (
            3,
            None,
        )


class TestUpsertOhlcv:
    def test_inserts_rows(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_ohlcv(conn, pd.DataFrame([_OHLCV_ROW]), venue="binance")
        assert _one(conn, "SELECT COUNT(*) FROM ohlcv")[0] == 1

    def test_replaces_on_conflict(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_ohlcv(conn, pd.DataFrame([_OHLCV_ROW]), venue="binance")
        upsert_ohlcv(
            conn, pd.DataFrame([{**_OHLCV_ROW, "close": 99999.0}]), venue="binance"
        )
        assert _one(conn, "SELECT close FROM ohlcv")[0] == 99999.0

    def test_empty_dataframe_is_noop(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_ohlcv(
            conn, pd.DataFrame(columns=list(_OHLCV_ROW.keys())), venue="binance"
        )
        assert _one(conn, "SELECT COUNT(*) FROM ohlcv")[0] == 0


class TestUpsertFundingRates:
    def test_inserts_rows(self, conn: duckdb.DuckDBPyConnection) -> None:
        df = pd.DataFrame(
            [
                {
                    "symbol": "BTCUSDT",
                    "funding_time": 1_700_000_000_000,
                    "funding_rate": 0.0001,
                }
            ]
        )
        upsert_funding_rates(conn, df)
        assert _one(conn, "SELECT COUNT(*) FROM funding_rates")[0] == 1

    def test_empty_dataframe_is_noop(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_funding_rates(
            conn, pd.DataFrame(columns=["symbol", "funding_time", "funding_rate"])
        )
        assert _one(conn, "SELECT COUNT(*) FROM funding_rates")[0] == 0


class TestUpsertOpenInterest:
    def test_inserts_rows(self, conn: duckdb.DuckDBPyConnection) -> None:
        df = pd.DataFrame(
            [
                {
                    "symbol": "BTCUSDT",
                    "timestamp": 1_700_000_000_000,
                    "oi_usd": 30_000_000.0,
                }
            ]
        )
        upsert_open_interest(conn, df)
        assert _one(conn, "SELECT COUNT(*) FROM open_interest")[0] == 1

    def test_empty_dataframe_is_noop(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_open_interest(
            conn, pd.DataFrame(columns=["symbol", "timestamp", "oi_usd"])
        )
        assert _one(conn, "SELECT COUNT(*) FROM open_interest")[0] == 0


class TestGetOhlcv:
    def test_returns_rows_in_range(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_ohlcv(conn, pd.DataFrame([_OHLCV_ROW]), venue="binance")
        result = get_ohlcv(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert len(result) == 1
        assert result.iloc[0]["close"] == 30500.0

    def test_excludes_rows_outside_range(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_ohlcv(conn, pd.DataFrame([_OHLCV_ROW]), venue="binance")
        result = get_ohlcv(conn, "BTCUSDT", "1h", 0, 1_000_000_000)
        assert result.empty

    def test_returns_empty_dataframe_when_no_data(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        result = get_ohlcv(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert result.empty


class TestTakerBuyVolume:
    def test_persists_taker_buy_volume(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_ohlcv(conn, pd.DataFrame([_OHLCV_ROW]), venue="binance")
        row = conn.execute("SELECT taker_buy_volume FROM ohlcv").fetchone()
        assert row is not None
        assert row[0] == 55.0

    def test_null_taker_buy_volume_accepted(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        row = {**_OHLCV_ROW, "taker_buy_volume": None}
        upsert_ohlcv(conn, pd.DataFrame([row]), venue="binance")
        result = conn.execute("SELECT taker_buy_volume FROM ohlcv").fetchone()
        assert result is not None
        assert result[0] is None

    def test_get_ohlcv_returns_taker_buy_volume_column(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        upsert_ohlcv(conn, pd.DataFrame([_OHLCV_ROW]), venue="binance")
        result = get_ohlcv(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert "taker_buy_volume" in result.columns
        assert result.iloc[0]["taker_buy_volume"] == 55.0


class TestUpsertSignals:
    def test_inserts_rows(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signals(conn, pd.DataFrame([_SIGNAL_ROW]))
        assert _one(conn, "SELECT COUNT(*) FROM signals")[0] == 1

    def test_ignores_on_conflict(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signals(conn, pd.DataFrame([_SIGNAL_ROW]))
        # Same PK — second insert should be ignored, not raise or update.
        upsert_signals(
            conn, pd.DataFrame([{**_SIGNAL_ROW, "reason": "updated reason"}])
        )
        row = _one(conn, "SELECT reason FROM signals")
        assert row[0] == "FVG filled"  # original preserved

    def test_empty_dataframe_is_noop(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signals(conn, pd.DataFrame(columns=list(_SIGNAL_ROW.keys())))
        assert _one(conn, "SELECT COUNT(*) FROM signals")[0] == 0

    def test_multiple_strategies_same_candle(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        row2 = {**_SIGNAL_ROW, "strategy": "bos"}
        upsert_signals(conn, pd.DataFrame([_SIGNAL_ROW, row2]))
        assert _one(conn, "SELECT COUNT(*) FROM signals")[0] == 2


class TestGetSignalsHistory:
    def test_returns_signals_in_range(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signals(conn, pd.DataFrame([_SIGNAL_ROW]))
        result = get_signals_history(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert len(result) == 1
        assert result.iloc[0]["strategy"] == "fvg"
        assert result.iloc[0]["direction"] == "long"

    def test_excludes_outside_range(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signals(conn, pd.DataFrame([_SIGNAL_ROW]))
        result = get_signals_history(conn, "BTCUSDT", "1h", 0, 1_000_000_000)
        assert result.empty

    def test_filters_by_symbol_and_timeframe(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        other = {**_SIGNAL_ROW, "symbol": "ETHUSDT"}
        upsert_signals(conn, pd.DataFrame([_SIGNAL_ROW, other]))
        result = get_signals_history(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert len(result) == 1
        assert result.iloc[0]["symbol"] == "BTCUSDT"

    def test_returns_empty_when_no_data(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = get_signals_history(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert result.empty

    def test_ordered_descending_by_open_time(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        row1 = {**_SIGNAL_ROW, "open_time": 1_700_000_000_000}
        row2 = {**_SIGNAL_ROW, "open_time": 1_700_003_600_000, "strategy": "bos"}
        upsert_signals(conn, pd.DataFrame([row1, row2]))
        result = get_signals_history(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert result.iloc[0]["open_time"] > result.iloc[1]["open_time"]


_OUTCOME_ROW: dict[str, object] = {
    "signal_id": "btcusdt-1h-fvg-1700000000000-long",
    "symbol": "BTCUSDT",
    "tf": "1h",
    "strategy": "fvg",
    "direction": "long",
    "fired_at_ms": 1_700_000_001_000,
    "candle_ts_ms": 1_700_000_000_000,
    "entry_price": 30500.0,
    "sl_price": 29000.0,
    "tp_price": 33500.0,
    "rr_ratio": 2.0,
    "confidence_at_fire": 4,
    "tags": '["vol_high"]',
    "outcome": None,
    "outcome_r": None,
    "outcome_filled_at_ms": None,
}


class TestSignalAlertOutcomesSchema:
    def test_init_creates_signal_alert_outcomes_table(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert "signal_alert_outcomes" in tables

    def test_signal_alert_outcomes_table_idempotent(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        init_schema(conn)
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert "signal_alert_outcomes" in tables

    def test_migration_renames_signal_outcomes(self) -> None:
        c = duckdb.connect(":memory:")
        # Simulate a legacy DB with the old table name.
        c.execute("""
            CREATE TABLE signal_outcomes (
                signal_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL, tf TEXT NOT NULL,
                strategy TEXT NOT NULL, direction TEXT NOT NULL,
                fired_at_ms BIGINT NOT NULL,
                candle_ts_ms BIGINT, entry_price DOUBLE,
                sl_price DOUBLE, tp_price DOUBLE,
                rr_ratio DOUBLE, confidence_at_fire INTEGER,
                tags TEXT, outcome TEXT,
                outcome_r DOUBLE, outcome_filled_at_ms BIGINT
            )
        """)
        c.execute(
            "INSERT INTO signal_outcomes(signal_id, symbol, tf, strategy, direction, fired_at_ms) "
            "VALUES ('old-id', 'BTCUSDT', '1h', 'fvg', 'long', 1700000001000)"
        )
        init_schema(c)
        tables = {r[0] for r in c.execute("SHOW TABLES").fetchall()}
        assert "signal_alert_outcomes" in tables
        assert "signal_outcomes" not in tables
        # Data preserved after rename.
        count = c.execute("SELECT COUNT(*) FROM signal_alert_outcomes").fetchone()
        assert count is not None
        assert count[0] == 1


class TestUpsertSignalOutcome:
    def test_inserts_row(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signal_outcome(conn, dict(_OUTCOME_ROW))
        assert _one(conn, "SELECT COUNT(*) FROM signal_alert_outcomes")[0] == 1

    def test_upsert_replaces_on_conflict(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signal_outcome(conn, dict(_OUTCOME_ROW))
        updated = {**_OUTCOME_ROW, "outcome": "win", "outcome_r": 1.8}
        upsert_signal_outcome(conn, updated)
        # Still only one row (no duplicate inserted).
        assert _one(conn, "SELECT COUNT(*) FROM signal_alert_outcomes")[0] == 1
        row = _one(conn, "SELECT outcome, outcome_r FROM signal_alert_outcomes")
        assert row[0] == "win"
        assert abs(row[1] - 1.8) < 1e-9

    def test_missing_optional_fields_default_to_null(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        minimal: dict[str, object] = {
            "signal_id": "minimal-signal",
            "symbol": "ETHUSDT",
            "tf": "4h",
            "strategy": "bos",
            "direction": "short",
            "fired_at_ms": 1_700_000_002_000,
        }
        upsert_signal_outcome(conn, minimal)
        row = _one(conn, "SELECT outcome, outcome_r FROM signal_alert_outcomes")
        assert row[0] is None
        assert row[1] is None

    def test_stores_all_fields_correctly(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signal_outcome(conn, dict(_OUTCOME_ROW))
        row = _one(
            conn,
            "SELECT symbol, tf, strategy, direction, entry_price, sl_price, "
            "tp_price, rr_ratio, confidence_at_fire, tags "
            "FROM signal_alert_outcomes",
        )
        assert row[0] == "BTCUSDT"
        assert row[1] == "1h"
        assert row[2] == "fvg"
        assert row[3] == "long"
        assert row[4] == 30500.0
        assert row[5] == 29000.0
        assert row[6] == 33500.0
        assert abs(row[7] - 2.0) < 1e-9
        assert row[8] == 4
        assert row[9] == '["vol_high"]'


# ---------------------------------------------------------------------------
# Helpers for backtest store tests
# ---------------------------------------------------------------------------


class _FakeTrade:
    def __init__(
        self,
        signal_time: int,
        entry_time: int,
        entry_price: float,
        direction: str,
        sl_price: float,
        tp_price: float,
        outcome: str,
        pnl_r: float | None,
        low_volume: bool = False,
        volume_spike: bool = False,
    ) -> None:
        self.signal_time = signal_time
        self.entry_time = entry_time
        self.entry_price = entry_price
        self.direction = direction
        self.sl_price = sl_price
        self.tp_price = tp_price
        self.exit_time: int | None = entry_time + 3_600_000
        self.exit_price: float | None = tp_price if outcome == "win" else sl_price
        self.outcome = outcome
        self.pnl_r = pnl_r
        self.low_volume = low_volume
        self.volume_spike = volume_spike


class _FakeResult:
    def __init__(self, symbol: str, timeframe: str, strategy: str) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.strategy = strategy
        self.fee_pct = 0.0
        self.trades: list[Any] = [
            _FakeTrade(
                1_700_000_000_000,
                1_700_003_600_000,
                30000.0,
                "long",
                29400.0,
                31200.0,
                "win",
                2.0,
            ),
            _FakeTrade(
                1_700_007_200_000,
                1_700_010_800_000,
                30100.0,
                "long",
                29498.0,
                31304.0,
                "loss",
                -1.0,
            ),
        ]

    @property
    def closed_trades(self) -> list[Any]:
        return [t for t in self.trades if t.outcome != "open"]

    @property
    def win_count(self) -> int:
        return sum(1 for t in self.closed_trades if t.outcome == "win")

    @property
    def loss_count(self) -> int:
        return sum(1 for t in self.closed_trades if t.outcome == "loss")

    @property
    def win_rate(self) -> float:
        closed = len(self.closed_trades)
        return self.win_count / closed if closed else 0.0

    @property
    def avg_r(self) -> float:
        vals = [t.pnl_r for t in self.closed_trades if t.pnl_r is not None]
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def total_r(self) -> float:
        vals: list[float] = [t.pnl_r for t in self.closed_trades if t.pnl_r is not None]
        return sum(vals)

    @property
    def max_drawdown_r(self) -> float:
        return 1.0

    @property
    def long_closed_trades(self) -> list[Any]:
        return [t for t in self.closed_trades if t.direction == "long"]

    @property
    def short_closed_trades(self) -> list[Any]:
        return [t for t in self.closed_trades if t.direction == "short"]

    @property
    def long_win_count(self) -> int:
        return sum(1 for t in self.long_closed_trades if t.outcome == "win")

    @property
    def long_win_rate(self) -> float | None:
        n = len(self.long_closed_trades)
        return self.long_win_count / n if n > 0 else None

    @property
    def long_avg_r(self) -> float | None:
        vals = [t.pnl_r for t in self.long_closed_trades if t.pnl_r is not None]
        return sum(vals) / len(vals) if vals else None

    @property
    def short_win_count(self) -> int:
        return sum(1 for t in self.short_closed_trades if t.outcome == "win")

    @property
    def short_win_rate(self) -> float | None:
        n = len(self.short_closed_trades)
        return self.short_win_count / n if n > 0 else None

    @property
    def short_avg_r(self) -> float | None:
        vals = [t.pnl_r for t in self.short_closed_trades if t.pnl_r is not None]
        return sum(vals) / len(vals) if vals else None

    @property
    def long_total_r(self) -> float:
        vals: list[float] = [
            t.pnl_r for t in self.long_closed_trades if t.pnl_r is not None
        ]
        return sum(vals)

    @property
    def short_total_r(self) -> float:
        vals: list[float] = [
            t.pnl_r for t in self.short_closed_trades if t.pnl_r is not None
        ]
        return sum(vals)

    @property
    def recovery_factor(self) -> float:
        dd = self.max_drawdown_r
        return self.total_r / dd if dd > 0 else 0.0


_BT_PARAMS: dict[str, Any] = {
    "days": 90,
    "data_start_ms": 1_690_000_000_000,
    "data_end_ms": 1_700_000_000_000,
    "sl_pct": 0.02,
    "tp_r": 2.0,
    "fee_pct": 0.0,
    "day_filter": "off",
    "smt_trend_filter": 1,
    "secondary_symbol": None,
    "sweep_id": None,
}


class TestUpsertBacktestRun:
    def test_inserts_row(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        upsert_backtest_run(conn, result, **_BT_PARAMS)
        assert _one(conn, "SELECT COUNT(*) FROM backtest_runs")[0] == 1

    def test_stores_aggregate_fields(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        upsert_backtest_run(conn, result, **_BT_PARAMS)
        row = _one(
            conn,
            "SELECT symbol, timeframe, strategy, closed_trades, win_count, "
            "loss_count, win_rate, avg_r FROM backtest_runs",
        )
        assert row[0] == "BTCUSDT"
        assert row[1] == "4h"
        assert row[2] == "bos"
        assert row[3] == 2
        assert row[4] == 1
        assert row[5] == 1
        assert abs(row[6] - 0.5) < 1e-9
        assert abs(row[7] - 0.5) < 1e-9

    def test_replaces_on_same_params(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        id1 = upsert_backtest_run(conn, result, **_BT_PARAMS)
        id2 = upsert_backtest_run(conn, result, **_BT_PARAMS)
        assert id1 == id2
        assert _one(conn, "SELECT COUNT(*) FROM backtest_runs")[0] == 1

    def test_different_params_produce_different_rows(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        params_b = {**_BT_PARAMS, "sl_pct": 0.03}
        upsert_backtest_run(conn, result, **_BT_PARAMS)
        upsert_backtest_run(conn, result, **params_b)
        assert _one(conn, "SELECT COUNT(*) FROM backtest_runs")[0] == 2


class TestUpsertBacktestTrades:
    def test_inserts_trade_rows(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        run_id = upsert_backtest_run(conn, result, **_BT_PARAMS)
        upsert_backtest_trades(conn, result, run_id)
        assert _one(conn, "SELECT COUNT(*) FROM backtest_trades")[0] == 2

    def test_trade_fields_correct(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        run_id = upsert_backtest_run(conn, result, **_BT_PARAMS)
        upsert_backtest_trades(conn, result, run_id)
        row = _one(
            conn,
            "SELECT outcome, pnl_r FROM backtest_trades ORDER BY signal_time LIMIT 1",
        )
        assert row[0] == "win"
        assert abs(row[1] - 2.0) < 1e-9

    def test_empty_trades_writes_nothing(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        result.trades = []
        run_id = upsert_backtest_run(conn, result, **_BT_PARAMS)
        upsert_backtest_trades(conn, result, run_id)
        assert _one(conn, "SELECT COUNT(*) FROM backtest_trades")[0] == 0

    def test_rewrite_leaves_exactly_the_new_set(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """#949: signal times the new set dropped must not survive the rewrite."""
        old = _FakeResult("BTCUSDT", "4h", "bos")
        run_id = upsert_backtest_run(conn, old, **_BT_PARAMS)
        upsert_backtest_trades(conn, old, run_id)
        old_times = {t.signal_time for t in old.trades}

        new = _FakeResult("BTCUSDT", "4h", "bos")
        # Keep one old signal time, drop the other, add a brand-new one.
        new.trades = [
            new.trades[0],
            _FakeTrade(
                1_700_020_000_000,
                1_700_023_600_000,
                30200.0,
                "short",
                30800.0,
                29000.0,
                "win",
                2.0,
            ),
        ]
        upsert_backtest_trades(conn, new, run_id)

        stored = {
            r[0]
            for r in conn.execute(
                "SELECT signal_time FROM backtest_trades WHERE run_id = ?", [run_id]
            ).fetchall()
        }
        assert stored == {t.signal_time for t in new.trades}
        assert not (old_times - {new.trades[0].signal_time}) & stored

    def test_empty_rewrite_clears_old_rows(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        run_id = upsert_backtest_run(conn, result, **_BT_PARAMS)
        upsert_backtest_trades(conn, result, run_id)
        assert _one(conn, "SELECT COUNT(*) FROM backtest_trades")[0] == 2

        result.trades = []
        upsert_backtest_trades(conn, result, run_id)
        assert _one(conn, "SELECT COUNT(*) FROM backtest_trades")[0] == 0

    def test_other_runs_are_untouched(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        run_a = upsert_backtest_run(conn, result, **_BT_PARAMS)
        run_b = upsert_backtest_run(conn, result, **{**_BT_PARAMS, "sl_pct": 0.03})
        assert run_a != run_b
        upsert_backtest_trades(conn, result, run_a)
        upsert_backtest_trades(conn, result, run_b)

        result.trades = result.trades[:1]
        upsert_backtest_trades(conn, result, run_a)

        counts = dict(
            conn.execute(
                "SELECT run_id, COUNT(*) FROM backtest_trades GROUP BY run_id"
            ).fetchall()
        )
        assert counts == {run_a: 1, run_b: 2}

    def test_failed_insert_rolls_the_delete_back(
        self, conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        run_id = upsert_backtest_run(conn, result, **_BT_PARAMS)
        upsert_backtest_trades(conn, result, run_id)
        before = conn.execute(
            "SELECT trade_id, signal_time, pnl_r FROM backtest_trades ORDER BY 1"
        ).fetchall()

        def _broken(table: str, row: dict[str, Any], view: str) -> str:
            return f"INSERT INTO {table} (no_such_column) SELECT 1 FROM {view}"

        monkeypatch.setattr("analytics.store.backtest_runs._insert_sql", _broken)
        result.trades = result.trades[:1]
        with pytest.raises(duckdb.Error):
            upsert_backtest_trades(conn, result, run_id)

        after = conn.execute(
            "SELECT trade_id, signal_time, pnl_r FROM backtest_trades ORDER BY 1"
        ).fetchall()
        assert after == before
        # The connection is usable again (no transaction left open).
        monkeypatch.undo()
        upsert_backtest_trades(conn, result, run_id)
        assert _one(conn, "SELECT COUNT(*) FROM backtest_trades")[0] == 1

    def test_volume_flags_persist(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        result.trades[0].low_volume = True
        result.trades[0].volume_spike = False
        result.trades[1].low_volume = False
        result.trades[1].volume_spike = True
        run_id = upsert_backtest_run(conn, result, **_BT_PARAMS)
        upsert_backtest_trades(conn, result, run_id)
        rows = conn.execute(
            "SELECT low_volume, volume_spike FROM backtest_trades ORDER BY signal_time"
        ).fetchall()
        assert rows == [(True, False), (False, True)]


class TestGetWinRateByStrategy:
    def test_returns_aggregated_win_rate(self, conn: duckdb.DuckDBPyConnection) -> None:
        # Insert enough closed trades to pass the min_trades=20 gate.
        # We fake it by inserting a row directly with closed_trades=25.
        from analytics.data_store import _backtest_run_id

        run_id = _backtest_run_id(
            "BTCUSDT", "4h", "bos", 90, 0.02, 2.0, 0.0, "off", 1, None
        )
        conn.execute(
            "INSERT INTO backtest_runs (run_id, symbol, timeframe, strategy, "
            "data_start_ms, data_end_ms, days, sl_pct, tp_r, fee_pct, day_filter, "
            "smt_trend_filter, total_signals, closed_trades, win_count, loss_count, "
            "win_rate, avg_r, total_r, max_drawdown_r, run_at_ms) VALUES "
            "(?, 'BTCUSDT', '4h', 'bos', 1690000000000, 1700000000000, 90, 0.02, "
            "2.0, 0.0, 'off', 1, 25, 25, 15, 10, 0.6, 0.5, 12.5, 3.0, 1700000001000)",
            [run_id],
        )
        df = get_win_rate_by_strategy(conn)
        assert len(df) == 1
        assert df.iloc[0]["strategy"] == "bos"
        assert abs(df.iloc[0]["win_rate_pct"] - 60.0) < 0.01

    def test_excludes_low_trade_count(self, conn: duckdb.DuckDBPyConnection) -> None:
        from analytics.data_store import _backtest_run_id

        run_id = _backtest_run_id(
            "BTCUSDT", "4h", "fvg", 90, 0.02, 2.0, 0.0, "off", 1, None
        )
        conn.execute(
            "INSERT INTO backtest_runs (run_id, symbol, timeframe, strategy, "
            "data_start_ms, data_end_ms, days, sl_pct, tp_r, fee_pct, day_filter, "
            "smt_trend_filter, total_signals, closed_trades, win_count, loss_count, "
            "win_rate, avg_r, total_r, max_drawdown_r, run_at_ms) VALUES "
            "(?, 'BTCUSDT', '4h', 'fvg', 1690000000000, 1700000000000, 90, 0.02, "
            "2.0, 0.0, 'off', 1, 5, 5, 3, 2, 0.6, 0.4, 2.0, 1.0, 1700000001000)",
            [run_id],
        )
        df = get_win_rate_by_strategy(conn)
        assert df.empty


class TestConfidenceRatings:
    def test_upsert_and_get_roundtrip(self, conn: duckdb.DuckDBPyConnection) -> None:
        ratings = {"fvg": {"1h": 3, "4h": 4}, "bos": {"15m": 1}}
        win_rates = pd.DataFrame(
            [
                {"strategy": "fvg", "timeframe": "1h", "avg_r": 0.35, "win_rate": 0.55},
                {"strategy": "fvg", "timeframe": "4h", "avg_r": 0.72, "win_rate": 0.60},
                {
                    "strategy": "bos",
                    "timeframe": "15m",
                    "avg_r": -0.28,
                    "win_rate": 0.13,
                },
            ]
        )
        upsert_confidence_ratings(conn, "signal_watch", ratings, win_rates)
        result = get_confidence_ratings(conn, "signal_watch")
        assert result == {"fvg": {"1h": 3, "4h": 4}, "bos": {"15m": 1}}

    def test_returns_empty_dict_for_unknown_config(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        result = get_confidence_ratings(conn, "nonexistent_config")
        assert result == {}

    def test_configs_are_isolated(self, conn: duckdb.DuckDBPyConnection) -> None:
        ratings_a = {"fvg": {"1h": 3}}
        ratings_b = {"fvg": {"1h": 5}}
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        upsert_confidence_ratings(conn, "config_a", ratings_a, empty_wr)
        upsert_confidence_ratings(conn, "config_b", ratings_b, empty_wr)
        assert get_confidence_ratings(conn, "config_a") == {"fvg": {"1h": 3}}
        assert get_confidence_ratings(conn, "config_b") == {"fvg": {"1h": 5}}

    def test_upsert_replaces_existing(self, conn: duckdb.DuckDBPyConnection) -> None:
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        upsert_confidence_ratings(conn, "signal_watch", {"fvg": {"1h": 2}}, empty_wr)
        upsert_confidence_ratings(conn, "signal_watch", {"fvg": {"1h": 4}}, empty_wr)
        result = get_confidence_ratings(conn, "signal_watch")
        assert result["fvg"]["1h"] == 4

    def test_empty_ratings_is_noop(self, conn: duckdb.DuckDBPyConnection) -> None:
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        upsert_confidence_ratings(conn, "signal_watch", {}, empty_wr)
        assert get_confidence_ratings(conn, "signal_watch") == {}

    def test_day_filter_stored_and_joined_in_backtest_runs(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """stars should be resolved per backtest row via day_filter JOIN."""
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        upsert_confidence_ratings(
            conn,
            "signal_watch_weekdays",
            {"bos": {"4h": 4}},
            empty_wr,
            day_filter="tue_thu",
        )
        result = _FakeResult("BTCUSDT", "4h", "bos")
        upsert_backtest_run(conn, result, **{**_BT_PARAMS, "day_filter": "tue_thu"})
        df = list_backtest_runs(conn)
        assert df.iloc[0]["stars"] == 4

    def test_stars_null_when_no_matching_confidence(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        upsert_backtest_run(conn, result, **_BT_PARAMS)
        df = list_backtest_runs(conn)
        assert pd.isna(df.iloc[0]["stars"])

    def test_upsert_persists_dsr_from_map(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        win_rates = pd.DataFrame(
            [{"strategy": "fvg", "timeframe": "1h", "avg_r": 0.6, "win_rate": 0.6}]
        )
        upsert_confidence_ratings(
            conn,
            "signal_watch",
            {"fvg": {"1h": 4}},
            win_rates,
            dsr_map={"fvg": {"1h": 0.42}},
        )
        dsr = _one(
            conn,
            "SELECT dsr FROM confidence_ratings "
            "WHERE strategy = 'fvg' AND tf = '1h' AND direction = 'combined'",
        )[0]
        assert dsr == pytest.approx(0.42)

    def test_upsert_dsr_null_when_no_map(self, conn: duckdb.DuckDBPyConnection) -> None:
        win_rates = pd.DataFrame(
            [{"strategy": "fvg", "timeframe": "1h", "avg_r": 0.6, "win_rate": 0.6}]
        )
        upsert_confidence_ratings(conn, "signal_watch", {"fvg": {"1h": 4}}, win_rates)
        dsr = _one(
            conn,
            "SELECT dsr FROM confidence_ratings WHERE strategy = 'fvg' AND tf = '1h'",
        )[0]
        assert dsr is None


class TestGetLatestOpenTime:
    def test_returns_none_when_no_rows(self, conn: duckdb.DuckDBPyConnection) -> None:
        assert get_latest_open_time(conn, "BTCUSDT", "1h") is None

    def test_returns_max_open_time(self, conn: duckdb.DuckDBPyConnection) -> None:
        rows = [
            {**_OHLCV_ROW, "open_time": 1_700_000_000_000},
            {**_OHLCV_ROW, "open_time": 1_700_003_600_000},
        ]
        upsert_ohlcv(conn, pd.DataFrame(rows), venue="binance")
        assert get_latest_open_time(conn, "BTCUSDT", "1h") == 1_700_003_600_000


def _make_result(
    symbol: str = "BTCUSDT",
    tf: str = "1h",
    strategy: str = "engulfing",
) -> BacktestResult:
    """Minimal BacktestResult with 2 long wins and 1 long loss."""
    entry, sl, tp = 50000.0, 49000.0, 52000.0

    def _trade(outcome: str) -> Trade:
        exit_price = tp if outcome == "win" else sl
        return Trade(
            signal_time=1_000_000,
            entry_time=1_100_000,
            entry_price=entry,
            direction="long",
            sl_price=sl,
            tp_price=tp,
            exit_time=2_000_000,
            exit_price=exit_price,
            outcome=outcome,
        )

    return BacktestResult(
        symbol=symbol,
        timeframe=tf,
        strategy=strategy,
        trades=[_trade("win"), _trade("win"), _trade("loss")],
    )


class TestBacktestCache:
    def test_get_miss(self, conn: duckdb.DuckDBPyConnection) -> None:
        assert get_backtest_cache(conn, "nonexistent") is None

    def test_put_and_get_round_trip(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _make_result()
        put_backtest_cache(conn, "key1", "run1", 100_000, result)
        snap = get_backtest_cache(conn, "key1")
        assert snap is not None
        assert isinstance(snap, BacktestSnapshot)
        assert len(snap.closed_trades) == len(result.closed_trades)
        assert len(snap.long_closed_trades) == len(result.long_closed_trades)
        assert len(snap.short_closed_trades) == len(result.short_closed_trades)
        assert snap.win_count == result.win_count
        assert snap.win_rate == pytest.approx(result.win_rate)
        assert snap.avg_r == pytest.approx(result.avg_r)
        assert snap.long_win_rate == pytest.approx(result.long_win_rate)
        assert snap.long_avg_r == pytest.approx(result.long_avg_r)

    def test_get_miss_on_different_key(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _make_result()
        put_backtest_cache(conn, "key1", "run1", 100_000, result)
        assert get_backtest_cache(conn, "key2") is None

    def test_prune_removes_old_entries(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _make_result()
        put_backtest_cache(conn, "new_key", "run_new", 100_000, result)
        # Clone the row as "old_key" with cached_at_ms backdated 40 days.
        old_ms = int(time.time() * 1000) - 40 * 24 * 3600 * 1000
        conn.execute(
            "INSERT INTO backtest_cache "
            "SELECT 'old_key', run_id, last_candle_ts, symbol, timeframe, strategy, fee_pct, "
            "n_closed, n_long, n_short, n_win, n_loss, r_win_rate, r_avg, r_total, "
            "n_long_win, r_long_win_rate, r_long_avg, r_long_total, "
            "n_short_win, r_short_win_rate, r_short_avg, r_short_total, "
            "h_median, h_long_median, h_short_median, ? "
            "FROM backtest_cache WHERE cache_key = 'new_key'",
            [old_ms],
        )
        prune_backtest_cache(conn, keep_days=30)
        assert get_backtest_cache(conn, "old_key") is None
        assert get_backtest_cache(conn, "new_key") is not None

    def test_prune_keeps_recent_entries(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _make_result()
        put_backtest_cache(conn, "key1", "run1", 100_000, result)
        prune_backtest_cache(conn, keep_days=30)
        assert get_backtest_cache(conn, "key1") is not None

    def test_backtest_run_id_unchanged_for_defaults(self) -> None:
        old_id = _backtest_run_id(
            "BTCUSDT", "1h", "engulfing", 90, 0.02, 3.0, 0.0, "off", 1, None
        )
        new_id = _backtest_run_id(
            "BTCUSDT",
            "1h",
            "engulfing",
            90,
            0.02,
            3.0,
            0.0,
            "off",
            1,
            None,
            min_sl_pct=0.0,
            atr_sl_multiplier=None,
            tp_r_long=None,
            tp_r_short=None,
            volume_suppress_long=None,
            volume_suppress_short=None,
            adr_exempt=False,
        )
        assert old_id == new_id

    def test_backtest_run_id_changes_for_nondefault_min_sl(self) -> None:
        base = _backtest_run_id(
            "BTCUSDT", "1h", "engulfing", 90, 0.02, 3.0, 0.0, "off", 1, None
        )
        with_min_sl = _backtest_run_id(
            "BTCUSDT",
            "1h",
            "engulfing",
            90,
            0.02,
            3.0,
            0.0,
            "off",
            1,
            None,
            min_sl_pct=0.005,
        )
        assert base != with_min_sl

    def test_make_bt_cache_key_changes_with_ts(self) -> None:
        k1 = _make_bt_cache_key("run1", 100)
        k2 = _make_bt_cache_key("run1", 200)
        assert k1 != k2

    def test_make_bt_cache_key_changes_with_run_id(self) -> None:
        k1 = _make_bt_cache_key("run1", 100)
        k2 = _make_bt_cache_key("run2", 100)
        assert k1 != k2

    def test_snapshot_truthiness(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _make_result()
        put_backtest_cache(conn, "key1", "run1", 100_000, result)
        snap = get_backtest_cache(conn, "key1")
        assert snap is not None
        assert bool(snap.closed_trades)
        assert not bool(snap.short_closed_trades)


class TestBacktestRunWriterIdentity:
    """The live gate and a sweep must not share a run_id.

    `_backtest_run_id` hashed only backtest *parameters*, and `upsert_backtest_run`
    issues `INSERT OR REPLACE`. So whenever the live gate's resolved sl_pct/tp_r
    matched a swept cell's — i.e. the CHOSEN cell, the one that matters — the
    15-minute daemon replaced the swept row: 415 rows overwritten, 331 whose
    aggregate disagreed with their own trades, and 53% of rated `tue_thu` cells
    owned by the live gate. See docs/audits/2026-08-12-multi-regime-validation.md's
    sibling report and PR #604 (which contained it via save_results=false but
    healed nothing).
    """

    def test_writer_changes_the_run_id(self) -> None:
        base = _backtest_run_id(
            "BTCUSDT", "4h", "bos", 90, 0.02, 2.0, 0.0, "off", 1, None
        )
        live = _backtest_run_id(
            "BTCUSDT", "4h", "bos", 90, 0.02, 2.0, 0.0, "off", 1, None, writer="live"
        )
        assert base != live

    def test_each_writer_gets_a_distinct_namespace(self) -> None:
        ids = {
            w: _backtest_run_id(
                "BTCUSDT", "4h", "bos", 90, 0.02, 2.0, 0.0, "off", 1, None, writer=w
            )
            for w in ("sweep", "live", "single", "ui")
        }
        assert len(set(ids.values())) == 4, ids

    def test_default_writer_preserves_historical_run_ids(self) -> None:
        """The default namespace is unsuffixed ON PURPOSE.

        Sweep rows are the validated evidence and `backtest_cache` keys derive from
        run_id, so keeping the default hash byte-identical means existing sweep rows
        stay addressable and the live cache is not invalidated. These two literals
        were captured from `main` before the writer parameter existed; if they move,
        this change has silently orphaned every historical row.
        """
        assert (
            _backtest_run_id("BTCUSDT", "4h", "bos", 90, 0.02, 2.0, 0.0, "off", 1, None)
            == "5f39a1eee6b6365f"
        )
        assert (
            _backtest_run_id(
                "BTCUSDT", "15m", "eqh_eql", 365, 0.02, 2.0, 0.05, "tue_thu", 1, None
            )
            == "04c7fd503c00779f"
        )

    def test_live_writer_does_not_replace_a_sweep_row(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        sweep_params = {**_BT_PARAMS, "sweep_id": "sweep-abc"}
        sweep_id = upsert_backtest_run(conn, result, **sweep_params)
        live_id = upsert_backtest_run(conn, result, **_BT_PARAMS, writer="live")

        assert sweep_id != live_id
        assert _one(conn, "SELECT COUNT(*) FROM backtest_runs")[0] == 2
        surviving = conn.execute(
            "SELECT sweep_id FROM backtest_runs WHERE run_id = ?", [sweep_id]
        ).fetchone()
        assert surviving is not None
        assert surviving[0] == "sweep-abc", "the live gate destroyed the swept row"


class TestBacktestRunIdNamespacing:
    """ST86: every axis that changes the engine's output must namespace the row.

    ``upsert_backtest_run`` forwarded 11 of the 19 axes ``_backtest_run_id``
    accepts and knew nothing about ``live_parity`` at all, so two runs that
    differ only in a dropped axis hashed to ONE ``run_id`` and the
    ``INSERT OR REPLACE`` destroyed one. That is the ``writer`` defect (415 rows
    overwritten, 331 disagreeing with their own trades) recurring on axes nobody
    namespaced.
    """

    def test_default_run_id_is_byte_identical(self) -> None:
        """The pin: rows written before ST86 must stay addressable.

        ``backtest_cache`` keys derive from ``run_id``, so a changed default
        hash would orphan every historical sweep row and invalidate the live
        cache. Literal hex rather than a self-comparison — only a constant
        catches a change to the key *format*.
        """
        assert (
            _backtest_run_id("BTCUSDT", "4h", "bos", 90, 0.02, 2.0, 0.0, "off", 1, None)
            == "5f39a1eee6b6365f"
        )

    def test_live_parity_changes_the_run_id(self) -> None:
        from analytics.backtest.live_parity_config import (
            LiveParityConfig,
            live_parity_key,
        )

        plain = _backtest_run_id(
            "BTCUSDT", "4h", "bos", 90, 0.02, 2.0, 0.0, "off", 1, None
        )
        parity = _backtest_run_id(
            "BTCUSDT",
            "4h",
            "bos",
            90,
            0.02,
            2.0,
            0.0,
            "off",
            1,
            None,
            live_parity=live_parity_key(LiveParityConfig(regime=True)),
        )
        assert parity != plain

    def test_live_parity_gate_sets_are_distinguished(self) -> None:
        """``--live-parity --without-cooldown`` is a different book to ``--live-parity``."""
        from analytics.backtest.live_parity_config import (
            LiveParityConfig,
            live_parity_key,
        )

        all_on = live_parity_key(
            LiveParityConfig(enabled=True, regime=True, cooldown=True)
        )
        no_cooldown = live_parity_key(LiveParityConfig(enabled=True, regime=True))
        assert all_on != no_cooldown

    def test_live_parity_off_is_indistinguishable_from_absent(self) -> None:
        """A default-constructed config must not move the hash."""
        from analytics.backtest.live_parity_config import (
            LiveParityConfig,
            live_parity_key,
        )

        assert live_parity_key(LiveParityConfig()) is None
        assert live_parity_key(None) is None

    def test_cooldown_bars_are_in_the_key(self) -> None:
        from analytics.backtest.live_parity_config import (
            LiveParityConfig,
            live_parity_key,
        )

        a = live_parity_key(
            LiveParityConfig(cooldown=True, cooldown_bars_per_tf={"15m": 3})
        )
        b = live_parity_key(
            LiveParityConfig(cooldown=True, cooldown_bars_per_tf={"15m": 6})
        )
        assert a != b

    def test_cooldown_bars_key_is_order_independent(self) -> None:
        """Dict insertion order must not fork the hash for one identical config."""
        from analytics.backtest.live_parity_config import (
            LiveParityConfig,
            live_parity_key,
        )

        a = live_parity_key(
            LiveParityConfig(cooldown=True, cooldown_bars_per_tf={"15m": 3, "1h": 2})
        )
        b = live_parity_key(
            LiveParityConfig(cooldown=True, cooldown_bars_per_tf={"1h": 2, "15m": 3})
        )
        assert a == b

    @pytest.mark.parametrize(
        ("axis", "value"),
        [
            ("min_sl_pct", 0.005),
            ("atr_sl_multiplier", 1.5),
            ("tp_r_long", 3.0),
            ("tp_r_short", 1.5),
            ("volume_suppress_long", True),
            ("volume_suppress_short", True),
            ("adr_exempt", True),
            ("atr_sl_floor", True),
            ("detector_params", {"lookback": 400}),
        ],
    )
    def test_every_engine_axis_survives_the_upsert(
        self, conn: duckdb.DuckDBPyConnection, axis: str, value: Any
    ) -> None:
        """Each axis reaches the stored row_id, so two runs cannot destroy each other.

        These nine were accepted by ``_backtest_run_id`` and dropped by
        ``upsert_backtest_run``, which is the only path that WRITES a row.
        ``detector_params`` (ST104 P1) joined this list rather than being
        exempted from it.
        """
        result = _FakeResult("BTCUSDT", "4h", "bos")
        upsert_backtest_run(conn, result, **_BT_PARAMS)
        upsert_backtest_run(conn, result, **_BT_PARAMS, **{axis: value})
        assert _one(conn, "SELECT COUNT(*) FROM backtest_runs")[0] == 2

    def test_live_parity_row_survives_beside_the_plain_run(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        from analytics.backtest.live_parity_config import LiveParityConfig

        result = _FakeResult("BTCUSDT", "4h", "bos")
        upsert_backtest_run(conn, result, **_BT_PARAMS)
        upsert_backtest_run(
            conn,
            result,
            **_BT_PARAMS,
            live_parity=LiveParityConfig(enabled=True, regime=True),
        )
        assert _one(conn, "SELECT COUNT(*) FROM backtest_runs")[0] == 2

    def test_provenance_is_readable_back(self, conn: duckdb.DuckDBPyConnection) -> None:
        """ST79 asks whether a stored tp_r was measured under the live gates.

        Namespacing alone cannot answer that — the axis has to be a COLUMN.
        """
        from analytics.backtest.live_parity_config import LiveParityConfig

        result = _FakeResult("BTCUSDT", "4h", "bos")
        upsert_backtest_run(
            conn,
            result,
            **_BT_PARAMS,
            live_parity=LiveParityConfig(enabled=True, regime=True),
            adr_exempt=True,
        )
        row = _one(conn, "SELECT live_parity, adr_exempt FROM backtest_runs")
        assert row[0] is not None and "regime" in row[0]
        assert row[1] is True

    def test_plain_run_stores_null_provenance(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        upsert_backtest_run(conn, result, **_BT_PARAMS)
        row = _one(conn, "SELECT live_parity, adr_exempt FROM backtest_runs")
        assert row[0] is None
        assert row[1] is False


class TestDetectorParamsRunId:
    """ST104 P1: eqh_eql's lookback/tolerance_pct/swing_n must namespace the row_id.

    Enables saved retune backtests that cannot collide with existing rows —
    the precondition `docs/superpowers/specs/2026-08-29-st104-eqh-eql-retune-prereg.md`
    names as blocking before any arm is run.
    """

    def test_hash_unchanged_when_unset(self) -> None:
        """The pin: every historical run_id must survive this axis landing.

        Same literal `_backtest_run_id` call and hash as
        ``TestBacktestRunIdNamespacing.test_default_run_id_is_byte_identical``
        — reproduced here so an explicit ``None``/``{}`` (not just the
        implicit default) proves out too. Literal hex, not a self-comparison,
        because only a constant catches a change to the key *format* itself.
        """
        assert (
            _backtest_run_id("BTCUSDT", "4h", "bos", 90, 0.02, 2.0, 0.0, "off", 1, None)
            == "5f39a1eee6b6365f"
        )
        assert (
            _backtest_run_id(
                "BTCUSDT",
                "4h",
                "bos",
                90,
                0.02,
                2.0,
                0.0,
                "off",
                1,
                None,
                detector_params=None,
            )
            == "5f39a1eee6b6365f"
        )
        assert (
            _backtest_run_id(
                "BTCUSDT",
                "4h",
                "bos",
                90,
                0.02,
                2.0,
                0.0,
                "off",
                1,
                None,
                detector_params={},
            )
            == "5f39a1eee6b6365f"
        )

    def test_hash_changes_when_set(self) -> None:
        base = _backtest_run_id(
            "BTCUSDT", "15m", "eqh_eql", 90, 0.02, 2.0, 0.0, "off", 1, None
        )
        retuned = _backtest_run_id(
            "BTCUSDT",
            "15m",
            "eqh_eql",
            90,
            0.02,
            2.0,
            0.0,
            "off",
            1,
            None,
            detector_params={"lookback": 400},
        )
        assert base != retuned

    def test_hash_is_order_independent_over_dict_insertion(self) -> None:
        """Keys are sorted before hashing, so insertion order cannot fork it."""
        a = _backtest_run_id(
            "BTCUSDT",
            "15m",
            "eqh_eql",
            90,
            0.02,
            2.0,
            0.0,
            "off",
            1,
            None,
            detector_params={"lookback": 400, "tolerance_pct": 0.00075},
        )
        b = _backtest_run_id(
            "BTCUSDT",
            "15m",
            "eqh_eql",
            90,
            0.02,
            2.0,
            0.0,
            "off",
            1,
            None,
            detector_params={"tolerance_pct": 0.00075, "lookback": 400},
        )
        assert a == b

    def test_each_param_value_moves_the_hash(self) -> None:
        base = _backtest_run_id(
            "BTCUSDT",
            "15m",
            "eqh_eql",
            90,
            0.02,
            2.0,
            0.0,
            "off",
            1,
            None,
            detector_params={"lookback": 400},
        )
        different = _backtest_run_id(
            "BTCUSDT",
            "15m",
            "eqh_eql",
            90,
            0.02,
            2.0,
            0.0,
            "off",
            1,
            None,
            detector_params={"lookback": 401},
        )
        assert base != different

    def test_bool_value_is_rejected(self) -> None:
        """``bool`` is an ``int`` subclass — an unguarded check would accept it silently."""
        with pytest.raises(TypeError):
            _backtest_run_id(
                "BTCUSDT",
                "15m",
                "eqh_eql",
                90,
                0.02,
                2.0,
                0.0,
                "off",
                1,
                None,
                detector_params={"swing_n": True},
            )

    def test_stored_as_sorted_key_json_and_read_back_verbatim(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        result = _FakeResult("BTCUSDT", "15m", "eqh_eql")
        upsert_backtest_run(
            conn,
            result,
            **_BT_PARAMS,
            detector_params={"swing_n": 2, "lookback": 400},
        )
        row = _one(conn, "SELECT detector_params FROM backtest_runs")
        assert row[0] == '{"lookback": 400, "swing_n": 2}'

    def test_null_when_unset(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _FakeResult("BTCUSDT", "15m", "eqh_eql")
        upsert_backtest_run(conn, result, **_BT_PARAMS)
        row = _one(conn, "SELECT detector_params FROM backtest_runs")
        assert row[0] is None


class TestUpsertColumnMapping:
    """The stored row must land in the columns it names, on ANY database.

    ``INSERT ... SELECT`` maps positionally, and ``backtest_runs`` does not have
    one column order: ``long_total_r`` / ``short_total_r`` / ``volume_suppress``
    are created inline while ``adr_suppress_threshold`` / ``recovery_factor``
    arrive through the ALTER migration. Measured 2026-08-25 on a fresh DB, an
    ``adr_suppress_threshold`` of 0.8 was read back out of ``long_total_r``.
    Production was correct — its order predates the CREATE — so this was
    invisible everywhere a gate could see it: CI, every in-memory test DB and
    every ``make preflight`` clone were the wrong half.
    """

    def test_five_migrated_columns_round_trip(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        params = dict(_BT_PARAMS)
        params["adr_suppress_threshold"] = 0.8
        params["volume_suppress"] = True
        upsert_backtest_run(conn, result, **params)
        row = _one(
            conn,
            "SELECT adr_suppress_threshold, volume_suppress, long_total_r, "
            "short_total_r, recovery_factor FROM backtest_runs",
        )
        assert abs(float(row[0]) - 0.8) < 1e-6
        assert row[1] is True
        assert abs(float(row[2]) - result.long_total_r) < 1e-6
        assert abs(float(row[3]) - result.short_total_r) < 1e-6
        assert abs(float(row[4]) - result.recovery_factor) < 1e-6

    def test_trade_rows_round_trip_by_name(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        run_id = upsert_backtest_run(conn, result, **_BT_PARAMS)
        upsert_backtest_trades(conn, result, run_id)
        row = _one(
            conn,
            "SELECT symbol, timeframe, strategy, direction, outcome "
            "FROM backtest_trades ORDER BY signal_time LIMIT 1",
        )
        assert row[0] == "BTCUSDT"
        assert row[1] == "4h"
        assert row[2] == "bos"
        assert row[3] in ("long", "short")


class TestListRunsPartitionAcrossTheMigration:
    """The partition must survive the NULL→FALSE boundary the migration creates.

    Every row written before ST86 has ``adr_exempt`` NULL, because the column was
    added empty; every row written after stores FALSE. A window partition treats
    NULL and FALSE as different groups, so a partition on the raw column returns
    the SAME cell twice — once from each era — instead of the latest. Uniform
    NULL today is exactly why no gate catches it: the defect only appears once
    the data crosses the boundary.
    """

    def _legacy_row(self, conn: duckdb.DuckDBPyConnection, run_id: str) -> None:
        """A pre-ST86 row: adr_exempt and live_parity never written, so NULL."""
        conn.execute(
            "INSERT INTO backtest_runs (run_id, symbol, timeframe, strategy, "
            "data_start_ms, data_end_ms, days, sl_pct, tp_r, fee_pct, day_filter, "
            "smt_trend_filter, total_signals, closed_trades, win_count, loss_count, "
            "win_rate, avg_r, total_r, max_drawdown_r, run_at_ms) VALUES "
            "(?, 'BTCUSDT', '4h', 'bos', 1690000000000, 1700000000000, 90, 0.02, "
            "2.0, 0.0, 'off', 1, 20, 20, 12, 8, 0.6, 0.4, 8.0, 4.0, 1000)",
            [run_id],
        )

    def test_one_row_per_cell_across_the_boundary(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        self._legacy_row(conn, "legacy-bos-4h")
        # A post-ST86 write for the same cell: adr_exempt stores FALSE, not NULL.
        upsert_backtest_run(conn, _FakeResult("BTCUSDT", "4h", "bos"), **_BT_PARAMS)
        df = list_backtest_runs(conn)
        assert len(df) == 1, (
            "the same cell appeared once per adr_exempt era — a partition on the "
            "raw column splits NULL from FALSE"
        )

    def test_a_parity_run_still_gets_its_own_row(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """Tolerating NULL must not collapse the axis it was added to separate.

        `live_parity` needs no COALESCE — a legacy row and a non-parity row are
        both NULL and already share a partition — and a parity run is a different
        book, so it must NOT be folded into the same one.
        """
        from analytics.backtest.live_parity_config import LiveParityConfig

        self._legacy_row(conn, "legacy-bos-4h")
        upsert_backtest_run(
            conn,
            _FakeResult("BTCUSDT", "4h", "bos"),
            **_BT_PARAMS,
            live_parity=LiveParityConfig(enabled=True, regime=True),
        )
        assert len(list_backtest_runs(conn)) == 2

    def test_a_detector_params_run_still_gets_its_own_row(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """ST104 P1: same failure mode as the parity row above, new axis.

        `detector_params` joins the partition for the identical reason
        `live_parity` does — a retuned study row and the default-param row
        no longer collide on `run_id` once namespaced, so without this the
        newer of the two silently hides the other from the listing.
        """
        upsert_backtest_run(conn, _FakeResult("BTCUSDT", "4h", "bos"), **_BT_PARAMS)
        upsert_backtest_run(
            conn,
            _FakeResult("BTCUSDT", "4h", "bos"),
            **_BT_PARAMS,
            detector_params={"lookback": 400},
        )
        assert len(list_backtest_runs(conn)) == 2

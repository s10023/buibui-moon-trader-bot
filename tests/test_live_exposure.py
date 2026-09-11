"""Live alert frequency per swept cell — ST134 section 5 item 2."""

import duckdb
import pytest

from analytics.live_exposure import alerts_per_week

_MS_PER_DAY = 86_400_000
_WEEK = 7 * _MS_PER_DAY


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE signal_alert_outcomes (
            signal_id TEXT PRIMARY KEY,
            symbol TEXT, tf TEXT, strategy TEXT, direction TEXT,
            fired_at_ms BIGINT
        )
    """)
    return conn


def _fire(conn: duckdb.DuckDBPyConnection, sid: str, ts: int, **kw: str) -> None:
    row = {"symbol": "BTCUSDT", "tf": "15m", "strategy": "bos", "direction": "long"}
    row.update(kw)
    conn.execute(
        "INSERT INTO signal_alert_outcomes VALUES (?, ?, ?, ?, ?, ?)",
        [sid, row["symbol"], row["tf"], row["strategy"], row["direction"], ts],
    )


class TestAlertsPerWeek:
    def test_counts_only_the_named_cell(self) -> None:
        conn = _conn()
        _fire(conn, "a", 0)
        _fire(conn, "b", _MS_PER_DAY)
        _fire(conn, "c", 2 * _MS_PER_DAY, strategy="fvg")
        _fire(conn, "d", 3 * _MS_PER_DAY, tf="1h")
        _fire(conn, "e", 4 * _MS_PER_DAY, symbol="ETHUSDT")
        rate = alerts_per_week(
            conn,
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=0,
            now_ms=_WEEK,
        )
        assert rate == pytest.approx(2.0)

    def test_scales_to_a_weekly_rate(self) -> None:
        conn = _conn()
        for i in range(8):
            _fire(conn, f"s{i}", i * _MS_PER_DAY)
        rate = alerts_per_week(
            conn,
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=0,
            now_ms=4 * _WEEK,
        )
        assert rate == pytest.approx(2.0)

    def test_excludes_fires_outside_the_window(self) -> None:
        conn = _conn()
        _fire(conn, "old", -_WEEK)
        _fire(conn, "in", _MS_PER_DAY)
        rate = alerts_per_week(
            conn,
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=0,
            now_ms=_WEEK,
        )
        assert rate == pytest.approx(1.0)

    def test_no_fires_is_zero_not_none(self) -> None:
        rate = alerts_per_week(
            _conn(),
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=0,
            now_ms=_WEEK,
        )
        assert rate == 0.0

    def test_zero_length_window_is_zero_not_a_division_error(self) -> None:
        conn = _conn()
        _fire(conn, "a", 0)
        rate = alerts_per_week(
            conn,
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=100,
            now_ms=100,
        )
        assert rate == 0.0

    def test_reads_tf_not_timeframe(self) -> None:
        """Mutation guard: backtest_runs uses `timeframe`, this ledger uses `tf`.

        A query copied from the backtest side returns nothing and reads as a cell
        that never fires — silent, and in the direction that hides exposure.
        """
        conn = _conn()
        _fire(conn, "a", 0, tf="4h")
        assert alerts_per_week(
            conn,
            symbol="BTCUSDT",
            timeframe="4h",
            strategy="bos",
            since_ms=0,
            now_ms=_WEEK,
        ) == pytest.approx(1.0)

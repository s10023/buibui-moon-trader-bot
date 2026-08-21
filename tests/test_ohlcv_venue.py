"""Venue-keyed ohlcv: order parsing, view generation, and read behaviour."""

import duckdb
import pytest

from analytics.store.venue import (
    DEFAULT_VENUE,
    ohlcv_view_sql,
    parse_venue_order,
    read_venue_order,
    set_read_venue_order,
)


class TestParseVenueOrder:
    def test_single_venue(self) -> None:
        assert parse_venue_order("binance") == ["binance"]

    def test_comma_separated_is_ordered_and_normalised(self) -> None:
        assert parse_venue_order(" OKX , binance ") == ["okx", "binance"]

    def test_empty_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            parse_venue_order("  ,  ")

    def test_duplicates_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            parse_venue_order("okx,okx")

    def test_injection_is_rejected(self) -> None:
        # Venues are interpolated into generated SQL, so this is a security boundary,
        # not a tidiness rule.
        with pytest.raises(ValueError, match="invalid venue"):
            parse_venue_order("binance'; DELETE FROM ohlcv_all; --")


def _conn_with_rows() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE ohlcv_all (venue TEXT NOT NULL, symbol TEXT NOT NULL, "
        "timeframe TEXT NOT NULL, open_time BIGINT NOT NULL, open DOUBLE, high DOUBLE, "
        "low DOUBLE, close DOUBLE, volume DOUBLE, taker_buy_volume DOUBLE, "
        "PRIMARY KEY (venue, symbol, timeframe, open_time))"
    )
    rows = [
        ("binance", "BTCUSDT", "1h", 1, 10.0, 11.0, 9.0, 10.5, 100.0, 50.0),
        ("binance", "BTCUSDT", "1h", 2, 10.0, 11.0, 9.0, 20.5, 100.0, 50.0),
        ("okx", "BTCUSDT", "1h", 2, 10.0, 11.0, 9.0, 99.0, 100.0, None),
        ("kraken", "BTCUSDT", "1h", 3, 10.0, 11.0, 9.0, 77.0, 100.0, None),
    ]
    for row in rows:
        conn.execute("INSERT INTO ohlcv_all VALUES (?,?,?,?,?,?,?,?,?,?)", list(row))
    return conn


class TestOhlcvViewSql:
    def test_single_venue_order_returns_only_that_venue(self) -> None:
        conn = _conn_with_rows()
        conn.execute(ohlcv_view_sql(["binance"]))
        got = conn.execute(
            "SELECT open_time, close FROM ohlcv ORDER BY open_time"
        ).fetchall()
        assert got == [(1, 10.5), (2, 20.5)]

    def test_preference_order_prefers_first_and_falls_back(self) -> None:
        # The CI shape: OKX wins where it has a bar, Binance fills every gap.
        conn = _conn_with_rows()
        conn.execute(ohlcv_view_sql(["okx", "binance"]))
        got = conn.execute(
            "SELECT open_time, close FROM ohlcv ORDER BY open_time"
        ).fetchall()
        assert got == [(1, 10.5), (2, 99.0)]

    def test_venue_outside_the_order_is_invisible(self) -> None:
        conn = _conn_with_rows()
        conn.execute(ohlcv_view_sql(["okx", "binance"]))
        opens = [r[0] for r in conn.execute("SELECT open_time FROM ohlcv").fetchall()]
        assert 3 not in opens, (
            "a venue absent from the order must never shadow a known one"
        )

    def test_view_exposes_exactly_the_nine_ohlcv_columns(self) -> None:
        conn = _conn_with_rows()
        conn.execute(ohlcv_view_sql(["binance"]))
        cols = [d[0] for d in conn.execute("SELECT * FROM ohlcv LIMIT 0").description]
        assert cols == [
            "symbol",
            "timeframe",
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "taker_buy_volume",
        ]

    def test_empty_order_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            ohlcv_view_sql([])


class TestReadVenueOrder:
    def test_missing_db_meta_defaults_to_binance(self) -> None:
        conn = duckdb.connect(":memory:")
        conn.execute(
            "CREATE TABLE db_meta (key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (key))"
        )
        assert read_venue_order(conn) == [DEFAULT_VENUE]

    def test_round_trips_through_db_meta(self) -> None:
        conn = duckdb.connect(":memory:")
        conn.execute(
            "CREATE TABLE db_meta (key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (key))"
        )
        set_read_venue_order(conn, ["okx", "binance"])
        assert read_venue_order(conn) == ["okx", "binance"]

    def test_set_is_idempotent(self) -> None:
        conn = duckdb.connect(":memory:")
        conn.execute(
            "CREATE TABLE db_meta (key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (key))"
        )
        set_read_venue_order(conn, ["okx", "binance"])
        set_read_venue_order(conn, ["okx", "binance"])
        rows = conn.execute("SELECT COUNT(*) FROM db_meta").fetchone()
        assert rows is not None and rows[0] == 1

    def test_set_with_only_db_meta_does_not_raise(self) -> None:
        # RULING R2, half 2: a DB with no ohlcv_all table yet (the unit-test shape,
        # and every DB before the venue migration runs) must not fail just because
        # the view has nothing to be built against.
        conn = duckdb.connect(":memory:")
        conn.execute(
            "CREATE TABLE db_meta (key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (key))"
        )
        set_read_venue_order(conn, ["okx", "binance"])  # must not raise
        assert read_venue_order(conn) == ["okx", "binance"]

    def test_set_rebuilds_the_view_when_ohlcv_all_exists(self) -> None:
        # RULING R2, half 1: a stored order and a live view must never disagree —
        # a later task stamping a new order onto a DB whose view was built from the
        # old one would otherwise silently diverge from what reads actually see.
        conn = _conn_with_rows()
        conn.execute(
            "CREATE TABLE db_meta (key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (key))"
        )
        conn.execute(ohlcv_view_sql(["binance"]))
        got_before = conn.execute(
            "SELECT open_time, close FROM ohlcv ORDER BY open_time"
        ).fetchall()
        assert got_before == [(1, 10.5), (2, 20.5)]

        set_read_venue_order(conn, ["okx", "binance"])

        got_after = conn.execute(
            "SELECT open_time, close FROM ohlcv ORDER BY open_time"
        ).fetchall()
        assert got_after == [(1, 10.5), (2, 99.0)]


class TestVenueCollision:
    """The whole point of ST60(b). Read this test first."""

    def test_okx_write_cannot_overwrite_a_binance_bar(self) -> None:
        import pandas as pd

        from analytics.store import init_schema, upsert_ohlcv

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        bar = {
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "open_time": 1,
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 100.0,
            "taker_buy_volume": 50.0,
        }
        upsert_ohlcv(conn, pd.DataFrame([bar]), venue="binance")
        upsert_ohlcv(
            conn,
            pd.DataFrame([{**bar, "close": 99.0, "taker_buy_volume": None}]),
            venue="okx",
        )

        both = conn.execute(
            "SELECT venue, close FROM ohlcv_all ORDER BY venue"
        ).fetchall()
        assert both == [("binance", 10.5), ("okx", 99.0)], "both venues must survive"

        through_view = conn.execute(
            "SELECT close, taker_buy_volume FROM ohlcv"
        ).fetchall()
        assert through_view == [(10.5, 50.0)], (
            "reads stay on Binance, taker split intact"
        )

    def test_fresh_schema_defaults_to_binance_only(self) -> None:
        from analytics.store import init_schema
        from analytics.store.venue import read_venue_order

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        assert read_venue_order(conn) == ["binance"]


class TestUnmigratedDatabaseGuard:
    def _legacy_conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        conn.execute(
            "CREATE TABLE ohlcv (symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
            "open_time BIGINT NOT NULL, open DOUBLE, high DOUBLE, low DOUBLE, "
            "close DOUBLE, volume DOUBLE, taker_buy_volume DOUBLE, "
            "PRIMARY KEY (symbol, timeframe, open_time))"
        )
        return conn

    def test_legacy_table_raises_and_names_the_tool(self) -> None:
        from analytics.store.schema import UnmigratedDatabaseError, init_schema

        conn = self._legacy_conn()
        with pytest.raises(UnmigratedDatabaseError) as excinfo:
            init_schema(conn)
        assert "tools/migrate_ohlcv_venue.py" in str(excinfo.value)

    def test_half_initialised_db_still_raises(self) -> None:
        # This is the state a FAILED init_schema run leaves behind on the real
        # analytics.db: `ohlcv` is still a legacy TABLE, but the CREATE TABLE IF NOT
        # EXISTS statements for `ohlcv_all` and `db_meta` already succeeded before
        # execution reached the CREATE OR REPLACE VIEW that raised. So after the
        # first failed run, BOTH `ohlcv` and `ohlcv_all` are present — a guard keyed
        # on "ohlcv_all absent" (the brief's original condition) stays silent here
        # and lets the same raw duckdb.CatalogException through on retry. The guard
        # must key on `ohlcv` alone.
        conn = self._legacy_conn()
        conn.execute(
            "CREATE TABLE ohlcv_all (venue TEXT NOT NULL, symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL, open_time BIGINT NOT NULL, open DOUBLE, "
            "high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE, "
            "taker_buy_volume DOUBLE, "
            "PRIMARY KEY (venue, symbol, timeframe, open_time))"
        )
        conn.execute(
            "CREATE TABLE db_meta (key TEXT NOT NULL, value TEXT NOT NULL, "
            "PRIMARY KEY (key))"
        )

        from analytics.store.schema import UnmigratedDatabaseError, init_schema

        with pytest.raises(UnmigratedDatabaseError) as excinfo:
            init_schema(conn)
        assert "tools/migrate_ohlcv_venue.py" in str(excinfo.value)

    def test_guard_reads_duckdb_tables_not_information_schema(self) -> None:
        # A VIEW appears in information_schema.columns but NOT in duckdb_tables().
        # Keying the guard on information_schema would make a MIGRATED database look
        # unmigrated forever, so this pins the discriminator itself.
        from analytics.store import init_schema

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        init_schema(conn)  # second call on a migrated DB must be a no-op, not a raise

        tables = {
            r[0]
            for r in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
        }
        assert "ohlcv" not in tables, "ohlcv must be a view, not a table"
        infoschema = {
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.columns WHERE table_name='ohlcv'"
            ).fetchall()
        }
        assert infoschema == {"ohlcv"}, "the view IS visible in information_schema"

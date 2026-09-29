"""Venue-keyed ohlcv: order parsing, view generation, and read behaviour."""

import os
import time
from typing import Any

import duckdb
import pytest

from analytics import data_fetcher
from analytics.store.venue import (
    DEFAULT_VENUE,
    OHLCV_COLUMNS,
    ohlcv_view_sql,
    parse_venue_order,
    read_venue_order,
    set_read_venue_order,
)


class TestOhlcvColumnsAgree:
    def test_venue_form_is_derived_from_data_fetcher_form(self) -> None:
        # Two public constants, same name, different types, same nine columns that
        # must stay in the same order: analytics.data_fetcher.OHLCV_COLUMNS
        # (list[str], pre-existing) and analytics.store.venue.OHLCV_COLUMNS (str,
        # this migration). tools/migrate_ohlcv_venue.py derives its legacy-schema
        # guard from the venue.py form; tests/test_analytics_runner.py imports the
        # data_fetcher form. Nothing else pins them together.
        assert ", ".join(data_fetcher.OHLCV_COLUMNS) == OHLCV_COLUMNS


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

    def test_injection_is_rejected_single_venue(self) -> None:
        # `ohlcv_view_sql` is a second, independent entry point into generated SQL —
        # DuckDB cannot parameterise a view body, so this validation loop is not
        # covered merely by `parse_venue_order` having its own guard.
        with pytest.raises(ValueError, match="invalid venue"):
            ohlcv_view_sql(["binance'; DELETE FROM ohlcv_all; --"])

    def test_injection_is_rejected_multi_venue(self) -> None:
        # The multi-venue path interpolates a `CASE` arm per venue as well as the
        # `IN` list, so it needs its own coverage of the injection guard.
        with pytest.raises(ValueError, match="invalid venue"):
            ohlcv_view_sql(["binance", "okx'; DELETE FROM ohlcv_all; --"])

    def test_duplicate_venue_is_rejected(self) -> None:
        # Without this, ["binance", "binance"] silently built a CASE with an
        # unreachable arm rather than failing.
        with pytest.raises(ValueError, match="duplicate"):
            ohlcv_view_sql(["binance", "binance"])

    def test_trailing_newline_is_rejected(self) -> None:
        # `$` in the old `^[a-z0-9_]+$` pattern matches immediately before a
        # trailing "\n", so `re.match` (rather than `fullmatch`) would silently
        # accept "binance\n" here — `ohlcv_view_sql` does not strip its input the
        # way `parse_venue_order` does, so this is the path that actually exercises
        # the hole.
        with pytest.raises(ValueError, match="invalid venue"):
            ohlcv_view_sql(["binance\n"])


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


# --- the one-shot migration tool (tools/migrate_ohlcv_venue.py) -------------------

_LEGACY_DDL = (
    "CREATE TABLE ohlcv (symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
    "open_time BIGINT NOT NULL, open DOUBLE NOT NULL, high DOUBLE NOT NULL, "
    "low DOUBLE NOT NULL, close DOUBLE NOT NULL, volume DOUBLE NOT NULL, "
    "taker_buy_volume DOUBLE, PRIMARY KEY (symbol, timeframe, open_time))"
)

_OHLCV_ALL_DDL = (
    "CREATE TABLE ohlcv_all (venue TEXT NOT NULL, symbol TEXT NOT NULL, "
    "timeframe TEXT NOT NULL, open_time BIGINT NOT NULL, open DOUBLE NOT NULL, "
    "high DOUBLE NOT NULL, low DOUBLE NOT NULL, close DOUBLE NOT NULL, "
    "volume DOUBLE NOT NULL, taker_buy_volume DOUBLE, "
    "PRIMARY KEY (venue, symbol, timeframe, open_time))"
)

_DB_META_DDL = (
    "CREATE TABLE db_meta (key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (key))"
)


def _legacy_db(tmp_path: Any, rows: int = 5) -> Any:
    """A pre-ST60(b) database: `ohlcv` is a TABLE, and nothing else exists."""
    path = tmp_path / "legacy.duckdb"
    conn = duckdb.connect(str(path))
    conn.execute(_LEGACY_DDL)
    for i in range(1, rows + 1):
        conn.execute(
            "INSERT INTO ohlcv VALUES ('BTCUSDT','1h',?,10,11,9,?,100,50)",
            [i, 10.0 + i],
        )
    conn.close()
    return path


def _half_initialised_db(tmp_path: Any, rows: int = 5) -> Any:
    """The REAL production state on 2026-08-21: legacy `ohlcv` + EMPTY leftovers.

    `init_schema` created `ohlcv_all` and `db_meta` (both IF NOT EXISTS) before the
    `CREATE OR REPLACE VIEW` that fails against a legacy `ohlcv` table, and the
    15-minute timer ran that repeatedly. Built by hand rather than by calling
    `init_schema`, which now raises before creating anything.
    """
    path = _legacy_db(tmp_path, rows)
    conn = duckdb.connect(str(path))
    conn.execute(_OHLCV_ALL_DDL)
    conn.execute(_DB_META_DDL)
    conn.close()
    return path


def _table_names(path: Any) -> set[str]:
    conn = duckdb.connect(str(path), read_only=True)
    try:
        return {
            r[0]
            for r in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
        }
    finally:
        conn.close()


def _count(path: Any, relation: str) -> int:
    conn = duckdb.connect(str(path), read_only=True)
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {relation}").fetchone()
        return int(row[0]) if row else -1
    finally:
        conn.close()


def _write_manifest(
    root: Any, stamp: str = "2026-08-21", age_hours: float = 0.0
) -> Any:
    """Write a snapshot manifest shaped like deploy/backup-analytics.sh's."""
    snapshot = root / "daily" / stamp
    snapshot.mkdir(parents=True, exist_ok=True)
    manifest = snapshot / "MANIFEST.json"
    manifest.write_text('{"captured_at_utc": "x", "row_counts": {}}', encoding="utf-8")
    when = time.time() - age_hours * 3600.0
    os.utime(manifest, (when, when))
    return manifest


class TestBackupFreshness:
    def test_missing_root_reads_none(self, tmp_path: Any) -> None:
        from tools.migrate_ohlcv_venue import newest_backup_age_hours

        assert newest_backup_age_hours(tmp_path / "nope") is None

    def test_root_without_manifests_reads_none(self, tmp_path: Any) -> None:
        from tools.migrate_ohlcv_venue import newest_backup_age_hours

        (tmp_path / "daily" / "2026-08-21").mkdir(parents=True)
        assert newest_backup_age_hours(tmp_path) is None

    def test_fresh_manifest_reads_near_zero(self, tmp_path: Any) -> None:
        from tools.migrate_ohlcv_venue import newest_backup_age_hours

        _write_manifest(tmp_path)
        age = newest_backup_age_hours(tmp_path)
        assert age is not None and age < 0.1

    def test_the_newest_manifest_wins(self, tmp_path: Any) -> None:
        # A stale snapshot sitting beside a fresh one must not veto the fresh one.
        from tools.migrate_ohlcv_venue import newest_backup_age_hours

        _write_manifest(tmp_path, stamp="2026-08-01", age_hours=480.0)
        _write_manifest(tmp_path, stamp="2026-08-21", age_hours=2.0)
        age = newest_backup_age_hours(tmp_path)
        assert age is not None and 1.9 < age < 2.1


class TestMigration:
    @pytest.fixture(autouse=True)
    def _isolated_backup_root(self, tmp_path: Any, monkeypatch: Any) -> None:
        # No test may ever read the operator's real ~/backups/buibui tree.
        monkeypatch.setenv("BUIBUI_BACKUP_ROOT", str(tmp_path / "backups"))

    def test_migrates_every_row_under_binance(self, tmp_path: Any) -> None:
        from tools.migrate_ohlcv_venue import migrate

        path = _legacy_db(tmp_path)
        before, after = migrate(path, force=True)
        assert (before, after) == (5, 5)

        conn = duckdb.connect(str(path), read_only=True)
        assert conn.execute("SELECT DISTINCT venue FROM ohlcv_all").fetchall() == [
            ("binance",)
        ]
        conn.close()
        tables = _table_names(path)
        assert "ohlcv" not in tables and "ohlcv_all" in tables

    def test_reads_through_the_view_are_unchanged_by_migrating(
        self, tmp_path: Any
    ) -> None:
        from tools.migrate_ohlcv_venue import migrate

        path = _legacy_db(tmp_path)
        conn = duckdb.connect(str(path))
        before_rows = conn.execute(
            "SELECT symbol, timeframe, open_time, open, high, low, close, volume, "
            "taker_buy_volume FROM ohlcv ORDER BY open_time"
        ).fetchall()
        conn.close()

        migrate(path, force=True)

        conn = duckdb.connect(str(path), read_only=True)
        after_rows = conn.execute(
            "SELECT symbol, timeframe, open_time, open, high, low, close, volume, "
            "taker_buy_volume FROM ohlcv ORDER BY open_time"
        ).fetchall()
        conn.close()
        assert after_rows == before_rows

    def test_half_initialised_database_migrates(self, tmp_path: Any) -> None:
        # The real 2026-08-21 production state: legacy `ohlcv` with rows, plus an
        # EMPTY `ohlcv_all` and an EMPTY `db_meta` left by failed init_schema runs.
        from tools.migrate_ohlcv_venue import migrate

        path = _half_initialised_db(tmp_path)
        conn = duckdb.connect(str(path))
        before_rows = conn.execute(
            "SELECT symbol, timeframe, open_time, close FROM ohlcv ORDER BY open_time"
        ).fetchall()
        conn.close()

        before, after = migrate(path, force=True)
        assert (before, after) == (5, 5)

        conn = duckdb.connect(str(path), read_only=True)
        assert (
            conn.execute(
                "SELECT symbol, timeframe, open_time, close FROM ohlcv "
                "ORDER BY open_time"
            ).fetchall()
            == before_rows
        )
        assert conn.execute("SELECT DISTINCT venue FROM ohlcv_all").fetchall() == [
            ("binance",)
        ]
        conn.close()
        assert "ohlcv" not in _table_names(path)

    def test_refuses_when_ohlcv_all_already_holds_rows(self, tmp_path: Any) -> None:
        # Nobody designed this state: a legacy table AND a populated venue table.
        # Refuse, and change nothing -- either side could be the real data.
        from tools.migrate_ohlcv_venue import PopulatedVenueTableError, migrate

        path = _half_initialised_db(tmp_path)
        conn = duckdb.connect(str(path))
        conn.execute(
            "INSERT INTO ohlcv_all VALUES ('okx','ETHUSDT','1h',9,1,1,1,1,1,NULL)"
        )
        conn.close()

        with pytest.raises(PopulatedVenueTableError, match="ohlcv_all"):
            migrate(path, force=True)

        assert _table_names(path) == {"ohlcv", "ohlcv_all", "db_meta"}
        assert _count(path, "ohlcv") == 5
        assert _count(path, "ohlcv_all") == 1

    def test_second_run_is_a_no_op(self, tmp_path: Any) -> None:
        from tools.migrate_ohlcv_venue import AlreadyMigratedError, migrate

        path = _legacy_db(tmp_path)
        migrate(path, force=True)
        with pytest.raises(AlreadyMigratedError):
            migrate(path, force=True)
        # ...and the migrated database is intact.
        assert _count(path, "ohlcv_all") == 5

    def test_refuses_without_a_fresh_backup(
        self, tmp_path: Any, monkeypatch: Any
    ) -> None:
        from tools.migrate_ohlcv_venue import StaleBackupError, migrate

        path = _legacy_db(tmp_path)
        monkeypatch.setenv("BUIBUI_BACKUP_ROOT", str(tmp_path / "no-backups"))
        with pytest.raises(StaleBackupError, match="backup"):
            migrate(path, force=False)
        # and the database is untouched
        tables = _table_names(path)
        assert "ohlcv" in tables and "ohlcv_all" not in tables
        assert _count(path, "ohlcv") == 5

    def test_refuses_a_stale_backup(self, tmp_path: Any, monkeypatch: Any) -> None:
        from tools.migrate_ohlcv_venue import StaleBackupError, migrate

        path = _legacy_db(tmp_path)
        root = tmp_path / "backups"
        _write_manifest(root, age_hours=48.0)
        monkeypatch.setenv("BUIBUI_BACKUP_ROOT", str(root))
        with pytest.raises(StaleBackupError, match="48"):
            migrate(path, force=False)
        assert "ohlcv" in _table_names(path)

    def test_a_fresh_backup_permits_the_migration(
        self, tmp_path: Any, monkeypatch: Any
    ) -> None:
        from tools.migrate_ohlcv_venue import migrate

        path = _legacy_db(tmp_path)
        root = tmp_path / "backups"
        _write_manifest(root, age_hours=1.0)
        monkeypatch.setenv("BUIBUI_BACKUP_ROOT", str(root))
        assert migrate(path, force=False) == (5, 5)

    def test_stamps_the_read_venue_order(self, tmp_path: Any) -> None:
        from tools.migrate_ohlcv_venue import migrate

        path = _legacy_db(tmp_path)
        migrate(path, force=True)
        conn = duckdb.connect(str(path), read_only=True)
        try:
            # The db_meta ROW, not just read_venue_order(): that helper returns the
            # default when the key is absent, so asserting on it alone passes with
            # the stamp removed entirely (measured -- it did).
            assert conn.execute(
                "SELECT value FROM db_meta WHERE key = 'read_venue_order'"
            ).fetchone() == (DEFAULT_VENUE,)
            assert read_venue_order(conn) == [DEFAULT_VENUE]
        finally:
            conn.close()

    def test_a_row_count_mismatch_rolls_everything_back(
        self, tmp_path: Any, monkeypatch: Any
    ) -> None:
        # Fault injection: a copy that silently drops rows. The real copy cannot do
        # this (one venue, no key collisions), so the guard is unreachable without
        # breaking it on purpose -- which is exactly what makes it worth pinning.
        import tools.migrate_ohlcv_venue as mod

        monkeypatch.setattr(mod, "_COPY_SQL", mod._COPY_SQL + " WHERE open_time <= 3")
        path = _legacy_db(tmp_path)
        with pytest.raises(mod.RowCountMismatchError, match="5"):
            mod.migrate(path, force=True)

        tables = _table_names(path)
        assert "ohlcv" in tables and "ohlcv_all" not in tables
        assert _count(path, "ohlcv") == 5

    def test_a_failure_after_the_legacy_table_is_gone_changes_nothing(
        self, tmp_path: Any, monkeypatch: Any
    ) -> None:
        # The dangerous window: the legacy table has been dropped and the view is
        # not built yet. DuckDB DDL is transactional, so the rollback must restore
        # BOTH the legacy table and the empty `ohlcv_all` leftover this run removed.
        import tools.migrate_ohlcv_venue as mod

        def _boom(order: list[str]) -> str:
            raise RuntimeError("injected failure while building the view")

        monkeypatch.setattr(mod, "ohlcv_view_sql", _boom)
        path = _half_initialised_db(tmp_path)
        with pytest.raises(RuntimeError, match="injected failure"):
            mod.migrate(path, force=True)

        assert _table_names(path) == {"ohlcv", "ohlcv_all", "db_meta"}
        assert _count(path, "ohlcv") == 5
        assert _count(path, "ohlcv_all") == 0

    def test_refuses_a_missing_database_without_creating_it(
        self, tmp_path: Any
    ) -> None:
        # duckdb.connect() CREATES an empty database at any path it is given, so a
        # typo'd path must be refused before the connect, not after.
        from tools.migrate_ohlcv_venue import MissingDatabaseError, migrate

        path = tmp_path / "typo.duckdb"
        with pytest.raises(MissingDatabaseError):
            migrate(path, force=True)
        assert not path.exists()

    def test_an_empty_database_is_refused_not_migrated(self, tmp_path: Any) -> None:
        from tools.migrate_ohlcv_venue import AlreadyMigratedError, migrate

        path = tmp_path / "empty.duckdb"
        duckdb.connect(str(path)).close()
        with pytest.raises(AlreadyMigratedError):
            migrate(path, force=True)

    def test_the_migrated_database_serves_init_schema_and_a_write(
        self, tmp_path: Any
    ) -> None:
        # The live consequence: the daemon must start against the migrated file.
        import pandas as pd

        from analytics.store.market_data import upsert_ohlcv
        from analytics.store.schema import init_schema
        from tools.migrate_ohlcv_venue import migrate

        path = _legacy_db(tmp_path)
        migrate(path, force=True)

        conn = duckdb.connect(str(path))
        try:
            init_schema(conn)  # must not raise UnmigratedDatabaseError any more
            upsert_ohlcv(
                conn,
                pd.DataFrame(
                    [
                        {
                            "symbol": "BTCUSDT",
                            "timeframe": "1h",
                            "open_time": 99,
                            "open": 1.0,
                            "high": 2.0,
                            "low": 0.5,
                            "close": 1.5,
                            "volume": 10.0,
                            "taker_buy_volume": 5.0,
                        }
                    ]
                ),
                venue="okx",
            )
            # The okx bar lands in ohlcv_all but NOT in the binance-pinned view.
            assert conn.execute("SELECT COUNT(*) FROM ohlcv_all").fetchone() == (6,)
            assert conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone() == (5,)
        finally:
            conn.close()

    def test_main_migrates_and_warns_loudly_on_force(
        self, tmp_path: Any, monkeypatch: Any, capsys: Any
    ) -> None:
        import tools.migrate_ohlcv_venue as mod

        path = _legacy_db(tmp_path)
        monkeypatch.setattr(
            "sys.argv", ["migrate_ohlcv_venue.py", str(path), "--force"]
        )
        assert mod.main() == 0
        captured = capsys.readouterr()
        assert "5" in captured.out
        assert "FORCE" in captured.err.upper()

    def test_main_reports_a_refusal_and_exits_nonzero(
        self, tmp_path: Any, monkeypatch: Any, capsys: Any
    ) -> None:
        import tools.migrate_ohlcv_venue as mod

        path = _legacy_db(tmp_path)
        monkeypatch.setenv("BUIBUI_BACKUP_ROOT", str(tmp_path / "no-backups"))
        monkeypatch.setattr("sys.argv", ["migrate_ohlcv_venue.py", str(path)])
        assert mod.main() == 1
        assert "backup" in capsys.readouterr().err.lower()
        assert "ohlcv" in _table_names(path)

    def test_refuses_a_legacy_table_carrying_an_unexpected_column(
        self, tmp_path: Any
    ) -> None:
        # The copy names its columns, so an extra one would be silently discarded --
        # a data loss that no row count could detect. Refuse instead.
        from tools.migrate_ohlcv_venue import LegacySchemaError, migrate

        path = tmp_path / "extra.duckdb"
        conn = duckdb.connect(str(path))
        conn.execute(_LEGACY_DDL)
        conn.execute("ALTER TABLE ohlcv ADD COLUMN quote_volume DOUBLE")
        conn.execute(
            "INSERT INTO ohlcv VALUES ('BTCUSDT','1h',1,10,11,9,10.5,100,50,999)"
        )
        conn.close()

        with pytest.raises(LegacySchemaError, match="quote_volume"):
            migrate(path, force=True)

        assert _table_names(path) == {"ohlcv"}
        assert _count(path, "ohlcv") == 1

    def test_refuses_a_legacy_table_missing_a_column(self, tmp_path: Any) -> None:
        from tools.migrate_ohlcv_venue import LegacySchemaError, migrate

        path = tmp_path / "short.duckdb"
        conn = duckdb.connect(str(path))
        conn.execute(
            "CREATE TABLE ohlcv (symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
            "open_time BIGINT NOT NULL, open DOUBLE NOT NULL, high DOUBLE NOT NULL, "
            "low DOUBLE NOT NULL, close DOUBLE NOT NULL, volume DOUBLE NOT NULL, "
            "PRIMARY KEY (symbol, timeframe, open_time))"
        )
        conn.execute("INSERT INTO ohlcv VALUES ('BTCUSDT','1h',1,10,11,9,10.5,100)")
        conn.close()

        with pytest.raises(LegacySchemaError, match="taker_buy_volume"):
            migrate(path, force=True)

        assert _table_names(path) == {"ohlcv"}

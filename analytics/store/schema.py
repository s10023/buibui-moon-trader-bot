"""DuckDB schema initialisation — extracted from analytics/data_store.py (store-1)."""

import duckdb

from analytics.store.venue import ohlcv_view_sql, read_venue_order


class UnmigratedDatabaseError(RuntimeError):
    """Raised when a database still has the pre-ST60(b) `ohlcv` TABLE."""


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create all tables if they do not exist."""
    tables = {
        row[0]
        for row in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
    }
    if "ohlcv" in tables:
        # duckdb_tables() lists TABLES only, never views — after a real migration
        # `ohlcv` is a view and never appears here. Do NOT also require
        # "ohlcv_all" not in tables: init_schema creates ohlcv_all and db_meta
        # (both CREATE TABLE IF NOT EXISTS) *before* it reaches the CREATE OR
        # REPLACE VIEW that fails on a legacy table, so a failed run leaves both
        # tables present. Keying on ohlcv_all's absence would silently pass that
        # half-initialised state straight through to the same raw
        # duckdb.CatalogException this guard exists to replace.
        raise UnmigratedDatabaseError(
            "This database predates the ohlcv venue key: `ohlcv` is still a TABLE. "
            "Take a backup, then run `poetry run python tools/migrate_ohlcv_venue.py`. "
            "Nothing has been modified."
        )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv_all (
            venue            TEXT   NOT NULL,
            symbol           TEXT   NOT NULL,
            timeframe        TEXT   NOT NULL,
            open_time        BIGINT NOT NULL,
            open             DOUBLE NOT NULL,
            high             DOUBLE NOT NULL,
            low              DOUBLE NOT NULL,
            close            DOUBLE NOT NULL,
            volume           DOUBLE NOT NULL,
            taker_buy_volume DOUBLE,
            PRIMARY KEY (venue, symbol, timeframe, open_time)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS db_meta (
            key   TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (key)
        )
    """)
    conn.execute(ohlcv_view_sql(read_venue_order(conn)))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS funding_rates (
            symbol       TEXT   NOT NULL,
            funding_time BIGINT NOT NULL,
            funding_rate DOUBLE NOT NULL,
            PRIMARY KEY (symbol, funding_time)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS open_interest (
            symbol    TEXT   NOT NULL,
            timestamp BIGINT NOT NULL,
            oi_usd    DOUBLE NOT NULL,
            PRIMARY KEY (symbol, timestamp)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS venue_spot_daily (
            venue     TEXT   NOT NULL,
            symbol    TEXT   NOT NULL,
            open_time BIGINT NOT NULL,
            close     DOUBLE NOT NULL,
            PRIMARY KEY (venue, symbol, open_time)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS spot_ohlcv (
            symbol           TEXT   NOT NULL,
            open_time        BIGINT NOT NULL,
            open             DOUBLE NOT NULL,
            high             DOUBLE NOT NULL,
            low              DOUBLE NOT NULL,
            close            DOUBLE NOT NULL,
            volume           DOUBLE NOT NULL,
            taker_buy_volume DOUBLE,
            PRIMARY KEY (symbol, open_time)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS symbol_lifecycle (
            symbol            TEXT   PRIMARY KEY,
            status            TEXT   NOT NULL,
            onboard_ms        BIGINT,
            first_checked_ms  BIGINT NOT NULL,
            last_checked_ms   BIGINT NOT NULL,
            delisted_noted_ms BIGINT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            symbol        VARCHAR NOT NULL,
            timeframe     VARCHAR NOT NULL,
            strategy      VARCHAR NOT NULL,
            open_time     BIGINT  NOT NULL,
            direction     VARCHAR NOT NULL,
            entry_price   DOUBLE,
            sl_price      DOUBLE,
            reason        VARCHAR,
            confidence    INTEGER,
            fired_at      BIGINT  NOT NULL,
            PRIMARY KEY (symbol, timeframe, strategy, open_time, direction)
        )
    """)
    # Migration: rename legacy signal_outcomes → signal_alert_outcomes.
    existing_tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    if (
        "signal_outcomes" in existing_tables
        and "signal_alert_outcomes" not in existing_tables
    ):
        conn.execute("ALTER TABLE signal_outcomes RENAME TO signal_alert_outcomes")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_alert_outcomes (
            signal_id              TEXT   PRIMARY KEY,
            symbol                 TEXT   NOT NULL,
            tf                     TEXT   NOT NULL,
            strategy               TEXT   NOT NULL,
            direction              TEXT   NOT NULL,
            fired_at_ms            BIGINT NOT NULL,
            candle_ts_ms           BIGINT,
            entry_price            DOUBLE,
            sl_price               DOUBLE,
            tp_price               DOUBLE,
            rr_ratio               DOUBLE,
            confidence_at_fire     INTEGER,
            tags                   TEXT,
            outcome                TEXT,
            outcome_r              DOUBLE,
            outcome_filled_at_ms   BIGINT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_runs (
            run_id               TEXT    PRIMARY KEY,
            symbol               TEXT    NOT NULL,
            timeframe            TEXT    NOT NULL,
            strategy             TEXT    NOT NULL,
            data_start_ms        BIGINT  NOT NULL,
            data_end_ms          BIGINT  NOT NULL,
            days                 INTEGER NOT NULL,
            sl_pct               DOUBLE  NOT NULL,
            tp_r                 DOUBLE  NOT NULL,
            fee_pct              DOUBLE  NOT NULL,
            day_filter           TEXT    NOT NULL,
            smt_trend_filter     INTEGER NOT NULL,
            secondary_symbol     TEXT,
            total_signals        INTEGER NOT NULL,
            closed_trades        INTEGER NOT NULL,
            win_count            INTEGER NOT NULL,
            loss_count           INTEGER NOT NULL,
            win_rate             DOUBLE  NOT NULL,
            avg_r                DOUBLE  NOT NULL,
            total_r              DOUBLE  NOT NULL,
            max_drawdown_r       DOUBLE  NOT NULL,
            run_at_ms            BIGINT  NOT NULL,
            sweep_id             TEXT,
            long_closed_trades   INTEGER,
            long_win_count       INTEGER,
            long_win_rate        DOUBLE,
            long_avg_r           DOUBLE,
            short_closed_trades  INTEGER,
            short_win_count      INTEGER,
            short_win_rate       DOUBLE,
            short_avg_r          DOUBLE,
            long_total_r         DOUBLE,
            short_total_r        DOUBLE,
            volume_suppress      BOOLEAN,
            live_parity          TEXT,
            adr_exempt           BOOLEAN
        )
    """)
    # Migration: add long/short split columns to existing DBs.
    existing_bt_cols = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'backtest_runs'"
        ).fetchall()
    }
    for col, dtype in [
        ("long_closed_trades", "INTEGER"),
        ("long_win_count", "INTEGER"),
        ("long_win_rate", "DOUBLE"),
        ("long_avg_r", "DOUBLE"),
        ("short_closed_trades", "INTEGER"),
        ("short_win_count", "INTEGER"),
        ("short_win_rate", "DOUBLE"),
        ("short_avg_r", "DOUBLE"),
        ("adr_suppress_threshold", "REAL"),
        ("long_total_r", "DOUBLE"),
        ("short_total_r", "DOUBLE"),
        ("recovery_factor", "DOUBLE"),
        ("volume_suppress", "BOOLEAN"),
        # ST86: which live-parity gates the run used, and whether it was ADR
        # exempt. Both are in the run_id too — these columns exist so a stored
        # row can SAY what it ran under, which is what a tp_r's provenance
        # turns on and what an exempt run needs to be told apart from an
        # unthresholded one (both sit at adr_suppress_threshold IS NULL).
        ("live_parity", "TEXT"),
        ("adr_exempt", "BOOLEAN"),
        # ST104 P1: sorted-key JSON text of a detector-level retune (e.g.
        # eqh_eql's lookback/tolerance_pct/swing_n), stored so a row can say
        # what it ran under. Also in the run_id (see _backtest_run_id) so a
        # retuned study row can never collide with the default-param row for
        # the same symbol/tf/strategy/day_filter. NULL on every pre-existing
        # row and on every default-param run going forward.
        ("detector_params", "TEXT"),
    ]:
        if col not in existing_bt_cols:
            conn.execute(f"ALTER TABLE backtest_runs ADD COLUMN {col} {dtype}")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_trades (
            trade_id        TEXT    PRIMARY KEY,
            run_id          TEXT    NOT NULL,
            symbol          TEXT    NOT NULL,
            timeframe       TEXT    NOT NULL,
            strategy        TEXT    NOT NULL,
            direction       TEXT    NOT NULL,
            signal_time     BIGINT  NOT NULL,
            entry_time      BIGINT  NOT NULL,
            entry_price     DOUBLE  NOT NULL,
            sl_price        DOUBLE  NOT NULL,
            tp_price        DOUBLE  NOT NULL,
            exit_time       BIGINT,
            exit_price      DOUBLE,
            outcome         TEXT    NOT NULL,
            pnl_r           DOUBLE,
            low_volume      BOOLEAN,
            volume_spike    BOOLEAN
        )
    """)
    # Migration: add volume flags for the gate audit / replay toolchain.
    existing_bt_trade_cols = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'backtest_trades'"
        ).fetchall()
    }
    for col in ("low_volume", "volume_spike"):
        if col not in existing_bt_trade_cols:
            conn.execute(f"ALTER TABLE backtest_trades ADD COLUMN {col} BOOLEAN")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stats_cache (
            symbol        TEXT    NOT NULL,
            days          INTEGER NOT NULL,
            computed_date TEXT    NOT NULL,
            payload_json  TEXT    NOT NULL,
            PRIMARY KEY (symbol, days, computed_date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS confidence_ratings (
            config_name   TEXT    NOT NULL,
            strategy      TEXT    NOT NULL,
            tf            TEXT    NOT NULL,
            direction     TEXT    NOT NULL,
            stars         INTEGER NOT NULL,
            avg_r         REAL,
            win_rate      REAL,
            updated_at_ms BIGINT,
            day_filter    TEXT,
            dsr           REAL,
            PRIMARY KEY (config_name, strategy, tf, direction)
        )
    """)
    # Migration: add direction column (new PK member) to existing tables.
    existing_cr_cols = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'confidence_ratings'"
        ).fetchall()
    }
    if "direction" not in existing_cr_cols:
        # Recreate table with direction in PK; backfill existing rows as 'combined'.
        conn.execute("""
            CREATE TABLE confidence_ratings_v2 (
                config_name   TEXT    NOT NULL,
                strategy      TEXT    NOT NULL,
                tf            TEXT    NOT NULL,
                direction     TEXT    NOT NULL,
                stars         INTEGER NOT NULL,
                avg_r         REAL,
                win_rate      REAL,
                updated_at_ms BIGINT,
                day_filter    TEXT,
                PRIMARY KEY (config_name, strategy, tf, direction)
            )
        """)
        conn.execute("""
            INSERT INTO confidence_ratings_v2
            SELECT config_name, strategy, tf, 'combined', stars, avg_r, win_rate,
                   updated_at_ms, day_filter
            FROM confidence_ratings
        """)
        conn.execute("DROP TABLE confidence_ratings")
        conn.execute("ALTER TABLE confidence_ratings_v2 RENAME TO confidence_ratings")
    elif "day_filter" not in existing_cr_cols:
        conn.execute("ALTER TABLE confidence_ratings ADD COLUMN day_filter TEXT")
    # Migration: add DSR annotation column (P0a-2 sub-PR 3). Re-query because the
    # branches above may have recreated the table (legacy direction backfill).
    final_cr_cols = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'confidence_ratings'"
        ).fetchall()
    }
    if "dsr" not in final_cr_cols:
        conn.execute("ALTER TABLE confidence_ratings ADD COLUMN dsr REAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_combos (
            combo_id         TEXT    PRIMARY KEY,
            symbol           TEXT    NOT NULL,
            timeframe        TEXT    NOT NULL,
            strategy_a       TEXT    NOT NULL,
            strategy_b       TEXT    NOT NULL,
            window_candles   INTEGER NOT NULL,
            data_start_ms    BIGINT  NOT NULL,
            data_end_ms      BIGINT  NOT NULL,
            days             INTEGER NOT NULL,
            sl_pct           DOUBLE  NOT NULL,
            tp_r             DOUBLE  NOT NULL,
            fee_pct          DOUBLE  NOT NULL,
            day_filter       TEXT    NOT NULL,
            total_signals    INTEGER NOT NULL,
            closed_trades    INTEGER NOT NULL,
            win_count        INTEGER NOT NULL,
            win_rate         DOUBLE  NOT NULL,
            avg_r            DOUBLE  NOT NULL,
            total_r          DOUBLE  NOT NULL,
            max_drawdown_r   DOUBLE  NOT NULL,
            recovery_factor  DOUBLE,
            long_closed_trades  INTEGER,
            long_win_rate    DOUBLE,
            long_avg_r       DOUBLE,
            short_closed_trades INTEGER,
            short_win_rate   DOUBLE,
            short_avg_r      DOUBLE,
            run_at_ms        BIGINT  NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_cross_tf_combos (
            combo_id            TEXT    PRIMARY KEY,
            symbol              TEXT    NOT NULL,
            tf_htf              TEXT    NOT NULL,
            tf_ltf              TEXT    NOT NULL,
            strategy_htf        TEXT    NOT NULL,
            strategy_ltf        TEXT    NOT NULL,
            window_hours        DOUBLE  NOT NULL,
            data_start_ms       BIGINT  NOT NULL,
            data_end_ms         BIGINT  NOT NULL,
            days                INTEGER NOT NULL,
            sl_pct              DOUBLE  NOT NULL,
            tp_r                DOUBLE  NOT NULL,
            fee_pct             DOUBLE  NOT NULL,
            day_filter          TEXT    NOT NULL,
            total_signals       INTEGER NOT NULL,
            closed_trades       INTEGER NOT NULL,
            win_count           INTEGER NOT NULL,
            win_rate            DOUBLE  NOT NULL,
            avg_r               DOUBLE  NOT NULL,
            total_r             DOUBLE  NOT NULL,
            max_drawdown_r      DOUBLE  NOT NULL,
            recovery_factor     DOUBLE,
            long_closed_trades  INTEGER,
            long_win_rate       DOUBLE,
            long_avg_r          DOUBLE,
            short_closed_trades INTEGER,
            short_win_rate      DOUBLE,
            short_avg_r         DOUBLE,
            run_at_ms           BIGINT  NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_cache (
            cache_key        TEXT    PRIMARY KEY,
            run_id           TEXT    NOT NULL,
            last_candle_ts   BIGINT  NOT NULL,
            symbol           TEXT    NOT NULL,
            timeframe        TEXT    NOT NULL,
            strategy         TEXT    NOT NULL,
            fee_pct          DOUBLE  NOT NULL,
            n_closed         INTEGER NOT NULL,
            n_long           INTEGER NOT NULL,
            n_short          INTEGER NOT NULL,
            n_win            INTEGER NOT NULL,
            n_loss           INTEGER NOT NULL,
            r_win_rate       DOUBLE  NOT NULL,
            r_avg            DOUBLE  NOT NULL,
            r_total          DOUBLE  NOT NULL,
            n_long_win       INTEGER NOT NULL,
            r_long_win_rate  DOUBLE,
            r_long_avg       DOUBLE,
            r_long_total     DOUBLE  NOT NULL,
            n_short_win      INTEGER NOT NULL,
            r_short_win_rate DOUBLE,
            r_short_avg      DOUBLE,
            r_short_total    DOUBLE  NOT NULL,
            h_median         DOUBLE,
            h_long_median    DOUBLE,
            h_short_median   DOUBLE,
            cached_at_ms     BIGINT  NOT NULL
        )
    """)
    # Backfill existing runs from trades table where split columns are still NULL.
    # Runs after backtest_trades is created so the table always exists.
    conn.execute("""
        UPDATE backtest_runs r
        SET
            long_closed_trades  = t.lct,
            long_win_count      = t.lwc,
            long_win_rate       = CASE WHEN t.lct > 0 THEN t.lwc * 1.0 / t.lct ELSE NULL END,
            long_avg_r          = t.lar,
            short_closed_trades = t.sct,
            short_win_count     = t.swc,
            short_win_rate      = CASE WHEN t.sct > 0 THEN t.swc * 1.0 / t.sct ELSE NULL END,
            short_avg_r         = t.sar
        FROM (
            SELECT
                run_id,
                COUNT(*)   FILTER (WHERE direction = 'long'  AND outcome != 'open') AS lct,
                COUNT(*)   FILTER (WHERE direction = 'long'  AND outcome = 'win')   AS lwc,
                AVG(pnl_r) FILTER (WHERE direction = 'long'  AND outcome != 'open') AS lar,
                COUNT(*)   FILTER (WHERE direction = 'short' AND outcome != 'open') AS sct,
                COUNT(*)   FILTER (WHERE direction = 'short' AND outcome = 'win')   AS swc,
                AVG(pnl_r) FILTER (WHERE direction = 'short' AND outcome != 'open') AS sar
            FROM backtest_trades
            GROUP BY run_id
        ) t
        WHERE r.run_id = t.run_id
          AND r.long_closed_trades IS NULL
    """)

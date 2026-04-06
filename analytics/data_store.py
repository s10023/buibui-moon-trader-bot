"""Pure data store logic for analytics — DuckDB-backed OHLCV/funding/OI storage.

All functions accept an open DuckDB connection as a parameter.
No module-level side effects.
"""

import hashlib
import time
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

DEFAULT_DB_PATH: Path = Path("analytics.db")


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create all tables if they do not exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            symbol           TEXT   NOT NULL,
            timeframe        TEXT   NOT NULL,
            open_time        BIGINT NOT NULL,
            open             DOUBLE NOT NULL,
            high             DOUBLE NOT NULL,
            low              DOUBLE NOT NULL,
            close            DOUBLE NOT NULL,
            volume           DOUBLE NOT NULL,
            taker_buy_volume DOUBLE,
            PRIMARY KEY (symbol, timeframe, open_time)
        )
    """)
    # Migration guard: add column to existing DBs that were created before this field.
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'ohlcv'"
        ).fetchall()
    }
    if "taker_buy_volume" not in existing:
        conn.execute("ALTER TABLE ohlcv ADD COLUMN taker_buy_volume DOUBLE")
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
            short_total_r        DOUBLE
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
            pnl_r           DOUBLE
        )
    """)
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


def _upsert(
    conn: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    table: str,
    columns: str,
) -> None:
    if df.empty:
        return
    # register/unregister in try/finally: DuckDB increments refcount on register and
    # decrements on unregister, giving safe bulk-scan performance without the stale
    # C-pointer heap corruption that the implicit replacement scan (FROM df) causes.
    conn.register("_upsert_df", df)
    try:
        conn.execute(f"INSERT OR REPLACE INTO {table} SELECT {columns} FROM _upsert_df")
    finally:
        conn.unregister("_upsert_df")


def upsert_ohlcv(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """Insert or replace OHLCV rows.

    df must have columns: symbol, timeframe, open_time, open, high, low, close, volume,
    taker_buy_volume.
    Conflicts on (symbol, timeframe, open_time) are replaced.
    """
    _upsert(
        conn,
        df,
        "ohlcv",
        "symbol, timeframe, open_time, open, high, low, close, volume, taker_buy_volume",
    )


def upsert_funding_rates(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """Insert or replace funding rate rows.

    df must have columns: symbol, funding_time, funding_rate.
    Conflicts on (symbol, funding_time) are replaced.
    """
    _upsert(conn, df, "funding_rates", "symbol, funding_time, funding_rate")


def upsert_open_interest(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """Insert or replace open interest rows.

    df must have columns: symbol, timestamp, oi_usd.
    Conflicts on (symbol, timestamp) are replaced.
    """
    _upsert(conn, df, "open_interest", "symbol, timestamp, oi_usd")


def get_ohlcv(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    start: int,
    end: int,
) -> pd.DataFrame:
    """Return OHLCV rows for (symbol, timeframe) between start and end (Unix ms, inclusive)."""
    return conn.execute(
        "SELECT symbol, timeframe, open_time, open, high, low, close, volume, taker_buy_volume "
        "FROM ohlcv "
        "WHERE symbol = ? AND timeframe = ? AND open_time >= ? AND open_time <= ? "
        "ORDER BY open_time",
        [symbol, timeframe, start, end],
    ).df()


def get_funding_rates(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    start: int,
    end: int,
) -> pd.DataFrame:
    """Return funding rate rows for symbol between start and end (Unix ms, inclusive)."""
    return conn.execute(
        "SELECT symbol, funding_time, funding_rate "
        "FROM funding_rates "
        "WHERE symbol = ? AND funding_time >= ? AND funding_time <= ? "
        "ORDER BY funding_time",
        [symbol, start, end],
    ).df()


def get_open_interest(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    start: int,
    end: int,
) -> pd.DataFrame:
    """Return open interest rows for symbol between start and end (Unix ms, inclusive)."""
    return conn.execute(
        "SELECT symbol, timestamp, oi_usd "
        "FROM open_interest "
        "WHERE symbol = ? AND timestamp >= ? AND timestamp <= ? "
        "ORDER BY timestamp",
        [symbol, start, end],
    ).df()


def upsert_signals(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """Insert or ignore signal rows (conflicts on PK are silently skipped).

    df must have columns: symbol, timeframe, strategy, open_time, direction,
    entry_price, sl_price, reason, confidence, fired_at.
    Conflicts on (symbol, timeframe, strategy, open_time, direction) are ignored
    so that re-runs of the same scan cycle do not overwrite previously persisted
    signals with potentially different metadata.
    """
    if df.empty:
        return
    # Explicit register/unregister in try/finally — see _upsert docstring for why.
    conn.register("_signals_upsert_df", df)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO signals "
            "SELECT symbol, timeframe, strategy, open_time, direction, "
            "entry_price, sl_price, reason, confidence, fired_at "
            "FROM _signals_upsert_df"
        )
    finally:
        conn.unregister("_signals_upsert_df")


def get_signals_history(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    """Return persisted signal rows for (symbol, timeframe) in [start_ms, end_ms].

    Results are ordered by open_time descending (most recent first).
    """
    return conn.execute(
        "SELECT symbol, timeframe, strategy, open_time, direction, "
        "entry_price, sl_price, reason, confidence, fired_at "
        "FROM signals "
        "WHERE symbol = ? AND timeframe = ? AND open_time >= ? AND open_time <= ? "
        "ORDER BY open_time DESC",
        [symbol, timeframe, start_ms, end_ms],
    ).df()


def upsert_signal_outcome(conn: duckdb.DuckDBPyConnection, row: dict[str, Any]) -> None:
    """Insert or replace a single signal outcome row.

    The row dict must contain at minimum: signal_id, symbol, tf, strategy,
    direction, fired_at_ms.  All other fields are optional and default to NULL
    when omitted.

    Conflicts on signal_id are replaced so that outcome / outcome_r /
    outcome_filled_at_ms can be backfilled later without inserting duplicates.
    """
    df = pd.DataFrame([row])
    # Ensure every column the table expects is present; fill missing with None.
    _OUTCOME_COLUMNS = [
        "signal_id",
        "symbol",
        "tf",
        "strategy",
        "direction",
        "fired_at_ms",
        "candle_ts_ms",
        "entry_price",
        "sl_price",
        "tp_price",
        "rr_ratio",
        "confidence_at_fire",
        "tags",
        "outcome",
        "outcome_r",
        "outcome_filled_at_ms",
    ]
    for col in _OUTCOME_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[_OUTCOME_COLUMNS]
    # Explicit register/unregister in try/finally — see _upsert docstring for why.
    conn.register("_outcome_upsert_df", df)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO signal_alert_outcomes "
            "SELECT signal_id, symbol, tf, strategy, direction, fired_at_ms, "
            "candle_ts_ms, entry_price, sl_price, tp_price, rr_ratio, "
            "confidence_at_fire, tags, outcome, outcome_r, outcome_filled_at_ms "
            "FROM _outcome_upsert_df"
        )
    finally:
        conn.unregister("_outcome_upsert_df")


def _backtest_run_id(
    symbol: str,
    timeframe: str,
    strategy: str,
    days: int,
    sl_pct: float,
    tp_r: float,
    fee_pct: float,
    day_filter: str,
    smt_trend_filter: int,
    secondary_symbol: str | None,
    adr_suppress_threshold: float | None = None,
) -> str:
    """Return a deterministic 16-char hex ID for a backtest param combination.

    adr_suppress_threshold is appended only when set so existing run_ids are
    unchanged (None = no ADR filter applied, same hash as before this column).
    """
    key = f"{symbol}|{timeframe}|{strategy}|{days}|{sl_pct}|{tp_r}|{fee_pct}|{day_filter}|{smt_trend_filter}|{secondary_symbol}"
    if adr_suppress_threshold is not None:
        key += f"|adr:{adr_suppress_threshold}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def upsert_backtest_run(
    conn: duckdb.DuckDBPyConnection,
    result: Any,
    days: int,
    data_start_ms: int,
    data_end_ms: int,
    sl_pct: float,
    tp_r: float,
    fee_pct: float,
    day_filter: str,
    smt_trend_filter: int,
    secondary_symbol: str | None = None,
    sweep_id: str | None = None,
    adr_suppress_threshold: float | None = None,
) -> str:
    """Insert or replace a backtest aggregate result row.

    result must be a BacktestResult instance.
    Returns the run_id so the caller can link backtest_trades rows.
    """
    run_id = _backtest_run_id(
        result.symbol,
        result.timeframe,
        result.strategy,
        days,
        sl_pct,
        tp_r,
        fee_pct,
        day_filter,
        smt_trend_filter,
        secondary_symbol,
        adr_suppress_threshold,
    )
    row: dict[str, Any] = {
        "run_id": run_id,
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "strategy": result.strategy,
        "data_start_ms": data_start_ms,
        "data_end_ms": data_end_ms,
        "days": days,
        "sl_pct": sl_pct,
        "tp_r": tp_r,
        "fee_pct": fee_pct,
        "day_filter": day_filter,
        "smt_trend_filter": smt_trend_filter,
        "secondary_symbol": secondary_symbol,
        "total_signals": len(result.trades),
        "closed_trades": len(result.closed_trades),
        "win_count": result.win_count,
        "loss_count": result.loss_count,
        "win_rate": result.win_rate,
        "avg_r": result.avg_r,
        "total_r": result.total_r,
        "max_drawdown_r": result.max_drawdown_r,
        "run_at_ms": int(time.time() * 1000),
        "sweep_id": sweep_id,
        "adr_suppress_threshold": adr_suppress_threshold,
        "long_closed_trades": len(result.long_closed_trades),
        "long_win_count": result.long_win_count,
        "long_win_rate": result.long_win_rate,
        "long_avg_r": result.long_avg_r,
        "short_closed_trades": len(result.short_closed_trades),
        "short_win_count": result.short_win_count,
        "short_win_rate": result.short_win_rate,
        "short_avg_r": result.short_avg_r,
        "long_total_r": result.long_total_r,
        "short_total_r": result.short_total_r,
    }
    df = pd.DataFrame([row])
    conn.register("_bt_run_upsert_df", df)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO backtest_runs SELECT "
            "run_id, symbol, timeframe, strategy, data_start_ms, data_end_ms, "
            "days, sl_pct, tp_r, fee_pct, day_filter, smt_trend_filter, "
            "secondary_symbol, total_signals, closed_trades, win_count, loss_count, "
            "win_rate, avg_r, total_r, max_drawdown_r, run_at_ms, sweep_id, "
            "long_closed_trades, long_win_count, long_win_rate, long_avg_r, "
            "short_closed_trades, short_win_count, short_win_rate, short_avg_r, "
            "adr_suppress_threshold, long_total_r, short_total_r "
            "FROM _bt_run_upsert_df"
        )
    finally:
        conn.unregister("_bt_run_upsert_df")
    return run_id


def upsert_backtest_trades(
    conn: duckdb.DuckDBPyConnection,
    result: Any,
    run_id: str,
) -> None:
    """Insert or replace per-trade rows for a backtest run.

    result must be a BacktestResult instance.
    Skips if result.trades is empty.
    """
    if not result.trades:
        return
    rows = [
        {
            "trade_id": f"{run_id}:{t.signal_time}",
            "run_id": run_id,
            "symbol": result.symbol,
            "timeframe": result.timeframe,
            "strategy": result.strategy,
            "direction": t.direction,
            "signal_time": t.signal_time,
            "entry_time": t.entry_time,
            "entry_price": t.entry_price,
            "sl_price": t.sl_price,
            "tp_price": t.tp_price,
            "exit_time": t.exit_time,
            "exit_price": t.exit_price,
            "outcome": t.outcome,
            "pnl_r": t.pnl_r,
        }
        for t in result.trades
    ]
    df = pd.DataFrame(rows)
    conn.register("_bt_trades_upsert_df", df)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO backtest_trades SELECT "
            "trade_id, run_id, symbol, timeframe, strategy, direction, "
            "signal_time, entry_time, entry_price, sl_price, tp_price, "
            "exit_time, exit_price, outcome, pnl_r "
            "FROM _bt_trades_upsert_df"
        )
    finally:
        conn.unregister("_bt_trades_upsert_df")


def list_backtest_runs(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Return the latest backtest_run per (symbol, timeframe, strategy, day_filter), newest first.

    Attaches calibrated star ratings from confidence_ratings by matching on
    (strategy, timeframe, day_filter) so each row shows the correct per-config stars.
    """
    return conn.execute(
        "SELECT b.run_id, b.symbol, b.timeframe, b.strategy, b.days, b.sl_pct, b.tp_r, "
        "b.fee_pct, b.day_filter, b.closed_trades, b.win_count, b.loss_count, b.win_rate, "
        "b.avg_r, b.total_r, b.max_drawdown_r, b.sweep_id, b.run_at_ms, "
        "b.long_closed_trades, b.long_win_count, b.long_win_rate, b.long_avg_r, "
        "b.short_closed_trades, b.short_win_count, b.short_win_rate, b.short_avg_r, "
        "b.adr_suppress_threshold, cr.stars, cr_long.long_stars, cr_short.short_stars "
        "FROM ("
        "  SELECT *, ROW_NUMBER() OVER ("
        "    PARTITION BY symbol, timeframe, strategy, day_filter, adr_suppress_threshold "
        "    ORDER BY run_at_ms DESC"
        "  ) AS rn FROM backtest_runs"
        ") b "
        "LEFT JOIN ("
        "  SELECT strategy, tf, day_filter, MAX(stars) AS stars "
        "  FROM confidence_ratings "
        "  WHERE direction = 'combined' AND day_filter IS NOT NULL "
        "  GROUP BY strategy, tf, day_filter"
        ") cr ON cr.strategy = b.strategy AND cr.tf = b.timeframe AND cr.day_filter = b.day_filter "
        "LEFT JOIN ("
        "  SELECT strategy, tf, day_filter, MAX(stars) AS long_stars "
        "  FROM confidence_ratings "
        "  WHERE direction = 'long' AND day_filter IS NOT NULL "
        "  GROUP BY strategy, tf, day_filter"
        ") cr_long ON cr_long.strategy = b.strategy AND cr_long.tf = b.timeframe "
        "  AND cr_long.day_filter = b.day_filter "
        "LEFT JOIN ("
        "  SELECT strategy, tf, day_filter, MAX(stars) AS short_stars "
        "  FROM confidence_ratings "
        "  WHERE direction = 'short' AND day_filter IS NOT NULL "
        "  GROUP BY strategy, tf, day_filter"
        ") cr_short ON cr_short.strategy = b.strategy AND cr_short.tf = b.timeframe "
        "  AND cr_short.day_filter = b.day_filter "
        "WHERE b.rn = 1 "
        "ORDER BY b.run_at_ms DESC"
    ).df()


def get_win_rate_by_strategy(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Return win rate aggregated per strategy across all saved backtest runs.

    Only includes combos with at least 20 closed trades (same gate as sweep table).
    Ordered by win_rate_pct descending.
    """
    return conn.execute("""
        SELECT
            strategy,
            SUM(closed_trades)                                                  AS total_closed,
            SUM(win_count)                                                      AS total_wins,
            ROUND(SUM(win_count) * 100.0 / NULLIF(SUM(closed_trades), 0), 1)   AS win_rate_pct,
            ROUND(AVG(avg_r), 3)                                                AS mean_avg_r,
            COUNT(*)                                                            AS combos_run
        FROM backtest_runs
        WHERE closed_trades >= 20
          AND adr_suppress_threshold IS NULL
        GROUP BY strategy
        ORDER BY win_rate_pct DESC
    """).df()


def get_latest_open_time(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
) -> int | None:
    """Return the maximum open_time stored for (symbol, timeframe), or None if no rows."""
    # ORDER BY ... LIMIT 1 instead of MAX() to avoid a DuckDB statistics
    # optimizer bug (InternalException on aggregate after multiple inserts).
    result = conn.execute(
        "SELECT open_time FROM ohlcv WHERE symbol = ? AND timeframe = ?"
        " ORDER BY open_time DESC LIMIT 1",
        [symbol, timeframe],
    ).fetchone()
    if result is None:
        return None
    return int(result[0])


def get_stats_cache(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    days: int,
    date_str: str,
) -> str | None:
    """Return cached stats payload JSON for (symbol, days, date_str), or None on miss."""
    result = conn.execute(
        "SELECT payload_json FROM stats_cache WHERE symbol = ? AND days = ? AND computed_date = ?",
        [symbol, days, date_str],
    ).fetchone()
    if result is None:
        return None
    return str(result[0])


def upsert_confidence_ratings(
    conn: duckdb.DuckDBPyConnection,
    config_name: str,
    ratings: dict[str, dict[str, int]],
    win_rates: pd.DataFrame,
    day_filter: str | None = None,
    direction: str = "combined",
    avg_r_col: str = "avg_r",
    win_rate_col: str = "win_rate",
) -> None:
    """Upsert per-config confidence star ratings keyed by (config_name, strategy, tf, direction).

    ratings: {strategy: {tf: stars}}
    win_rates: DataFrame from get_backtest_win_rates() — used to store avg_r/win_rate alongside stars.
    day_filter: the config's day_filter value — stored so backtest rows can JOIN correctly.
    direction: 'combined' (default), 'long', or 'short'.
    avg_r_col / win_rate_col: column names to read from win_rates (allows directional lookups).
    """
    if not ratings:
        return
    now_ms = int(time.time() * 1000)
    stats: dict[tuple[str, str], tuple[float | None, float | None]] = {}
    if not win_rates.empty and avg_r_col in win_rates.columns:
        for _, row in win_rates.iterrows():
            key = (str(row["strategy"]), str(row["timeframe"]))
            ar = row.get(avg_r_col)
            wr = row.get(win_rate_col)
            stats[key] = (
                float(ar) if ar is not None and ar == ar else None,  # noqa: PLR0124
                float(wr) if wr is not None and wr == wr else None,
            )
    rows = []
    for strategy, tf_map in ratings.items():
        for tf, stars in tf_map.items():
            avg_r_val, win_rate_val = stats.get((strategy, tf), (None, None))
            rows.append(
                {
                    "config_name": config_name,
                    "strategy": strategy,
                    "tf": tf,
                    "direction": direction,
                    "stars": stars,
                    "avg_r": avg_r_val,
                    "win_rate": win_rate_val,
                    "updated_at_ms": now_ms,
                    "day_filter": day_filter,
                }
            )
    df = pd.DataFrame(rows)
    conn.register("_cr_upsert_df", df)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO confidence_ratings "
            "SELECT config_name, strategy, tf, direction, stars, avg_r, win_rate, "
            "updated_at_ms, day_filter "
            "FROM _cr_upsert_df"
        )
    finally:
        conn.unregister("_cr_upsert_df")


def get_confidence_ratings(
    conn: duckdb.DuckDBPyConnection,
    config_name: str,
    direction: str = "combined",
) -> dict[str, dict[str, int]]:
    """Load confidence star ratings for a given config and direction from the DB.

    direction: 'combined' (default), 'long', or 'short'.
    Returns {strategy: {tf: stars}}, or empty dict if no ratings have been written yet.
    """
    rows = conn.execute(
        "SELECT strategy, tf, stars FROM confidence_ratings "
        "WHERE config_name = ? AND direction = ?",
        [config_name, direction],
    ).fetchall()
    result: dict[str, dict[str, int]] = {}
    for strategy, tf, stars in rows:
        if strategy not in result:
            result[str(strategy)] = {}
        result[str(strategy)][str(tf)] = int(stars)
    return result


def get_directional_confidence_ratings(
    conn: duckdb.DuckDBPyConnection,
    config_name: str,
) -> dict[str, dict[str, dict[str, int]]]:
    """Load directional confidence star ratings for a given config.

    Returns {strategy: {tf: {"long": stars, "short": stars}}}.
    Only includes entries where both long and short ratings exist.
    """
    rows = conn.execute(
        "SELECT strategy, tf, direction, stars FROM confidence_ratings "
        "WHERE config_name = ? AND direction IN ('long', 'short')",
        [config_name],
    ).fetchall()
    result: dict[str, dict[str, dict[str, int]]] = {}
    for strategy, tf, direction, stars in rows:
        s, t, d = str(strategy), str(tf), str(direction)
        if s not in result:
            result[s] = {}
        if t not in result[s]:
            result[s][t] = {}
        result[s][t][d] = int(stars)
    return result


def upsert_stats_cache(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    days: int,
    date_str: str,
    payload_json: str,
) -> None:
    """Insert or replace a stats cache entry for (symbol, days, date_str)."""
    conn.execute(
        "INSERT OR REPLACE INTO stats_cache (symbol, days, computed_date, payload_json) "
        "VALUES (?, ?, ?, ?)",
        [symbol, days, date_str, payload_json],
    )

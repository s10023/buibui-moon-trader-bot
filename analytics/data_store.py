"""Pure data store logic for analytics — DuckDB-backed OHLCV/funding/OI storage.

All functions accept an open DuckDB connection as a parameter.
No module-level side effects.
"""

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from analytics.store.schema import init_schema  # noqa: F401

DEFAULT_DB_PATH: Path = Path("analytics.db")

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


@dataclass
class BacktestSnapshot:
    """Pre-computed aggregate stats cached in backtest_cache table.

    Duck-type compatible with BacktestResult for signal filtering.
    closed_trades / long_closed_trades / short_closed_trades return dummy
    lists of the correct length — only len() and truthiness are used by callers.
    """

    symbol: str
    timeframe: str
    strategy: str
    fee_pct: float = 0.0
    n_closed: int = 0
    n_long: int = 0
    n_short: int = 0
    n_win: int = 0
    n_loss: int = 0
    r_win_rate: float = 0.0
    r_avg: float = 0.0
    r_total: float = 0.0
    n_long_win: int = 0
    r_long_win_rate: float | None = None
    r_long_avg: float | None = None
    r_long_total: float = 0.0
    n_short_win: int = 0
    r_short_win_rate: float | None = None
    r_short_avg: float | None = None
    r_short_total: float = 0.0
    h_median: float | None = None
    h_long_median: float | None = None
    h_short_median: float | None = None

    @property
    def closed_trades(self) -> list[None]:
        return [None] * self.n_closed

    @property
    def long_closed_trades(self) -> list[None]:
        return [None] * self.n_long

    @property
    def short_closed_trades(self) -> list[None]:
        return [None] * self.n_short

    @property
    def win_count(self) -> int:
        return self.n_win

    @property
    def loss_count(self) -> int:
        return self.n_loss

    @property
    def win_rate(self) -> float:
        return self.r_win_rate

    @property
    def avg_r(self) -> float:
        return self.r_avg

    @property
    def total_r(self) -> float:
        return self.r_total

    @property
    def long_win_count(self) -> int:
        return self.n_long_win

    @property
    def long_win_rate(self) -> float | None:
        return self.r_long_win_rate

    @property
    def long_avg_r(self) -> float | None:
        return self.r_long_avg

    @property
    def long_total_r(self) -> float:
        return self.r_long_total

    @property
    def short_win_count(self) -> int:
        return self.n_short_win

    @property
    def short_win_rate(self) -> float | None:
        return self.r_short_win_rate

    @property
    def short_avg_r(self) -> float | None:
        return self.r_short_avg

    @property
    def short_total_r(self) -> float:
        return self.r_short_total

    @property
    def median_duration_h(self) -> float | None:
        return self.h_median

    @property
    def long_median_duration_h(self) -> float | None:
        return self.h_long_median

    @property
    def short_median_duration_h(self) -> float | None:
        return self.h_short_median


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
    values = [row.get(col) for col in _OUTCOME_COLUMNS]
    conn.execute(
        "INSERT OR REPLACE INTO signal_alert_outcomes "
        "(signal_id, symbol, tf, strategy, direction, fired_at_ms, "
        "candle_ts_ms, entry_price, sl_price, tp_price, rr_ratio, "
        "confidence_at_fire, tags, outcome, outcome_r, outcome_filled_at_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        values,
    )


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
    volume_suppress: bool | None = None,
    min_sl_pct: float = 0.0,
    atr_sl_multiplier: float | None = None,
    tp_r_long: float | None = None,
    tp_r_short: float | None = None,
    volume_suppress_long: bool | None = None,
    volume_suppress_short: bool | None = None,
    volume_spike_boost_long: bool | None = None,
    volume_spike_boost_short: bool | None = None,
    adr_exempt: bool = False,
) -> str:
    """Return a deterministic 16-char hex ID for a backtest param combination.

    Optional suffixes are appended only when set so existing run_ids are
    unchanged (None = flag not applied, same hash as before these columns).
    """
    key = f"{symbol}|{timeframe}|{strategy}|{days}|{sl_pct}|{tp_r}|{fee_pct}|{day_filter}|{smt_trend_filter}|{secondary_symbol}"
    if adr_suppress_threshold is not None:
        key += f"|adr:{adr_suppress_threshold}"
    if volume_suppress:
        key += "|vol_suppress"
    if min_sl_pct > 0.0:
        key += f"|min_sl:{min_sl_pct}"
    if atr_sl_multiplier is not None:
        key += f"|atr_sl:{atr_sl_multiplier}"
    if tp_r_long is not None:
        key += f"|tp_long:{tp_r_long}"
    if tp_r_short is not None:
        key += f"|tp_short:{tp_r_short}"
    if volume_suppress_long:
        key += "|vol_sup_l"
    if volume_suppress_short:
        key += "|vol_sup_s"
    if volume_spike_boost_long:
        key += "|spike_l"
    if volume_spike_boost_short:
        key += "|spike_s"
    if adr_exempt:
        key += "|adr_exempt"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _make_bt_cache_key(run_id: str, last_candle_ts: int) -> str:
    """24-char hex key combining run params hash and last closed candle timestamp."""
    return hashlib.sha256(f"{run_id}|{last_candle_ts}".encode()).hexdigest()[:24]


def get_backtest_cache(
    conn: duckdb.DuckDBPyConnection,
    cache_key: str,
) -> BacktestSnapshot | None:
    """Return cached BacktestSnapshot for cache_key, or None on miss."""
    row = conn.execute(
        "SELECT symbol, timeframe, strategy, fee_pct, "
        "n_closed, n_long, n_short, n_win, n_loss, "
        "r_win_rate, r_avg, r_total, "
        "n_long_win, r_long_win_rate, r_long_avg, r_long_total, "
        "n_short_win, r_short_win_rate, r_short_avg, r_short_total, "
        "h_median, h_long_median, h_short_median "
        "FROM backtest_cache WHERE cache_key = ?",
        [cache_key],
    ).fetchone()
    if row is None:
        return None
    return BacktestSnapshot(
        symbol=str(row[0]),
        timeframe=str(row[1]),
        strategy=str(row[2]),
        fee_pct=float(row[3]),
        n_closed=int(row[4]),
        n_long=int(row[5]),
        n_short=int(row[6]),
        n_win=int(row[7]),
        n_loss=int(row[8]),
        r_win_rate=float(row[9]),
        r_avg=float(row[10]),
        r_total=float(row[11]),
        n_long_win=int(row[12]),
        r_long_win_rate=float(row[13]) if row[13] is not None else None,
        r_long_avg=float(row[14]) if row[14] is not None else None,
        r_long_total=float(row[15]) if row[15] is not None else 0.0,
        n_short_win=int(row[16]),
        r_short_win_rate=float(row[17]) if row[17] is not None else None,
        r_short_avg=float(row[18]) if row[18] is not None else None,
        r_short_total=float(row[19]) if row[19] is not None else 0.0,
        h_median=float(row[20]) if row[20] is not None else None,
        h_long_median=float(row[21]) if row[21] is not None else None,
        h_short_median=float(row[22]) if row[22] is not None else None,
    )


def put_backtest_cache(
    conn: duckdb.DuckDBPyConnection,
    cache_key: str,
    run_id: str,
    last_candle_ts: int,
    result: Any,
) -> None:
    """Persist a BacktestResult's aggregate stats to backtest_cache.

    result must be a BacktestResult instance. Trades are not stored.
    Uses parameterised INSERT OR REPLACE — no DataFrame scan, no try/finally needed.
    """
    from analytics.backtest_lib import BacktestResult

    assert isinstance(result, BacktestResult)
    now_ms = int(time.time() * 1000)
    conn.execute(
        "INSERT OR REPLACE INTO backtest_cache VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            cache_key,
            run_id,
            last_candle_ts,
            result.symbol,
            result.timeframe,
            result.strategy,
            result.fee_pct,
            len(result.closed_trades),
            len(result.long_closed_trades),
            len(result.short_closed_trades),
            result.win_count,
            result.loss_count,
            result.win_rate,
            result.avg_r,
            result.total_r,
            result.long_win_count,
            result.long_win_rate,
            result.long_avg_r,
            result.long_total_r,
            result.short_win_count,
            result.short_win_rate,
            result.short_avg_r,
            result.short_total_r,
            result.median_duration_h,
            result.long_median_duration_h,
            result.short_median_duration_h,
            now_ms,
        ],
    )


def prune_backtest_cache(
    conn: duckdb.DuckDBPyConnection,
    keep_days: int = 30,
) -> None:
    """Delete backtest_cache rows older than keep_days."""
    cutoff_ms = int(time.time() * 1000) - keep_days * 24 * 3600 * 1000
    conn.execute("DELETE FROM backtest_cache WHERE cached_at_ms < ?", [cutoff_ms])


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
    volume_suppress: bool | None = None,
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
        volume_suppress,
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
        "recovery_factor": result.recovery_factor,
        "volume_suppress": volume_suppress,
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
            "adr_suppress_threshold, long_total_r, short_total_r, recovery_factor, "
            "volume_suppress "
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
        "b.avg_r, b.total_r, b.max_drawdown_r, b.recovery_factor, b.sweep_id, b.run_at_ms, "
        "b.long_closed_trades, b.long_win_count, b.long_win_rate, b.long_avg_r, b.long_total_r, "
        "b.short_closed_trades, b.short_win_count, b.short_win_rate, b.short_avg_r, b.short_total_r, "
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
                float(ar) if ar is not None and not pd.isna(ar) else None,
                float(wr) if wr is not None and not pd.isna(wr) else None,
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


def upsert_combo_run(
    conn: duckdb.DuckDBPyConnection,
    combo: "Any",  # ComboBacktestResult — avoid circular import
    days: int,
    data_start_ms: int,
    data_end_ms: int,
    sl_pct: float,
    tp_r: float,
    fee_pct: float,
    day_filter: str,
) -> str:
    """Persist a ComboBacktestResult aggregate row and return the combo_id."""
    from analytics.backtest_lib import ComboBacktestResult

    assert isinstance(combo, ComboBacktestResult)
    r = combo.result

    run_at_ms = int(time.time() * 1000)
    # combo_id excludes run_at_ms so re-running overwrites the previous result
    # for the same (symbol, tf, pair, window, day_filter) instead of duplicating.
    combo_id = (
        f"{r.symbol}|{r.timeframe}|{combo.strategy_a}+{combo.strategy_b}"
        f"|w{combo.window}|{day_filter}"
    )

    rf = r.recovery_factor if r.max_drawdown_r > 0 else None

    conn.execute(
        """
        INSERT OR REPLACE INTO backtest_combos (
            combo_id, symbol, timeframe, strategy_a, strategy_b, window_candles,
            data_start_ms, data_end_ms, days, sl_pct, tp_r, fee_pct, day_filter,
            total_signals, closed_trades, win_count, win_rate, avg_r, total_r,
            max_drawdown_r, recovery_factor,
            long_closed_trades, long_win_rate, long_avg_r,
            short_closed_trades, short_win_rate, short_avg_r,
            run_at_ms
        ) VALUES (
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?
        )
        """,
        [
            combo_id,
            r.symbol,
            r.timeframe,
            combo.strategy_a,
            combo.strategy_b,
            combo.window,
            data_start_ms,
            data_end_ms,
            days,
            sl_pct,
            tp_r,
            fee_pct,
            day_filter,
            len(r.trades),
            len(r.closed_trades),
            r.win_count,
            r.win_rate,
            r.avg_r,
            r.total_r,
            r.max_drawdown_r,
            rf,
            len(r.long_closed_trades),
            r.long_win_rate,
            r.long_avg_r,
            len(r.short_closed_trades),
            r.short_win_rate,
            r.short_avg_r,
            run_at_ms,
        ],
    )
    return combo_id


def list_combo_runs(
    conn: duckdb.DuckDBPyConnection,
) -> "pd.DataFrame":
    """Return all backtest_combos rows sorted newest-first."""

    return conn.execute("SELECT * FROM backtest_combos ORDER BY run_at_ms DESC").df()


def get_combo_lookup(
    conn: duckdb.DuckDBPyConnection,
) -> "dict[tuple[str, str, frozenset[str]], dict[str, Any]]":
    """Build a lookup dict for live co-fire detection from backtest_combos.

    Keyed by (symbol, timeframe, frozenset({strategy_a, strategy_b})) → the row
    with the highest avg_r for that pair (across all day_filter values).

    Uses list_combo_runs which already deduplicates to the latest run per combo_id.
    Returns an empty dict when no combo runs have been saved yet.
    """
    df = list_combo_runs(conn)
    if df.empty:
        return {}
    lookup: dict[tuple[str, str, frozenset[str]], dict[str, Any]] = {}
    for row in df.to_dict("records"):
        key: tuple[str, str, frozenset[str]] = (
            str(row["symbol"]),
            str(row["timeframe"]),
            frozenset({str(row["strategy_a"]), str(row["strategy_b"])}),
        )
        avg_r = float(row["avg_r"])
        if key not in lookup or avg_r > lookup[key]["avg_r"]:
            lookup[key] = {
                "avg_r": avg_r,
                "win_rate": float(row["win_rate"]),
                "closed_trades": int(row["closed_trades"]),
                "strategy_a": str(row["strategy_a"]),
                "strategy_b": str(row["strategy_b"]),
            }
    return lookup


def upsert_cross_tf_combo_run(
    conn: duckdb.DuckDBPyConnection,
    combo: "Any",
    days: int,
    data_start_ms: int,
    data_end_ms: int,
    sl_pct: float,
    tp_r: float,
    fee_pct: float,
    day_filter: str,
) -> str:
    """Persist a CrossTfComboBacktestResult and return the combo_id.

    combo_id is stable across re-runs: same (symbol, tf_htf, tf_ltf,
    strategy_htf, strategy_ltf, window_hours, day_filter) always overwrites
    the previous result via INSERT OR REPLACE.
    """
    from analytics.backtest_lib import CrossTfComboBacktestResult

    assert isinstance(combo, CrossTfComboBacktestResult)
    r = combo.result

    run_at_ms = int(time.time() * 1000)
    wh = combo.window_hours
    wh_str = f"{wh:.1f}".rstrip("0").rstrip(".")
    combo_id = (
        f"{r.symbol}|{combo.tf_htf}+{combo.tf_ltf}"
        f"|{combo.strategy_htf}+{combo.strategy_ltf}"
        f"|w{wh_str}h|{day_filter}"
    )

    rf = r.recovery_factor if r.max_drawdown_r > 0 else None

    conn.execute(
        """
        INSERT OR REPLACE INTO backtest_cross_tf_combos (
            combo_id, symbol, tf_htf, tf_ltf, strategy_htf, strategy_ltf,
            window_hours, data_start_ms, data_end_ms, days, sl_pct, tp_r,
            fee_pct, day_filter, total_signals, closed_trades, win_count,
            win_rate, avg_r, total_r, max_drawdown_r, recovery_factor,
            long_closed_trades, long_win_rate, long_avg_r,
            short_closed_trades, short_win_rate, short_avg_r,
            run_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            combo_id,
            r.symbol,
            combo.tf_htf,
            combo.tf_ltf,
            combo.strategy_htf,
            combo.strategy_ltf,
            combo.window_hours,
            data_start_ms,
            data_end_ms,
            days,
            sl_pct,
            tp_r,
            fee_pct,
            day_filter,
            len(r.trades),
            len(r.closed_trades),
            r.win_count,
            r.win_rate,
            r.avg_r,
            r.total_r,
            r.max_drawdown_r,
            rf,
            len(r.long_closed_trades),
            r.long_win_rate,
            r.long_avg_r,
            len(r.short_closed_trades),
            r.short_win_rate,
            r.short_avg_r,
            run_at_ms,
        ],
    )
    return combo_id


def list_cross_tf_combo_runs(
    conn: duckdb.DuckDBPyConnection,
) -> "pd.DataFrame":
    """Return all backtest_cross_tf_combos rows sorted newest-first."""
    return conn.execute(
        "SELECT * FROM backtest_cross_tf_combos ORDER BY run_at_ms DESC"
    ).df()


def get_cross_tf_combo_lookup(
    conn: duckdb.DuckDBPyConnection,
) -> "dict[tuple[str, str, str, str, str], dict[str, Any]]":
    """Build a lookup dict for live cross-TF co-fire detection.

    Keyed by (symbol, tf_htf, tf_ltf, strategy_htf, strategy_ltf) → the row
    with the highest avg_r for that ordered pair (across all day_filter values).
    The key is ordered (not frozenset) because HTF/LTF roles are distinct.

    Returns an empty dict when no cross-TF combo runs have been saved yet.
    """
    df = list_cross_tf_combo_runs(conn)
    if df.empty:
        return {}
    lookup: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in df.to_dict("records"):
        key: tuple[str, str, str, str, str] = (
            str(row["symbol"]),
            str(row["tf_htf"]),
            str(row["tf_ltf"]),
            str(row["strategy_htf"]),
            str(row["strategy_ltf"]),
        )
        avg_r = float(row["avg_r"])
        if key not in lookup or avg_r > lookup[key]["avg_r"]:
            lookup[key] = {
                "avg_r": avg_r,
                "win_rate": float(row["win_rate"]),
                "closed_trades": int(row["closed_trades"]),
                "strategy_htf": str(row["strategy_htf"]),
                "strategy_ltf": str(row["strategy_ltf"]),
                "tf_htf": str(row["tf_htf"]),
                "tf_ltf": str(row["tf_ltf"]),
                "window_hours": float(row["window_hours"]),
            }
    return lookup

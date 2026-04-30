"""signals + signal_alert_outcomes table accessors."""

from typing import Any

import duckdb
import pandas as pd

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

"""Open-interest archive tables (#936): upserts, the merged read, coverage queries.

The archive is Binance's public `data.binance.vision` `futures/um/daily/metrics`
dump, loaded by `analytics/oi_archive.py`. It lives BESIDE `open_interest` rather
than inside it: that table's key is `(symbol, timestamp)` with no source column, so
adding one would be a migration of the live database, and a schema change to a
table the daemon writes takes the daemon down. Instead:

- `source` is part of every archive PRIMARY KEY, so an archive write can never touch
  a REST row, and a REST write can never touch an archive row.
- `open_interest` (REST) stays the primary series. `get_open_interest_merged` is the
  documented read rule: REST wins at an identical timestamp, the archive fills the
  timestamps REST does not have, and every row says which it came from.

⚠ `_upsert` emits `INSERT OR REPLACE ... SELECT <columns>` with no INSERT column
list, so the column strings below must stay in the tables' declared order.
"""

import duckdb
import pandas as pd

from analytics.store._common import _upsert

ARCHIVE_SOURCE = "vision_um_metrics"
REST_SOURCE = "rest"

HOURLY_COLUMNS: list[str] = [
    "symbol",
    "timestamp",
    "oi_usd",
    "oi_contracts",
    "toptrader_count_ls_ratio",
    "toptrader_sum_ls_ratio",
    "count_ls_ratio",
]
FIVE_MIN_COLUMNS: list[str] = [*HOURLY_COLUMNS, "taker_ls_vol_ratio"]
DAY_STATUS_OK = "ok"
DAY_STATUS_MISSING = "missing"


def upsert_oi_archive_hourly(
    conn: duckdb.DuckDBPyConnection, df: pd.DataFrame, *, source: str = ARCHIVE_SOURCE
) -> None:
    """Insert or replace hourly archive rows; df carries `HOURLY_COLUMNS`."""
    if df.empty:
        return
    _upsert(
        conn,
        df[HOURLY_COLUMNS].assign(source=source),
        "open_interest_archive",
        "source, " + ", ".join(HOURLY_COLUMNS),
    )


def upsert_oi_archive_5m(
    conn: duckdb.DuckDBPyConnection, df: pd.DataFrame, *, source: str = ARCHIVE_SOURCE
) -> None:
    """Insert or replace 5-minute archive rows; df carries `FIVE_MIN_COLUMNS`."""
    if df.empty:
        return
    _upsert(
        conn,
        df[FIVE_MIN_COLUMNS].assign(source=source),
        "open_interest_archive_5m",
        "source, " + ", ".join(FIVE_MIN_COLUMNS),
    )


def upsert_oi_archive_days(
    conn: duckdb.DuckDBPyConnection,
    rows: pd.DataFrame,
    *,
    source: str = ARCHIVE_SOURCE,
) -> None:
    """Insert or replace day-ledger rows: symbol, day, status, n_rows, fetched_at_ms."""
    if rows.empty:
        return
    _upsert(
        conn,
        rows[["symbol", "day", "status", "n_rows", "fetched_at_ms"]].assign(
            source=source
        ),
        "open_interest_archive_days",
        "source, symbol, day, status, n_rows, fetched_at_ms",
    )


def get_oi_archive_day_status(
    conn: duckdb.DuckDBPyConnection, symbol: str, *, source: str = ARCHIVE_SOURCE
) -> dict[str, str]:
    """Return {day 'YYYY-MM-DD': status} for every ledgered day of `symbol`."""
    rows = conn.execute(
        "SELECT day, status FROM open_interest_archive_days "
        "WHERE source = ? AND symbol = ?",
        [source, symbol],
    ).fetchall()
    return {str(d): str(s) for d, s in rows}


def get_open_interest_merged(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    start: int,
    end: int,
    *,
    archive_source: str = ARCHIVE_SOURCE,
) -> pd.DataFrame:
    """Hourly OI for `symbol`, REST first and the archive filling what REST lacks.

    READ RULE: at an identical `timestamp` the REST row wins and the archive row is
    dropped; nothing is blended or averaged. `source` on each output row is `'rest'`
    or the archive source, so a consumer that needs one pure series filters on it.
    Window is Unix ms, inclusive on both ends, like `get_open_interest`.
    """
    return conn.execute(
        "SELECT symbol, timestamp, oi_usd, ? AS source FROM open_interest "
        "WHERE symbol = ? AND timestamp >= ? AND timestamp <= ? "
        "UNION ALL "
        "SELECT a.symbol, a.timestamp, a.oi_usd, a.source FROM open_interest_archive a "
        "WHERE a.source = ? AND a.symbol = ? AND a.timestamp >= ? AND a.timestamp <= ? "
        "AND NOT EXISTS (SELECT 1 FROM open_interest o "
        "WHERE o.symbol = a.symbol AND o.timestamp = a.timestamp) "
        "ORDER BY timestamp",
        [REST_SOURCE, symbol, start, end, archive_source, symbol, start, end],
    ).df()

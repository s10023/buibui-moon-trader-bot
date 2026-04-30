"""stats_cache table accessors."""

import duckdb


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

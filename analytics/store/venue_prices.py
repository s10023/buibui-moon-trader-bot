"""Venue spot daily closes — additive store for the H14 premium state tag."""

from __future__ import annotations

import duckdb
import pandas as pd

from analytics.store._common import _upsert

_COLUMNS = "venue, symbol, open_time, close"


def upsert_venue_spot_daily(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """Insert-or-replace daily closes keyed on (venue, symbol, open_time)."""
    _upsert(conn, df, "venue_spot_daily", _COLUMNS)


def get_venue_spot_daily(
    conn: duckdb.DuckDBPyConnection, venue: str, symbol: str
) -> pd.DataFrame:
    """All stored closes for one (venue, symbol), ascending by open_time."""
    return conn.execute(
        f"SELECT {_COLUMNS} FROM venue_spot_daily "
        "WHERE venue = ? AND symbol = ? ORDER BY open_time",
        [venue, symbol],
    ).df()

"""Venue preference for the `ohlcv` read path.

`ohlcv_all` holds every venue's bars; the view named `ohlcv` exposes ONE bar per
(symbol, timeframe, open_time), chosen by a preference order stored in `db_meta`.
The order lives in the DATABASE rather than the process environment: a view
definition is shared schema state, so deriving it from `DATA_SOURCE` would let a
stray process change what every later reader sees.
"""

import re

import duckdb

from analytics.data_fetcher import OHLCV_COLUMNS as _DATA_FETCHER_OHLCV_COLUMNS

DEFAULT_VENUE: str = "binance"
READ_VENUE_ORDER_KEY: str = "read_venue_order"

# Derived from analytics.data_fetcher.OHLCV_COLUMNS (the pre-existing `list[str]`
# form used by the fetch path) rather than restated, so the two can never drift out
# of column order relative to each other. tools/migrate_ohlcv_venue.py derives its
# legacy-schema guard from this string form; analytics/data_fetcher.py's list form
# is what tests/test_analytics_runner.py imports.
OHLCV_COLUMNS: str = ", ".join(_DATA_FETCHER_OHLCV_COLUMNS)

# Venue names are interpolated into generated SQL (DuckDB cannot parameterise a view
# body), so this pattern is a security boundary rather than a naming preference.
# `fullmatch` (not `match` + `$`) is deliberate: `$` matches immediately before a
# trailing "\n", so `match(r"^[a-z0-9_]+$")` accepts "binance\n".
_VENUE_RE = re.compile(r"[a-z0-9_]+")


def _validate_venue_order(order: list[str], raw: str) -> None:
    """Shared validation for both entry points into generated SQL.

    `parse_venue_order` and `ohlcv_view_sql` are independent entry points into
    generated SQL — DuckDB cannot parameterise a view body, so both must reject the
    same inputs or one becomes a weaker validator than the other by accident.
    """
    if not order:
        raise ValueError("venue order is empty")
    for venue in order:
        if not _VENUE_RE.fullmatch(venue):
            raise ValueError(f"invalid venue name: {venue!r}")
    if len(set(order)) != len(order):
        raise ValueError(f"duplicate venue in order: {raw!r}")


def parse_venue_order(raw: str) -> list[str]:
    """Parse a comma-separated preference list into normalised venue names."""
    order = [part.strip().lower() for part in raw.split(",")]
    order = [part for part in order if part]
    _validate_venue_order(order, raw)
    return order


def ohlcv_view_sql(order: list[str]) -> str:
    """Return the CREATE OR REPLACE VIEW statement for the `ohlcv` read path."""
    _validate_venue_order(order, ",".join(order))
    if len(order) == 1:
        # A plain filter, deliberately: the single-venue production case must not pay
        # for a window function it can never need.
        body = f"SELECT {OHLCV_COLUMNS} FROM ohlcv_all WHERE venue = '{order[0]}'"
    else:
        venues = ", ".join(f"'{v}'" for v in order)
        cases = " ".join(f"WHEN '{v}' THEN {i}" for i, v in enumerate(order))
        body = (
            f"SELECT {OHLCV_COLUMNS} FROM ohlcv_all "
            f"WHERE venue IN ({venues}) "
            "QUALIFY row_number() OVER ("
            "PARTITION BY symbol, timeframe, open_time "
            f"ORDER BY CASE venue {cases} END) = 1"
        )
    return f"CREATE OR REPLACE VIEW ohlcv AS {body}"


def read_venue_order(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """Return the stored read preference order, defaulting to Binance alone."""
    row = conn.execute(
        "SELECT value FROM db_meta WHERE key = ?", [READ_VENUE_ORDER_KEY]
    ).fetchone()
    if row is None:
        return [DEFAULT_VENUE]
    return parse_venue_order(str(row[0]))


def set_read_venue_order(conn: duckdb.DuckDBPyConnection, order: list[str]) -> None:
    """Store the read preference order (validated first) and rebuild the view.

    A stored order and a live view must never disagree: without the rebuild here, a
    later call could stamp a new order onto a database whose `ohlcv` view was already
    built from the old one, and every reader would silently see stale preference
    until something else happened to run `ohlcv_view_sql` again. The rebuild is
    skipped when `ohlcv_all` does not exist yet — the `db_meta`-only unit-test shape,
    and every database before the venue migration runs — so this stays a pure
    metadata write in that case rather than failing on a table it has nothing to
    build the view against.
    """
    validated = parse_venue_order(",".join(order))
    conn.execute(
        "INSERT OR REPLACE INTO db_meta (key, value) VALUES (?, ?)",
        [READ_VENUE_ORDER_KEY, ",".join(validated)],
    )
    tables = {
        row[0]
        for row in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
    }
    if "ohlcv_all" in tables:
        conn.execute(ohlcv_view_sql(validated))

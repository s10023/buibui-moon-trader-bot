"""OHLCV / funding rates / open interest table accessors."""

from collections.abc import Iterable
from itertools import groupby
from typing import NamedTuple

import duckdb
import pandas as pd

from analytics.store._common import _upsert


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


def upsert_symbol_lifecycle(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """Insert or replace symbol lifecycle rows (N3 survivorship guard).

    df must have columns: symbol, status, onboard_ms, first_checked_ms,
    last_checked_ms, delisted_noted_ms. Conflicts on (symbol) are replaced.
    """
    _upsert(
        conn,
        df,
        "symbol_lifecycle",
        "symbol, status, onboard_ms, first_checked_ms, last_checked_ms, delisted_noted_ms",
    )


def get_symbol_lifecycle(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Return all symbol lifecycle rows ordered by symbol."""
    return conn.execute(
        "SELECT symbol, status, onboard_ms, first_checked_ms, last_checked_ms, "
        "delisted_noted_ms FROM symbol_lifecycle ORDER BY symbol"
    ).df()


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


# ---------------------------------------------------------------- fabricated CVD
# `upsert_ohlcv` above REPLACES on conflict and the `ohlcv` primary key carries NO
# venue component (`schema.py`), while the OKX adapter fabricates
# `taker_buy_volume = volume / 2` because OKX publishes no taker-buy split
# (`utils/okx_client.py`). So a `DATA_SOURCE=okx` run against the real DB does not
# land BESIDE the Binance bars, it overwrites them -- and silently, because OHLC and
# volume agree closely across venues and the only field that changes is the one CVD
# is computed from. The rows stay recoverable by re-backfilling from Binance, so the
# binding gap is DETECTION: nothing marks them and no other gate reads them.
#
# `venue_spot_daily` in the same schema file already declares
# `PRIMARY KEY (venue, symbol, open_time)`, which is the shape that would make the
# collision unreachable rather than merely detectable. That migration touches the
# backtest surface and is deliberately NOT what this code does.

FABRICATED_CVD_SQL = """
    SELECT symbol, timeframe, open_time
    FROM ohlcv
    WHERE volume > 0 AND taker_buy_volume = volume / 2.0
    ORDER BY symbol, timeframe, open_time
"""
"""Bars whose taker-buy split is EXACTLY half the volume.

``volume > 0`` is load-bearing rather than tidy: a zero-volume bar stores
``taker_buy_volume = 0``, which satisfies ``0 = 0 / 2`` trivially. There are 1,136 of
them on the live DB (measured 2026-08-21), so without the filter the check reports a
four-figure standing count and becomes a line nobody reads.
"""

# Bar length per timeframe, used only to decide whether two flagged bars are ADJACENT.
# `analytics.exits.audit` keeps a private copy of the same table without `1w`; this one
# is local on purpose rather than imported across packages, and an absent entry is
# handled below rather than assumed away.
_BAR_MS = {
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}

_RECENT_DAYS = 90
_DAY_MS = 86_400_000


class NeutralCvdRun(NamedTuple):
    """A maximal group of adjacent bars whose CVD delta is exactly neutral."""

    symbol: str
    timeframe: str
    first_open_time: int
    last_open_time: int
    bars: int


def neutral_cvd_runs(rows: Iterable[tuple[str, str, int]]) -> list[NeutralCvdRun]:
    """Group the rows returned by :data:`FABRICATED_CVD_SQL` into runs of adjacent bars.

    Takes rows rather than a connection so the caller can supply them from a snapshot
    copy of the DB -- reading the live file fails outright while the signal daemon
    holds it.

    An UNKNOWN timeframe merges unconditionally, so its bars are reported as one run
    instead of dissolving into singles. That is the fail-safe direction: a missing
    entry in `_BAR_MS` over-reports rather than hiding a fabricated block.
    """
    runs: list[NeutralCvdRun] = []
    # Sorted here rather than relying on the SQL's ORDER BY, so a caller reading the
    # rows some other way cannot silently break the adjacency test.
    for (symbol, timeframe), group in groupby(sorted(rows), key=lambda r: (r[0], r[1])):
        bar_ms = _BAR_MS.get(timeframe)
        first = last = None
        count = 0
        for _, _, open_time in group:
            adjacent = last is not None and (
                bar_ms is None or open_time - last <= bar_ms
            )
            if adjacent:
                count += 1
            else:
                if first is not None and last is not None:
                    runs.append(NeutralCvdRun(symbol, timeframe, first, last, count))
                first, count = open_time, 1
            last = open_time
        if first is not None and last is not None:
            runs.append(NeutralCvdRun(symbol, timeframe, first, last, count))
    return runs


def suspect_neutral_cvd(
    rows: Iterable[tuple[str, str, int]],
    *,
    now_ms: int,
    recent_days: int = _RECENT_DAYS,
) -> list[NeutralCvdRun]:
    """Runs that look FABRICATED rather than coincidental.

    A real bar whose taker-buy volume happens to land on exactly half the total is a
    coincidence, and a coincidence is ISOLATED; the OKX adapter writes whatever window
    it fetched, so fabrication is CONTIGUOUS. Two signatures follow, and each has a
    measured zero baseline on the live DB (2,003,268 rows, 2026-08-21):

    * **a run of 2+ adjacent bars** -- catches a deep ``backfill``, which rewrites
      history and would sit entirely outside any recency window. All 7 coincidental
      bars on the live DB are isolated; the longest run is 1.
    * **any bar newer than `recent_days`** -- catches a single fabricated candle from
      an incremental ``sync``, which the run test alone would read as a coincidence.
      The newest coincidental bar is 2024-02-25, roughly 1.5 years outside the window.

    Neither signature needs a pinned baseline count to compare against, which is what
    keeps this a binary gate rather than a number someone has to eyeball each morning.
    """
    cutoff = now_ms - recent_days * _DAY_MS
    return [
        run
        for run in neutral_cvd_runs(rows)
        if run.bars >= 2 or run.last_open_time >= cutoff
    ]

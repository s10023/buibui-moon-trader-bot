"""Average Daily Range (ADR) statistics."""

from dataclasses import dataclass

import duckdb

from analytics.stats._common import _start_ms


@dataclass
class ADRResult:
    """Average Daily Range statistics."""

    adr_14: float  # 14-day avg (high-low)/open
    adr_30: float  # 30-day avg (high-low)/open
    today_range_pct: float | None  # today's (high-low)/open so far
    today_consumed_pct: float | None  # today_range / adr_14
    today_move_up: bool | None = (
        None  # True if latest close is in upper half of today's range
    )


def compute_adr(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
) -> ADRResult:
    """Compute Average Daily Range for 14-day and 30-day windows plus today's range.

    Raises ValueError if no OHLCV data exists for the symbol.
    """
    # We need 30 days back for the 30-day ADR, but also today's data
    start = _start_ms(35)  # 35 days gives us buffer for 30-day calc

    rows = conn.execute(
        """
        WITH daily AS (
            SELECT
                (epoch_ms(open_time)::TIMESTAMP)::DATE AS trade_date,
                MAX(high) AS day_high, MIN(low) AS day_low,
                FIRST(open ORDER BY open_time) AS day_open,
                LAST(close ORDER BY open_time) AS day_close
            FROM ohlcv
            WHERE symbol = $symbol AND timeframe = '1h'
              AND open_time >= $start_ms
            GROUP BY trade_date
            ORDER BY trade_date DESC
        )
        SELECT trade_date, day_high, day_low, day_open, day_close
        FROM daily
        """,
        {"symbol": symbol, "start_ms": start},
    ).fetchall()

    if not rows:
        raise ValueError(f"No OHLCV data for {symbol}")

    # rows are ordered newest first
    ranges = [
        (float(day_high) - float(day_low)) / float(day_open)
        for (_, day_high, day_low, day_open, _close) in rows
        if float(day_open) > 0
    ]

    if not ranges:
        raise ValueError(f"No OHLCV data for {symbol}")

    adr_14 = sum(ranges[:14]) / min(14, len(ranges))
    adr_30 = sum(ranges[:30]) / min(30, len(ranges))

    # Today's range: use the most recent date in rows (rows[0] is the newest day)
    # rows[0] may be today (partial) or yesterday (if before today's candles sync)
    today_range_pct: float | None = None
    today_consumed_pct: float | None = None
    today_move_up: bool | None = None
    if rows:
        newest_day_high = float(rows[0][1])
        newest_day_low = float(rows[0][2])
        newest_day_open = float(rows[0][3])
        newest_day_close = float(rows[0][4])
        if newest_day_open > 0:
            today_range_pct = (newest_day_high - newest_day_low) / newest_day_open
            if adr_14 > 0:
                today_consumed_pct = today_range_pct / adr_14
        if newest_day_high != newest_day_low:
            mid = (newest_day_high + newest_day_low) / 2
            today_move_up = newest_day_close > mid

    return ADRResult(
        adr_14=adr_14,
        adr_30=adr_30,
        today_range_pct=today_range_pct,
        today_consumed_pct=today_consumed_pct,
        today_move_up=today_move_up,
    )

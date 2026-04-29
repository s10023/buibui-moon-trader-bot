"""Hourly extreme distribution — which MYT hour most often makes daily H/L."""

from dataclasses import dataclass

import duckdb

from analytics.stats._common import _DOW_SHORT, _start_ms


@dataclass
class HourlyExtremeRow:
    """Hourly extreme frequency for a single MYT hour."""

    hour_myt: int
    high_pct: float  # fraction of days this hour made the daily high
    low_pct: float  # fraction of days this hour made the daily low


@dataclass
class HourlyResult:
    """Hourly extreme distribution across all 24 MYT hours."""

    rows: list[HourlyExtremeRow]  # len=24
    peak_high_hour: int
    peak_low_hour: int
    peak_high_hour_by_dow: dict[
        str, int
    ]  # "Mon" → hour that most often makes daily high
    peak_low_hour_by_dow: dict[str, int]  # "Mon" → hour that most often makes daily low


def compute_hourly_extremes(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    days: int = 180,
) -> HourlyResult:
    """Compute hourly extreme distribution: which MYT hour most often makes daily high/low.

    Raises ValueError if no OHLCV data exists for the symbol.
    """
    start = _start_ms(days)
    rows = conn.execute(
        """
        WITH hourly AS (
            SELECT
                open_time, high, low,
                HOUR((epoch_ms(open_time) + INTERVAL 8 HOUR)::TIMESTAMP) AS hour_myt,
                (epoch_ms(open_time)::TIMESTAMP)::DATE                   AS trade_date
            FROM ohlcv WHERE symbol = $symbol AND timeframe = '1h' AND open_time >= $start_ms
        ),
        daily_ext AS (
            SELECT trade_date, MAX(high) AS day_high, MIN(low) AS day_low
            FROM hourly GROUP BY trade_date
        ),
        day_first_hour AS (
            SELECT
                h.trade_date,
                FIRST(h.hour_myt ORDER BY h.open_time)
                    FILTER (WHERE h.high = de.day_high) AS high_hour,
                FIRST(h.hour_myt ORDER BY h.open_time)
                    FILTER (WHERE h.low  = de.day_low)  AS low_hour
            FROM hourly h JOIN daily_ext de ON h.trade_date = de.trade_date
            GROUP BY h.trade_date
        ),
        total_days AS (SELECT COUNT(*) AS n FROM day_first_hour)
        SELECT
            g.hour_myt,
            SUM(CASE WHEN dfh.high_hour = g.hour_myt THEN 1 ELSE 0 END)::DOUBLE / t.n AS high_pct,
            SUM(CASE WHEN dfh.low_hour  = g.hour_myt THEN 1 ELSE 0 END)::DOUBLE / t.n AS low_pct
        FROM generate_series(0, 23) g(hour_myt)
        CROSS JOIN total_days t
        LEFT JOIN day_first_hour dfh ON TRUE
        GROUP BY g.hour_myt, t.n
        ORDER BY g.hour_myt
        """,
        {"symbol": symbol, "start_ms": start},
    ).fetchall()

    if not rows:
        raise ValueError(f"No OHLCV data for {symbol}")

    hourly_rows = [
        HourlyExtremeRow(
            hour_myt=int(hour),
            high_pct=float(high_pct) if high_pct is not None else 0.0,
            low_pct=float(low_pct) if low_pct is not None else 0.0,
        )
        for (hour, high_pct, low_pct) in rows
    ]

    peak_high_hour = max(hourly_rows, key=lambda r: r.high_pct).hour_myt
    peak_low_hour = max(hourly_rows, key=lambda r: r.low_pct).hour_myt

    # Per-DOW peak hours: MODE(high_hour) / MODE(low_hour) grouped by day-of-week
    dow_peak_rows = conn.execute(
        """
        WITH hourly AS (
            SELECT
                open_time, high, low,
                HOUR((epoch_ms(open_time) + INTERVAL 8 HOUR)::TIMESTAMP) AS hour_myt,
                (epoch_ms(open_time)::TIMESTAMP)::DATE                   AS trade_date,
                dayname((epoch_ms(open_time)::TIMESTAMP)::DATE)          AS dow
            FROM ohlcv WHERE symbol = $symbol AND timeframe = '1h' AND open_time >= $start_ms
        ),
        daily_ext AS (
            SELECT trade_date, MAX(high) AS day_high, MIN(low) AS day_low
            FROM hourly GROUP BY trade_date
        ),
        day_first_hour AS (
            SELECT
                h.trade_date, h.dow,
                FIRST(h.hour_myt ORDER BY h.open_time)
                    FILTER (WHERE h.high = de.day_high) AS high_hour,
                FIRST(h.hour_myt ORDER BY h.open_time)
                    FILTER (WHERE h.low  = de.day_low)  AS low_hour
            FROM hourly h JOIN daily_ext de ON h.trade_date = de.trade_date
            GROUP BY h.trade_date, h.dow
        )
        SELECT dow, MODE(high_hour) AS peak_high, MODE(low_hour) AS peak_low
        FROM day_first_hour
        WHERE high_hour IS NOT NULL AND low_hour IS NOT NULL
        GROUP BY dow
        """,
        {"symbol": symbol, "start_ms": start},
    ).fetchall()

    peak_high_hour_by_dow: dict[str, int] = {}
    peak_low_hour_by_dow: dict[str, int] = {}
    for dow_full, ph, pl in dow_peak_rows:
        short = _DOW_SHORT.get(str(dow_full), str(dow_full)[:3])
        if ph is not None:
            peak_high_hour_by_dow[short] = int(ph)
        if pl is not None:
            peak_low_hour_by_dow[short] = int(pl)

    return HourlyResult(
        rows=hourly_rows,
        peak_high_hour=peak_high_hour,
        peak_low_hour=peak_low_hour,
        peak_high_hour_by_dow=peak_high_hour_by_dow,
        peak_low_hour_by_dow=peak_low_hour_by_dow,
    )

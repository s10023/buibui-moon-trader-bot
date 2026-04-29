"""Day-of-week pattern statistics."""

from dataclasses import dataclass

import duckdb

from analytics.stats._common import _DOW_SHORT, _start_ms


@dataclass
class DOWRow:
    """Day-of-week pattern statistics."""

    dow: str  # "Mon" … "Sun"
    avg_range_pct: float
    bull_pct: float  # % days close > open
    sample_days: int
    avg_return_pct: float = 0.0  # avg (close-open)/open — directional return
    strong_high_pct: float = 0.0  # fraction of days where upper wick < 20% of range
    strong_low_pct: float = 0.0  # fraction of days where lower wick < 20% of range


@dataclass
class DOWResult:
    """Day-of-week patterns for all 7 days."""

    rows: list[DOWRow]


def compute_dow_patterns(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    days: int = 180,
) -> DOWResult:
    """Compute day-of-week average range, bull percentage, and sample count.

    Raises ValueError if no OHLCV data exists for the symbol.
    """
    start = _start_ms(days)
    rows = conn.execute(
        """
        WITH daily AS (
            SELECT
                (epoch_ms(open_time)::TIMESTAMP)::DATE                   AS trade_date,
                dayname((epoch_ms(open_time)::TIMESTAMP)::DATE)          AS dow,
                MAX(high) AS day_high, MIN(low) AS day_low,
                FIRST(open ORDER BY open_time)  AS day_open,
                LAST(close ORDER BY open_time)  AS day_close
            FROM ohlcv
            WHERE symbol = $symbol AND timeframe = '1h'
              AND open_time >= $start_ms
            GROUP BY trade_date, dow
        )
        SELECT
            dow,
            AVG((day_high - day_low) / day_open) AS avg_range_pct,
            SUM(CASE WHEN day_close > day_open THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS bull_pct,
            COUNT(*) AS sample_days,
            AVG((day_close - day_open) / day_open) AS avg_return_pct,
            AVG(CASE
                WHEN (day_high - day_low) > 0 AND
                     (day_close - day_low) / (day_high - day_low) < 0.20
                THEN 1.0 ELSE 0.0
            END) AS strong_high_pct,
            AVG(CASE
                WHEN (day_high - day_low) > 0 AND
                     (day_high - day_close) / (day_high - day_low) < 0.20
                THEN 1.0 ELSE 0.0
            END) AS strong_low_pct
        FROM daily
        WHERE day_open > 0
        GROUP BY dow
        ORDER BY dow
        """,
        {"symbol": symbol, "start_ms": start},
    ).fetchall()

    if not rows:
        raise ValueError(f"No OHLCV data for {symbol}")

    dow_map: dict[str, DOWRow] = {}
    for dow_full, avg_range, bull_pct, n, avg_return, strong_high, strong_low in rows:
        short = _DOW_SHORT.get(str(dow_full), str(dow_full)[:3])
        dow_map[short] = DOWRow(
            dow=short,
            avg_range_pct=float(avg_range),
            bull_pct=float(bull_pct),
            sample_days=int(n),
            avg_return_pct=float(avg_return),
            strong_high_pct=float(strong_high),
            strong_low_pct=float(strong_low),
        )

    # Return in Mon–Sun order
    ordered_rows = [
        dow_map[s]
        for s in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        if s in dow_map
    ]
    return DOWResult(rows=ordered_rows)

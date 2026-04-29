"""Daily P1/P2 statistics — was the daily low made before the daily high?"""

from dataclasses import dataclass

import duckdb

from analytics.stats._common import _DOW_SHORT, _start_ms


@dataclass
class P1P2Result:
    """P1/P2 daily analysis — was the daily low made before the daily high?"""

    overall_p1_low_pct: float  # % of days where low came before high
    by_dow: dict[str, float]  # "Mon" → 0.58, "Tue" → 0.44, ...
    sample_days: int
    p1_strong_pct: float = (
        0.0  # fraction of P1 candles where P1 extreme wick < 20% of range
    )


def compute_p1p2_daily(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    days: int = 180,
) -> P1P2Result:
    """Compute daily P1/P2: fraction of days where daily low was made before daily high.

    Raises ValueError if no OHLCV data exists for the symbol.
    """
    start = _start_ms(days)
    rows = conn.execute(
        """
        WITH hourly AS (
            SELECT
                open_time, high, low, open, close,
                (epoch_ms(open_time)::TIMESTAMP)::DATE   AS trade_date,
                dayname((epoch_ms(open_time)::TIMESTAMP)::DATE) AS dow
            FROM ohlcv
            WHERE symbol = $symbol AND timeframe = '1h'
              AND open_time >= $start_ms
        ),
        daily_extremes AS (
            SELECT trade_date, dow,
                MAX(high) AS day_high, MIN(low) AS day_low,
                FIRST(open ORDER BY open_time) AS day_open,
                LAST(close ORDER BY open_time) AS day_close
            FROM hourly GROUP BY trade_date, dow
        ),
        first_hit AS (
            SELECT
                h.trade_date, de.dow,
                de.day_high, de.day_low, de.day_open, de.day_close,
                MIN(CASE WHEN h.high = de.day_high THEN h.open_time END) AS high_ts,
                MIN(CASE WHEN h.low  = de.day_low  THEN h.open_time END) AS low_ts
            FROM hourly h JOIN daily_extremes de ON h.trade_date = de.trade_date
            GROUP BY h.trade_date, de.dow, de.day_high, de.day_low, de.day_open, de.day_close
        )
        SELECT
            dow,
            SUM(CASE WHEN low_ts < high_ts THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS p1_low_pct,
            COUNT(*) AS n,
            AVG(CASE
                WHEN (day_high - day_low) > 0 AND low_ts < high_ts AND
                     (day_high - day_close) / (day_high - day_low) < 0.20 THEN 1.0
                WHEN (day_high - day_low) > 0 AND low_ts >= high_ts AND
                     (day_close - day_low) / (day_high - day_low) < 0.20 THEN 1.0
                ELSE 0.0
            END) AS p1_strong_pct
        FROM first_hit
        WHERE high_ts IS NOT NULL AND low_ts IS NOT NULL
        GROUP BY dow
        """,
        {"symbol": symbol, "start_ms": start},
    ).fetchall()

    if not rows:
        raise ValueError(f"No OHLCV data for {symbol}")

    by_dow: dict[str, float] = {}
    total_p1_sum = 0.0
    total_strong_sum = 0.0
    total_n = 0

    for dow_full, p1_pct, n, p1_strong in rows:
        short = _DOW_SHORT.get(str(dow_full), str(dow_full)[:3])
        by_dow[short] = float(p1_pct)
        total_p1_sum += float(p1_pct) * int(n)
        total_strong_sum += float(p1_strong) * int(n)
        total_n += int(n)

    overall = total_p1_sum / total_n if total_n > 0 else 0.0
    p1_strong_overall = total_strong_sum / total_n if total_n > 0 else 0.0
    return P1P2Result(
        overall_p1_low_pct=overall,
        by_dow=by_dow,
        sample_days=total_n,
        p1_strong_pct=p1_strong_overall,
    )

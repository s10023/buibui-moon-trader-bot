"""Weekly P1/P2 — which day of the week makes the weekly low/high first."""

from dataclasses import dataclass

import duckdb

from analytics.stats._common import _DOW_SHORT, _start_ms


@dataclass
class WeeklyP1P2Result:
    """Weekly P1/P2 — which day of the week makes the weekly low/high first."""

    overall_p1_low_pct: float  # % of weeks where weekly low came before weekly high
    low_day: str  # DOW most often making weekly low
    high_day: str  # DOW most often making weekly high
    low_by_dow: dict[str, float]  # fraction of weeks each DOW makes weekly low
    high_by_dow: dict[str, float]  # fraction of weeks each DOW makes weekly high
    sample_weeks: int


def compute_weekly_p1p2(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    days: int = 180,
) -> WeeklyP1P2Result:
    """Compute weekly P1/P2: fraction of weeks where weekly low was made before weekly high.

    Also identifies dominant day for weekly high and weekly low.
    Raises ValueError if no OHLCV data exists for the symbol.
    """
    start = _start_ms(days)
    rows = conn.execute(
        """
        WITH weekly AS (
            SELECT
                date_trunc('week', epoch_ms(open_time)::TIMESTAMP) AS week_start,
                MAX(high) AS wk_high, MIN(low) AS wk_low
            FROM ohlcv
            WHERE symbol = $symbol AND timeframe = '1h' AND open_time >= $start_ms
            GROUP BY week_start
        ),
        wk_first_hit AS (
            SELECT
                w.week_start,
                MIN(CASE WHEN h.high = w.wk_high THEN h.open_time END) AS high_ts,
                MIN(CASE WHEN h.low  = w.wk_low  THEN h.open_time END) AS low_ts,
                FIRST(dayname((epoch_ms(h.open_time)::TIMESTAMP)::DATE)
                      ORDER BY h.open_time)
                      FILTER (WHERE h.high = w.wk_high) AS high_day,
                FIRST(dayname((epoch_ms(h.open_time)::TIMESTAMP)::DATE)
                      ORDER BY h.open_time)
                      FILTER (WHERE h.low  = w.wk_low)  AS low_day
            FROM ohlcv h
            JOIN weekly w
              ON date_trunc('week', epoch_ms(h.open_time)::TIMESTAMP) = w.week_start
            WHERE h.symbol = $symbol AND h.timeframe = '1h' AND h.open_time >= $start_ms
            GROUP BY w.week_start
        )
        SELECT
            SUM(CASE WHEN low_ts < high_ts THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS p1_low_pct,
            MODE(high_day) AS dominant_high_day,
            MODE(low_day)  AS dominant_low_day,
            COUNT(*) AS sample_weeks
        FROM wk_first_hit
        WHERE high_ts IS NOT NULL AND low_ts IS NOT NULL
        """,
        {"symbol": symbol, "start_ms": start},
    ).fetchone()

    if rows is None or rows[3] == 0:
        raise ValueError(f"No OHLCV data for {symbol}")

    p1_low_pct, dominant_high_day, dominant_low_day, sample_weeks = rows

    # Compute per-DOW distribution for weekly high/low days
    dow_dist_rows = conn.execute(
        """
        WITH weekly AS (
            SELECT
                date_trunc('week', epoch_ms(open_time)::TIMESTAMP) AS week_start,
                MAX(high) AS wk_high, MIN(low) AS wk_low
            FROM ohlcv
            WHERE symbol = $symbol AND timeframe = '1h' AND open_time >= $start_ms
            GROUP BY week_start
        ),
        wk_first_hit AS (
            SELECT
                w.week_start,
                FIRST(dayname((epoch_ms(h.open_time)::TIMESTAMP)::DATE)
                      ORDER BY h.open_time)
                      FILTER (WHERE h.high = w.wk_high) AS high_day,
                FIRST(dayname((epoch_ms(h.open_time)::TIMESTAMP)::DATE)
                      ORDER BY h.open_time)
                      FILTER (WHERE h.low  = w.wk_low)  AS low_day
            FROM ohlcv h
            JOIN weekly w
              ON date_trunc('week', epoch_ms(h.open_time)::TIMESTAMP) = w.week_start
            WHERE h.symbol = $symbol AND h.timeframe = '1h' AND h.open_time >= $start_ms
            GROUP BY w.week_start
        ),
        total AS (SELECT COUNT(*) AS n FROM wk_first_hit WHERE high_day IS NOT NULL AND low_day IS NOT NULL)
        SELECT
            high_day,
            low_day,
            COUNT(*) AS n,
            t.n AS total_n
        FROM wk_first_hit CROSS JOIN total t
        WHERE high_day IS NOT NULL AND low_day IS NOT NULL
        GROUP BY high_day, low_day, t.n
        """,
        {"symbol": symbol, "start_ms": start},
    ).fetchall()

    total_wks = int(sample_weeks)
    high_by_dow: dict[str, float] = {}
    low_by_dow: dict[str, float] = {}

    # Aggregate from pairs
    high_counts: dict[str, int] = {}
    low_counts: dict[str, int] = {}
    for high_day, low_day, n, _total_n in dow_dist_rows:
        hd = _DOW_SHORT.get(str(high_day), str(high_day)[:3])
        ld = _DOW_SHORT.get(str(low_day), str(low_day)[:3])
        high_counts[hd] = high_counts.get(hd, 0) + int(n)
        low_counts[ld] = low_counts.get(ld, 0) + int(n)

    if total_wks > 0:
        high_by_dow = {dow: cnt / total_wks for dow, cnt in high_counts.items()}
        low_by_dow = {dow: cnt / total_wks for dow, cnt in low_counts.items()}

    dom_high = (
        _DOW_SHORT.get(str(dominant_high_day), str(dominant_high_day)[:3])
        if dominant_high_day
        else "N/A"
    )
    dom_low = (
        _DOW_SHORT.get(str(dominant_low_day), str(dominant_low_day)[:3])
        if dominant_low_day
        else "N/A"
    )

    return WeeklyP1P2Result(
        overall_p1_low_pct=float(p1_low_pct),
        low_day=dom_low,
        high_day=dom_high,
        low_by_dow=low_by_dow,
        high_by_dow=high_by_dow,
        sample_weeks=total_wks,
    )

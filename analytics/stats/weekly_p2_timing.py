"""Weekly P2 timing — for each DOW, fraction of weeks where weekly extreme is still ahead."""

from dataclasses import dataclass

import duckdb

from analytics.stats._common import _ISODOW_TO_SHORT, _start_ms


@dataclass
class WeeklyP2Timing:
    """Weekly P2 timing: fraction of weeks where weekly extreme is still to come.

    For a given day-of-week X, low_still_ahead_by_dow[X] = fraction of weeks
    where the weekly low was made AFTER day X (i.e. it is still ahead if today is X).
    """

    low_still_ahead_by_dow: dict[str, float]  # "Mon" → 0.78
    high_still_ahead_by_dow: dict[str, float]  # "Mon" → 0.65
    low_flip_risk_by_dow: dict[str, float]  # "Mon" → 0.42 (prob low forms AFTER Mon)
    high_flip_risk_by_dow: dict[str, float]  # "Mon" → 0.38


def compute_weekly_p2_timing(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    days: int = 180,
) -> WeeklyP2Timing:
    """Compute weekly P2 timing: for each DOW, fraction of weeks where weekly extreme is still ahead.

    For DOW X: low_still_ahead_by_dow[X] = % of weeks where weekly low was made AFTER day X.
    Uses ISODOW (1=Monday ... 7=Sunday).

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
        wk_extreme_dow AS (
            SELECT
                w.week_start,
                ISODOW(MIN(CASE WHEN h.high = w.wk_high
                    THEN (epoch_ms(h.open_time)::TIMESTAMP)::DATE END)) AS high_isodow,
                ISODOW(MIN(CASE WHEN h.low  = w.wk_low
                    THEN (epoch_ms(h.open_time)::TIMESTAMP)::DATE END)) AS low_isodow
            FROM ohlcv h
            JOIN weekly w
              ON date_trunc('week', epoch_ms(h.open_time)::TIMESTAMP) = w.week_start
            WHERE h.symbol = $symbol AND h.timeframe = '1h' AND h.open_time >= $start_ms
            GROUP BY w.week_start
        ),
        valid_weeks AS (
            SELECT high_isodow, low_isodow FROM wk_extreme_dow
            WHERE high_isodow IS NOT NULL AND low_isodow IS NOT NULL
        ),
        total AS (SELECT COUNT(*) AS n FROM valid_weeks)
        SELECT
            g.isodow,
            SUM(CASE WHEN vw.low_isodow  > g.isodow THEN 1 ELSE 0 END)::DOUBLE
                / NULLIF(t.n, 0) AS low_still_ahead,
            SUM(CASE WHEN vw.high_isodow > g.isodow THEN 1 ELSE 0 END)::DOUBLE
                / NULLIF(t.n, 0) AS high_still_ahead
        FROM generate_series(1, 7) g(isodow)
        CROSS JOIN total t
        LEFT JOIN valid_weeks vw ON TRUE
        GROUP BY g.isodow, t.n
        ORDER BY g.isodow
        """,
        {"symbol": symbol, "start_ms": start},
    ).fetchall()

    if not rows:
        raise ValueError(f"No OHLCV data for {symbol}")

    low_still_ahead: dict[str, float] = {}
    high_still_ahead: dict[str, float] = {}
    for isodow, low_pct, high_pct in rows:
        short = _ISODOW_TO_SHORT[int(isodow)]
        low_still_ahead[short] = float(low_pct) if low_pct is not None else 0.0
        high_still_ahead[short] = float(high_pct) if high_pct is not None else 0.0

    # Flip risk: given today is DOW X and the running P1 is already set,
    # what % of historical weeks saw a LOWER low (or HIGHER high) form after day X?
    flip_rows = conn.execute(
        """
        WITH hourly_base AS (
            SELECT
                date_trunc('week', (epoch_ms(open_time) + INTERVAL 8 HOUR)::TIMESTAMP)
                    AS week_start,
                ISODOW(((epoch_ms(open_time) + INTERVAL 8 HOUR)::TIMESTAMP)::DATE)
                    AS candle_isodow,
                high, low
            FROM ohlcv
            WHERE symbol = $symbol AND timeframe = '1h' AND open_time >= $start_ms
        ),
        daily_by_dow AS (
            SELECT week_start, candle_isodow,
                   MAX(high) AS dow_high, MIN(low) AS dow_low
            FROM hourly_base
            GROUP BY week_start, candle_isodow
        ),
        weekly_extremes AS (
            SELECT week_start,
                   MAX(dow_high) AS wk_high,
                   MIN(dow_low)  AS wk_low
            FROM daily_by_dow GROUP BY week_start
        ),
        cumulative AS (
            SELECT d.week_start, d.candle_isodow,
                   MIN(d.dow_low)  OVER (PARTITION BY d.week_start
                                        ORDER BY d.candle_isodow
                                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                       AS cum_low,
                   MAX(d.dow_high) OVER (PARTITION BY d.week_start
                                        ORDER BY d.candle_isodow
                                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                       AS cum_high
            FROM daily_by_dow d
        ),
        with_flips AS (
            SELECT c.candle_isodow,
                   (we.wk_low  < c.cum_low)::INT  AS low_flip,
                   (we.wk_high > c.cum_high)::INT AS high_flip
            FROM cumulative c
            JOIN weekly_extremes we ON c.week_start = we.week_start
        )
        SELECT candle_isodow,
               SUM(low_flip)::DOUBLE  / NULLIF(COUNT(*), 0) AS low_flip_risk,
               SUM(high_flip)::DOUBLE / NULLIF(COUNT(*), 0) AS high_flip_risk
        FROM with_flips
        GROUP BY candle_isodow
        ORDER BY candle_isodow
        """,
        {"symbol": symbol, "start_ms": start},
    ).fetchall()

    low_flip_risk: dict[str, float] = {}
    high_flip_risk: dict[str, float] = {}
    for isodow, low_fr, high_fr in flip_rows:
        short = _ISODOW_TO_SHORT[int(isodow)]
        low_flip_risk[short] = float(low_fr) if low_fr is not None else 0.0
        high_flip_risk[short] = float(high_fr) if high_fr is not None else 0.0

    return WeeklyP2Timing(
        low_still_ahead_by_dow=low_still_ahead,
        high_still_ahead_by_dow=high_still_ahead,
        low_flip_risk_by_dow=low_flip_risk,
        high_flip_risk_by_dow=high_flip_risk,
    )

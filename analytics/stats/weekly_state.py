"""Live current-week position with distance-conditioned P2 probability."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import duckdb

from analytics.stats._common import _ISODOW_TO_SHORT, MYT_OFFSET_HOURS, _start_ms


@dataclass
class WeeklyCurrentState:
    """Live current-week position — distance-conditioned P2 probability.

    Not cached — computed fresh on every API request.
    """

    current_isodow: int  # 1=Mon … 7=Sun
    current_dow: str  # "Mon"
    weekly_open: float  # first 1h candle open of current ISO week (MYT)
    current_price: float  # latest 1h close
    move_pct: float  # (current_price - weekly_open) / weekly_open
    move_bucket: str  # "small" | "medium" | "large"
    low_still_ahead_conditioned: float | None  # P(low still ahead | DOW, bucket)
    high_still_ahead_conditioned: float | None  # P(high still ahead | DOW, bucket)


def compute_weekly_current_state(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    adr_14: float,
    days: int = 180,
) -> "WeeklyCurrentState | None":
    """Compute live current-week state with distance-conditioned P2 probability.

    Returns None if the current week has no OHLCV data yet.
    Not intended for caching — call fresh on every API request.

    move_bucket thresholds (symbol-agnostic via ADR14 normalisation):
        small  = |move_pct| < 1× adr_14
        medium = |move_pct| < 2× adr_14
        large  = |move_pct| >= 2× adr_14
    """
    now_utc = datetime.now(tz=UTC)
    now_myt = now_utc + timedelta(hours=MYT_OFFSET_HOURS)

    # Current ISO day-of-week in MYT (1=Mon … 7=Sun)
    current_isodow = now_myt.isoweekday()
    current_dow = _ISODOW_TO_SHORT[current_isodow]

    # Monday 00:00 MYT of current week → UTC ms for DB query
    days_since_monday = now_myt.weekday()  # 0 = Monday
    week_start_myt = (now_myt - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_start_ms = int(
        (week_start_myt - timedelta(hours=MYT_OFFSET_HOURS)).timestamp() * 1000
    )

    # Weekly open = first 1h candle open on/after week start
    open_row = conn.execute(
        """
        SELECT open FROM ohlcv
        WHERE symbol = $symbol AND timeframe = '1h' AND open_time >= $week_start
        ORDER BY open_time ASC LIMIT 1
        """,
        {"symbol": symbol, "week_start": week_start_ms},
    ).fetchone()
    if open_row is None:
        return None
    weekly_open = float(open_row[0])

    # Current price = latest 1h close within the current week
    price_row = conn.execute(
        """
        SELECT close FROM ohlcv
        WHERE symbol = $symbol AND timeframe = '1h' AND open_time >= $week_start
        ORDER BY open_time DESC LIMIT 1
        """,
        {"symbol": symbol, "week_start": week_start_ms},
    ).fetchone()
    if price_row is None:
        return None
    current_price = float(price_row[0])

    move_pct = (current_price - weekly_open) / weekly_open
    abs_move = abs(move_pct)
    if abs_move < adr_14:
        move_bucket = "small"
    elif abs_move < 2.0 * adr_14:
        move_bucket = "medium"
    else:
        move_bucket = "large"

    # Historical conditioned probability lookup:
    # P(low/high still ahead | today=DOW X AND weekly move so far = bucket Y)
    start_ms = _start_ms(days)
    adr_medium = 2.0 * adr_14

    lookup_rows = conn.execute(
        """
        WITH weekly AS (
            SELECT
                date_trunc('week', (epoch_ms(open_time) + INTERVAL 8 HOUR)::TIMESTAMP)
                    AS week_myt,
                MAX(high) AS wk_high, MIN(low) AS wk_low
            FROM ohlcv
            WHERE symbol = $symbol AND timeframe = '1h' AND open_time >= $start_ms
            GROUP BY week_myt
        ),
        wk_extreme_dow AS (
            SELECT
                w.week_myt,
                ISODOW(MIN(CASE WHEN h.high = w.wk_high
                    THEN ((epoch_ms(h.open_time) + INTERVAL 8 HOUR)::TIMESTAMP)::DATE
                    END)) AS high_isodow,
                ISODOW(MIN(CASE WHEN h.low = w.wk_low
                    THEN ((epoch_ms(h.open_time) + INTERVAL 8 HOUR)::TIMESTAMP)::DATE
                    END)) AS low_isodow
            FROM ohlcv h
            JOIN weekly w
              ON date_trunc('week', (epoch_ms(h.open_time) + INTERVAL 8 HOUR)::TIMESTAMP)
                 = w.week_myt
            WHERE h.symbol = $symbol AND h.timeframe = '1h' AND h.open_time >= $start_ms
            GROUP BY w.week_myt
        ),
        wk_first_times AS (
            SELECT
                date_trunc('week', (epoch_ms(open_time) + INTERVAL 8 HOUR)::TIMESTAMP)
                    AS week_myt,
                MIN(open_time) AS first_open_time
            FROM ohlcv
            WHERE symbol = $symbol AND timeframe = '1h' AND open_time >= $start_ms
            GROUP BY week_myt
        ),
        weekly_opens AS (
            SELECT f.week_myt, o.open AS week_open
            FROM wk_first_times f
            JOIN ohlcv o ON o.open_time = f.first_open_time
            WHERE o.symbol = $symbol AND o.timeframe = '1h'
        ),
        dow_last_times AS (
            SELECT
                date_trunc('week', (epoch_ms(open_time) + INTERVAL 8 HOUR)::TIMESTAMP)
                    AS week_myt,
                ISODOW(((epoch_ms(open_time) + INTERVAL 8 HOUR)::TIMESTAMP)::DATE)
                    AS candle_isodow,
                MAX(open_time) AS last_open_time
            FROM ohlcv
            WHERE symbol = $symbol AND timeframe = '1h' AND open_time >= $start_ms
            GROUP BY week_myt, candle_isodow
        ),
        dow_eod AS (
            SELECT d.week_myt, d.candle_isodow, o.close AS eod_close
            FROM dow_last_times d
            JOIN ohlcv o ON o.open_time = d.last_open_time
            WHERE o.symbol = $symbol AND o.timeframe = '1h'
        ),
        joined AS (
            SELECT
                d.candle_isodow,
                (d.eod_close - wo.week_open) / wo.week_open AS move_pct,
                e.low_isodow, e.high_isodow
            FROM dow_eod d
            JOIN weekly_opens wo ON d.week_myt = wo.week_myt
            JOIN wk_extreme_dow e  ON d.week_myt = e.week_myt
            WHERE e.low_isodow IS NOT NULL AND e.high_isodow IS NOT NULL
        ),
        with_bucket AS (
            SELECT *,
                CASE
                    WHEN ABS(move_pct) < $adr_small  THEN 'small'
                    WHEN ABS(move_pct) < $adr_medium THEN 'medium'
                    ELSE 'large'
                END AS move_bucket
            FROM joined
        )
        SELECT
            candle_isodow,
            move_bucket,
            SUM(CASE WHEN low_isodow  > candle_isodow THEN 1 ELSE 0 END)::DOUBLE
                / NULLIF(COUNT(*), 0) AS low_still_ahead,
            SUM(CASE WHEN high_isodow > candle_isodow THEN 1 ELSE 0 END)::DOUBLE
                / NULLIF(COUNT(*), 0) AS high_still_ahead
        FROM with_bucket
        GROUP BY candle_isodow, move_bucket
        ORDER BY candle_isodow, move_bucket
        """,
        {
            "symbol": symbol,
            "start_ms": start_ms,
            "adr_small": adr_14,
            "adr_medium": adr_medium,
        },
    ).fetchall()

    lookup: dict[tuple[int, str], tuple[float, float]] = {}
    for row in lookup_rows:
        isodow, bucket, low_pct, high_pct = row
        lookup[(int(isodow), str(bucket))] = (
            float(low_pct) if low_pct is not None else 0.0,
            float(high_pct) if high_pct is not None else 0.0,
        )

    conditioned = lookup.get((current_isodow, move_bucket))

    return WeeklyCurrentState(
        current_isodow=current_isodow,
        current_dow=current_dow,
        weekly_open=weekly_open,
        current_price=current_price,
        move_pct=move_pct,
        move_bucket=move_bucket,
        low_still_ahead_conditioned=conditioned[0] if conditioned is not None else None,
        high_still_ahead_conditioned=conditioned[1]
        if conditioned is not None
        else None,
    )

"""Weekly wick percentile — exceedance for current week's P1 wick vs historical."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import duckdb

from analytics.stats._common import MYT_OFFSET_HOURS, _start_ms


@dataclass
class WeeklyWickPercentile:
    """Live exceedance probability for current week's P1 wick vs historical P1 wicks.

    Not cached — computed fresh on every API request.
    """

    current_wick_of_adr: (
        float | None
    )  # current week's P1 wick normalised by open × ADR14
    exceedance_pct: (
        float | None
    )  # P(historical_wick > current_wick); None if P1 not set
    p1_direction: str | None  # "low" | "high"; None if P1 not set this week
    sample_count: int


def _fetch_p1_candle_data(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    start_ms: int,
) -> list[tuple[str, float, float, float, float]]:
    """Fetch (p1_dir, high, low, open, close) for the first-extreme candle of each week.

    Returns rows only for weeks where both extremes are identified and they differ.
    """
    raw = conn.execute(
        """
        WITH weekly AS (
            SELECT
                date_trunc('week', epoch_ms(open_time)::TIMESTAMP) AS week_start,
                MAX(high) AS wk_high, MIN(low) AS wk_low
            FROM ohlcv
            WHERE symbol = $symbol AND timeframe = '1h' AND open_time >= $start_ms
            GROUP BY week_start
        ),
        p1_ts AS (
            SELECT
                w.week_start,
                MIN(CASE WHEN h.high = w.wk_high THEN h.open_time END) AS high_ts,
                MIN(CASE WHEN h.low  = w.wk_low  THEN h.open_time END) AS low_ts
            FROM ohlcv h
            JOIN weekly w ON date_trunc('week', epoch_ms(h.open_time)::TIMESTAMP) = w.week_start
            WHERE h.symbol = $symbol AND h.timeframe = '1h' AND h.open_time >= $start_ms
            GROUP BY w.week_start
        ),
        p1_info AS (
            SELECT
                CASE WHEN low_ts < high_ts THEN 'low' ELSE 'high' END AS p1_dir,
                CASE WHEN low_ts < high_ts THEN low_ts ELSE high_ts END AS p1_candle_ts
            FROM p1_ts
            WHERE low_ts IS NOT NULL AND high_ts IS NOT NULL AND low_ts != high_ts
        )
        SELECT pi.p1_dir, h.high, h.low, h.open, h.close
        FROM p1_info pi
        JOIN ohlcv h ON h.open_time = pi.p1_candle_ts
        WHERE h.symbol = $symbol AND h.timeframe = '1h'
        """,
        {"symbol": symbol, "start_ms": start_ms},
    ).fetchall()
    return raw


def compute_weekly_wick_percentile(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    adr_14: float,
    days: int = 180,
) -> WeeklyWickPercentile:
    """Compute exceedance probability for current week's P1 wick vs historical P1 wicks.

    Historical: wick / open / adr_14 for each historical week where P1 is identified.
    Current: same metric for the current (possibly incomplete) week.

    Returns WeeklyWickPercentile with None fields if:
    - adr_14 == 0
    - no historical data
    - current week's P1 has not been set yet (only one extreme formed so far)
    Not intended for caching — call fresh on every API request.
    """
    if adr_14 <= 0:
        return WeeklyWickPercentile(
            current_wick_of_adr=None,
            exceedance_pct=None,
            p1_direction=None,
            sample_count=0,
        )

    start = _start_ms(days)
    candle_rows = _fetch_p1_candle_data(conn, symbol, start)
    if not candle_rows:
        return WeeklyWickPercentile(
            current_wick_of_adr=None,
            exceedance_pct=None,
            p1_direction=None,
            sample_count=0,
        )

    historical: list[float] = []
    for p1_dir, high, low, open_, close in candle_rows:
        high, low, open_, close = float(high), float(low), float(open_), float(close)
        if open_ <= 0:
            continue
        wick = min(open_, close) - low if p1_dir == "low" else high - max(open_, close)
        historical.append(wick / open_ / adr_14)

    # Current week: MYT-based Monday 00:00 boundary (same as compute_weekly_current_state)
    now_utc = datetime.now(tz=UTC)
    now_myt = now_utc + timedelta(hours=MYT_OFFSET_HOURS)
    days_since_monday = now_myt.weekday()  # 0 = Monday
    week_start_myt = (now_myt - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_start_ms = int(
        (week_start_myt - timedelta(hours=MYT_OFFSET_HOURS)).timestamp() * 1000
    )

    current_rows = conn.execute(
        """
        WITH weekly AS (
            SELECT MAX(high) AS wk_high, MIN(low) AS wk_low
            FROM ohlcv
            WHERE symbol = $symbol AND timeframe = '1h' AND open_time >= $week_start_ms
        ),
        p1_ts AS (
            SELECT
                MIN(CASE WHEN h.high = w.wk_high THEN h.open_time END) AS high_ts,
                MIN(CASE WHEN h.low  = w.wk_low  THEN h.open_time END) AS low_ts
            FROM ohlcv h CROSS JOIN weekly w
            WHERE h.symbol = $symbol AND h.timeframe = '1h' AND h.open_time >= $week_start_ms
        ),
        p1_info AS (
            SELECT
                CASE WHEN low_ts < high_ts THEN 'low' ELSE 'high' END AS p1_dir,
                CASE WHEN low_ts < high_ts THEN low_ts ELSE high_ts END AS p1_candle_ts
            FROM p1_ts
            WHERE low_ts IS NOT NULL AND high_ts IS NOT NULL AND low_ts != high_ts
        )
        SELECT pi.p1_dir, h.high, h.low, h.open, h.close
        FROM p1_info pi
        JOIN ohlcv h ON h.open_time = pi.p1_candle_ts
        WHERE h.symbol = $symbol AND h.timeframe = '1h'
        """,
        {"symbol": symbol, "week_start_ms": week_start_ms},
    ).fetchall()

    if not current_rows or not historical:
        return WeeklyWickPercentile(
            current_wick_of_adr=None,
            exceedance_pct=None,
            p1_direction=None,
            sample_count=len(historical),
        )

    p1_dir_curr, high, low, open_, close = current_rows[0]
    high, low, open_, close = float(high), float(low), float(open_), float(close)
    if open_ <= 0:
        return WeeklyWickPercentile(
            current_wick_of_adr=None,
            exceedance_pct=None,
            p1_direction=None,
            sample_count=len(historical),
        )

    if p1_dir_curr == "low":
        current_wick = min(open_, close) - low
    else:
        current_wick = high - max(open_, close)

    current_of_adr = current_wick / open_ / adr_14
    exceedance = sum(1 for h in historical if h > current_of_adr) / len(historical)

    return WeeklyWickPercentile(
        current_wick_of_adr=current_of_adr,
        exceedance_pct=exceedance,
        p1_direction=str(p1_dir_curr),
        sample_count=len(historical),
    )

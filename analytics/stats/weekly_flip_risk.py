"""Weekly flip risk conditioned on P1 direction (which extreme was set first)."""

from dataclasses import dataclass

import duckdb

from analytics.stats._common import _ISODOW_TO_SHORT, _start_ms


@dataclass
class WeeklyFlipRiskConditionedRow:
    """Single row of conditioned flip risk data."""

    p1_direction: str  # "low" | "high"
    isodow: int  # 1=Mon … 7=Sun
    dow_label: str  # "Mon" … "Sun"
    flip_pct: float  # P(P2 still ahead | p1_direction, DOW)
    sample_count: int


@dataclass
class WeeklyFlipRiskConditioned:
    """Weekly flip risk conditioned on P1 direction (which extreme was set first).

    For each (p1_direction, DOW) pair: probability that the opposite extreme (P2)
    is still ahead at that point in the week.
    - p1_direction="low": bullish weeks (low formed first), flip_pct = P(high still ahead)
    - p1_direction="high": bearish weeks (high formed first), flip_pct = P(low still ahead)
    """

    rows: list[WeeklyFlipRiskConditionedRow]


def compute_weekly_flip_risk_conditioned(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    days: int = 180,
) -> WeeklyFlipRiskConditioned:
    """Compute conditioned flip risk by P1 direction and day-of-week.

    For each historical week, identifies which extreme was set first (P1 direction).
    Returns P(P2 still ahead | p1_direction, query_dow) for every (direction, DOW) pair.

    p1_direction="low": bullish weeks (weekly low formed first).
    p1_direction="high": bearish weeks (weekly high formed first).

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
        wk_first_ts AS (
            SELECT
                w.week_start,
                MIN(CASE WHEN h.high = w.wk_high THEN h.open_time END) AS high_ts,
                MIN(CASE WHEN h.low  = w.wk_low  THEN h.open_time END) AS low_ts
            FROM ohlcv h
            JOIN weekly w ON date_trunc('week', epoch_ms(h.open_time)::TIMESTAMP) = w.week_start
            WHERE h.symbol = $symbol AND h.timeframe = '1h' AND h.open_time >= $start_ms
            GROUP BY w.week_start
        ),
        valid_weeks AS (
            SELECT
                CASE WHEN low_ts < high_ts THEN 'low' ELSE 'high' END AS p1_dir,
                ISODOW(
                    (epoch_ms(
                        CASE WHEN low_ts < high_ts THEN high_ts ELSE low_ts END
                    )::TIMESTAMP)::DATE
                ) AS p2_isodow
            FROM wk_first_ts
            WHERE low_ts IS NOT NULL AND high_ts IS NOT NULL
              AND low_ts != high_ts
        )
        SELECT
            vw.p1_dir,
            g.isodow AS query_dow,
            SUM(CASE WHEN vw.p2_isodow > g.isodow THEN 1 ELSE 0 END)::DOUBLE
                / NULLIF(COUNT(*), 0) AS flip_pct,
            COUNT(*) AS sample_count
        FROM valid_weeks vw
        CROSS JOIN generate_series(1, 7) g(isodow)
        GROUP BY vw.p1_dir, g.isodow
        ORDER BY vw.p1_dir, g.isodow
        """,
        {"symbol": symbol, "start_ms": start},
    ).fetchall()

    if not rows:
        raise ValueError(f"No OHLCV data for {symbol}")

    result_rows: list[WeeklyFlipRiskConditionedRow] = []
    for p1_dir, isodow, flip_pct, sample_count in rows:
        result_rows.append(
            WeeklyFlipRiskConditionedRow(
                p1_direction=str(p1_dir),
                isodow=int(isodow),
                dow_label=_ISODOW_TO_SHORT[int(isodow)],
                flip_pct=float(flip_pct) if flip_pct is not None else 0.0,
                sample_count=int(sample_count),
            )
        )
    return WeeklyFlipRiskConditioned(rows=result_rows)

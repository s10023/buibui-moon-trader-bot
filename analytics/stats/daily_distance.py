"""Daily distance — empirical CDF for today's daily move size vs ADR14 distribution."""

from dataclasses import dataclass

import duckdb

from analytics.stats._common import _start_ms


@dataclass
class DailyDistanceResult:
    """Empirical CDF for today's daily move size vs ADR14 historical distribution.

    Given today's current move (as × ADR14), how extreme is it historically?
    Not cached — computed fresh on every API request.
    """

    exceedance_pct: (
        float  # P(historical > current); 0.20 = "only 20% of days went further"
    )
    p80_of_adr: float  # 80th-percentile daily move in the lookback window, as × ADR14
    gap_to_p80: (
        float | None
    )  # additional × ADR needed to reach p80; None if already past p80
    sample_count: int


def compute_daily_distance(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    adr_14: float,
    days: int = 180,
) -> "DailyDistanceResult | None":
    """Compute exceedance probability for today's move vs historical daily moves.

    Queries the last `days` days, normalises each completed day's range by adr_14,
    then returns where today's partial move sits in that empirical CDF.

    Returns None if adr_14 == 0 or insufficient data.
    Not intended for caching — call fresh on every API request.
    """
    if adr_14 <= 0:
        return None

    start = _start_ms(days)
    rows = conn.execute(
        """
        WITH daily AS (
            SELECT
                (epoch_ms(open_time)::TIMESTAMP)::DATE AS trade_date,
                MAX(high) AS day_high, MIN(low) AS day_low,
                FIRST(open ORDER BY open_time) AS day_open
            FROM ohlcv
            WHERE symbol = $symbol AND timeframe = '1h'
              AND open_time >= $start_ms
            GROUP BY trade_date
            ORDER BY trade_date DESC
        )
        SELECT trade_date, day_high, day_low, day_open
        FROM daily
        """,
        {"symbol": symbol, "start_ms": start},
    ).fetchall()

    if len(rows) < 2:
        return None

    # rows[0] = today (newest, possibly partial); rows[1:] = completed historical days
    historical: list[float] = []
    for _date, day_high, day_low, day_open in rows[1:]:
        open_f = float(day_open)
        if open_f <= 0:
            continue
        range_pct = (float(day_high) - float(day_low)) / open_f
        historical.append(range_pct / adr_14)

    if not historical:
        return None

    today_open = float(rows[0][3])
    if today_open <= 0:
        return None
    today_range_pct = (float(rows[0][1]) - float(rows[0][2])) / today_open
    current_of_adr = today_range_pct / adr_14

    exceedance = sum(1 for h in historical if h > current_of_adr) / len(historical)

    sorted_hist = sorted(historical)
    n = len(sorted_hist)
    idx = (n - 1) * 0.8
    lo, hi = int(idx), min(int(idx) + 1, n - 1)
    frac = idx - lo
    p80 = sorted_hist[lo] + frac * (sorted_hist[hi] - sorted_hist[lo])

    gap_to_p80: float | None = p80 - current_of_adr if p80 > current_of_adr else None

    return DailyDistanceResult(
        exceedance_pct=exceedance,
        p80_of_adr=p80,
        gap_to_p80=gap_to_p80,
        sample_count=len(historical),
    )

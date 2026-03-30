"""Pure statistical context library — BrighterData-style market statistics.

Computes P1/P2, hourly extremes, ADR, DOW patterns, session breakdown, and
weekly P1/P2 from the ohlcv table.

Day/week boundaries: grouped by UTC date so each "day" matches exactly one
Binance daily candle (00:00 UTC – 23:59 UTC = 08:00 MYT – 07:59 MYT).
Hour display in the kill-zone chart uses MYT (+8h) to show local time-of-day.
Session labels (Asia/London/NY) are defined in MYT hours.

No database writes, no network calls. No module-level side effects.
"""

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import duckdb

MYT_OFFSET_HOURS = 8  # UTC+8

_DOW_ORDER = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]
_DOW_SHORT = {
    "Monday": "Mon",
    "Tuesday": "Tue",
    "Wednesday": "Wed",
    "Thursday": "Thu",
    "Friday": "Fri",
    "Saturday": "Sat",
    "Sunday": "Sun",
}


@dataclass
class P1P2Result:
    """P1/P2 daily analysis — was the daily low made before the daily high?"""

    overall_p1_low_pct: float  # % of days where low came before high
    by_dow: dict[str, float]  # "Mon" → 0.58, "Tue" → 0.44, ...
    sample_days: int


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


@dataclass
class ADRResult:
    """Average Daily Range statistics."""

    adr_14: float  # 14-day avg (high-low)/open
    adr_30: float  # 30-day avg (high-low)/open
    today_range_pct: float | None  # today's (high-low)/open so far
    today_consumed_pct: float | None  # today_range / adr_14


@dataclass
class DOWRow:
    """Day-of-week pattern statistics."""

    dow: str  # "Mon" … "Sun"
    avg_range_pct: float
    bull_pct: float  # % days close > open
    sample_days: int
    avg_return_pct: float = 0.0  # avg (close-open)/open — directional return


@dataclass
class DOWResult:
    """Day-of-week patterns for all 7 days."""

    rows: list[DOWRow]


@dataclass
class SessionRow:
    """Session extreme analysis."""

    session: str  # "Asia" | "London" | "NY"
    high_pct: float  # fraction of days this session made the daily high
    low_pct: float  # fraction of days this session made the daily low
    by_dow: dict[str, float]  # session_high_pct keyed by short DOW name


@dataclass
class SessionResult:
    """Session breakdown for all 3 sessions."""

    rows: list[SessionRow]


@dataclass
class WeeklyP1P2Result:
    """Weekly P1/P2 — which day of the week makes the weekly low/high first."""

    overall_p1_low_pct: float  # % of weeks where weekly low came before weekly high
    low_day: str  # DOW most often making weekly low
    high_day: str  # DOW most often making weekly high
    low_by_dow: dict[str, float]  # fraction of weeks each DOW makes weekly low
    high_by_dow: dict[str, float]  # fraction of weeks each DOW makes weekly high
    sample_weeks: int


@dataclass
class WeeklyP2Timing:
    """Weekly P2 timing: fraction of weeks where weekly extreme is still to come.

    For a given day-of-week X, low_still_ahead_by_dow[X] = fraction of weeks
    where the weekly low was made AFTER day X (i.e. it is still ahead if today is X).
    """

    low_still_ahead_by_dow: dict[str, float]  # "Mon" → 0.78
    high_still_ahead_by_dow: dict[str, float]  # "Mon" → 0.65


@dataclass
class StatsBundle:
    """Complete statistics bundle for one symbol."""

    symbol: str
    days: int
    computed_at_ms: int
    p1p2: P1P2Result
    hourly: HourlyResult
    adr: ADRResult
    dow: DOWResult
    sessions: SessionResult
    weekly_p1p2: WeeklyP1P2Result
    weekly_p2_timing: WeeklyP2Timing


def _start_ms(days: int) -> int:
    """Return Unix ms timestamp for `days` ago from now."""
    return int((datetime.now(tz=UTC) - timedelta(days=days)).timestamp() * 1000)


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
                open_time,
                high,
                low,
                (epoch_ms(open_time)::TIMESTAMP)::DATE   AS trade_date,
                dayname((epoch_ms(open_time)::TIMESTAMP)::DATE) AS dow
            FROM ohlcv
            WHERE symbol = $symbol AND timeframe = '1h'
              AND open_time >= $start_ms
        ),
        daily_extremes AS (
            SELECT trade_date, dow, MAX(high) AS day_high, MIN(low) AS day_low
            FROM hourly GROUP BY trade_date, dow
        ),
        first_hit AS (
            SELECT
                h.trade_date,
                de.dow,
                MIN(CASE WHEN h.high = de.day_high THEN h.open_time END) AS high_ts,
                MIN(CASE WHEN h.low  = de.day_low  THEN h.open_time END) AS low_ts
            FROM hourly h JOIN daily_extremes de ON h.trade_date = de.trade_date
            GROUP BY h.trade_date, de.dow
        )
        SELECT
            dow,
            SUM(CASE WHEN low_ts < high_ts THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS p1_low_pct,
            COUNT(*) AS n
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
    total_n = 0

    for dow_full, p1_pct, n in rows:
        short = _DOW_SHORT.get(str(dow_full), str(dow_full)[:3])
        by_dow[short] = float(p1_pct)
        total_p1_sum += float(p1_pct) * int(n)
        total_n += int(n)

    overall = total_p1_sum / total_n if total_n > 0 else 0.0
    return P1P2Result(
        overall_p1_low_pct=overall,
        by_dow=by_dow,
        sample_days=total_n,
    )


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

    if not rows:
        raise ValueError(f"No OHLCV data for {symbol}")

    # rows are ordered newest first
    ranges = [
        (float(day_high) - float(day_low)) / float(day_open)
        for (_, day_high, day_low, day_open) in rows
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
    if rows:
        newest_day_high = float(rows[0][1])
        newest_day_low = float(rows[0][2])
        newest_day_open = float(rows[0][3])
        if newest_day_open > 0:
            today_range_pct = (newest_day_high - newest_day_low) / newest_day_open
            if adr_14 > 0:
                today_consumed_pct = today_range_pct / adr_14

    return ADRResult(
        adr_14=adr_14,
        adr_30=adr_30,
        today_range_pct=today_range_pct,
        today_consumed_pct=today_consumed_pct,
    )


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
            AVG((day_close - day_open) / day_open) AS avg_return_pct
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
    for dow_full, avg_range, bull_pct, n, avg_return in rows:
        short = _DOW_SHORT.get(str(dow_full), str(dow_full)[:3])
        dow_map[short] = DOWRow(
            dow=short,
            avg_range_pct=float(avg_range),
            bull_pct=float(bull_pct),
            sample_days=int(n),
            avg_return_pct=float(avg_return),
        )

    # Return in Mon–Sun order
    ordered_rows = [
        dow_map[s]
        for s in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        if s in dow_map
    ]
    return DOWResult(rows=ordered_rows)


def compute_session_breakdown(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    days: int = 180,
) -> SessionResult:
    """Compute session breakdown: which session (Asia/London/NY) most often makes daily H/L.

    Sessions are in MYT hours (UTC+8):
    - Asia:   00-07
    - London: 14-21
    - NY:     >= 20 OR <= 3  (crosses midnight; overlaps with London 20-21)

    Raises ValueError if no OHLCV data exists for the symbol.
    """
    start = _start_ms(days)

    # Session high/low pct overall
    rows = conn.execute(
        """
        WITH hourly AS (
            SELECT open_time, high, low,
                (epoch_ms(open_time)::TIMESTAMP)::DATE                   AS trade_date,
                dayname((epoch_ms(open_time)::TIMESTAMP)::DATE)          AS dow,
                CASE
                    WHEN HOUR((epoch_ms(open_time) + INTERVAL 8 HOUR)::TIMESTAMP) BETWEEN 0  AND 7  THEN 'Asia'
                    WHEN HOUR((epoch_ms(open_time) + INTERVAL 8 HOUR)::TIMESTAMP) BETWEEN 14 AND 21 THEN 'London'
                    WHEN HOUR((epoch_ms(open_time) + INTERVAL 8 HOUR)::TIMESTAMP) >= 20
                      OR HOUR((epoch_ms(open_time) + INTERVAL 8 HOUR)::TIMESTAMP) <= 3             THEN 'NY'
                    ELSE 'Off'
                END AS session
            FROM ohlcv WHERE symbol = $symbol AND timeframe = '1h' AND open_time >= $start_ms
        ),
        daily_ext AS (
            SELECT trade_date, MAX(high) AS day_high, MIN(low) AS day_low
            FROM hourly GROUP BY trade_date
        ),
        session_ext AS (
            SELECT h.trade_date, h.session, h.dow,
                   MAX(h.high) = de.day_high AS made_high,
                   MIN(h.low)  = de.day_low  AS made_low
            FROM hourly h JOIN daily_ext de ON h.trade_date = de.trade_date
            WHERE h.session != 'Off'
            GROUP BY h.trade_date, h.session, h.dow, de.day_high, de.day_low
        ),
        totals AS (SELECT COUNT(DISTINCT trade_date) AS n FROM daily_ext)
        SELECT
            session,
            SUM(made_high::INT)::DOUBLE / t.n AS high_pct,
            SUM(made_low::INT)::DOUBLE  / t.n AS low_pct
        FROM session_ext CROSS JOIN totals t
        GROUP BY session, t.n
        """,
        {"symbol": symbol, "start_ms": start},
    ).fetchall()

    if not rows:
        raise ValueError(f"No OHLCV data for {symbol}")

    # Session high_pct by DOW
    dow_rows = conn.execute(
        """
        WITH hourly AS (
            SELECT open_time, high, low,
                (epoch_ms(open_time)::TIMESTAMP)::DATE                   AS trade_date,
                dayname((epoch_ms(open_time)::TIMESTAMP)::DATE)          AS dow,
                CASE
                    WHEN HOUR((epoch_ms(open_time) + INTERVAL 8 HOUR)::TIMESTAMP) BETWEEN 0  AND 7  THEN 'Asia'
                    WHEN HOUR((epoch_ms(open_time) + INTERVAL 8 HOUR)::TIMESTAMP) BETWEEN 14 AND 21 THEN 'London'
                    WHEN HOUR((epoch_ms(open_time) + INTERVAL 8 HOUR)::TIMESTAMP) >= 20
                      OR HOUR((epoch_ms(open_time) + INTERVAL 8 HOUR)::TIMESTAMP) <= 3             THEN 'NY'
                    ELSE 'Off'
                END AS session
            FROM ohlcv WHERE symbol = $symbol AND timeframe = '1h' AND open_time >= $start_ms
        ),
        daily_ext AS (
            SELECT trade_date, MAX(high) AS day_high, MIN(low) AS day_low
            FROM hourly GROUP BY trade_date
        ),
        session_ext AS (
            SELECT h.trade_date, h.session, h.dow,
                   MAX(h.high) = de.day_high AS made_high
            FROM hourly h JOIN daily_ext de ON h.trade_date = de.trade_date
            WHERE h.session != 'Off'
            GROUP BY h.trade_date, h.session, h.dow, de.day_high
        ),
        totals_by_dow AS (
            SELECT dow, COUNT(DISTINCT trade_date) AS n
            FROM (SELECT DISTINCT trade_date, dow FROM hourly WHERE session != 'Off')
            GROUP BY dow
        )
        SELECT
            se.session,
            se.dow,
            SUM(se.made_high::INT)::DOUBLE / td.n AS high_pct_dow
        FROM session_ext se
        JOIN totals_by_dow td ON se.dow = td.dow
        GROUP BY se.session, se.dow, td.n
        """,
        {"symbol": symbol, "start_ms": start},
    ).fetchall()

    # Build by_dow per session
    session_dow_map: dict[str, dict[str, float]] = {"Asia": {}, "London": {}, "NY": {}}
    for session, dow_full, high_pct_dow in dow_rows:
        s = str(session)
        if s not in session_dow_map:
            continue
        short = _DOW_SHORT.get(str(dow_full), str(dow_full)[:3])
        session_dow_map[s][short] = float(high_pct_dow)

    session_order = ["Asia", "London", "NY"]
    session_map: dict[str, SessionRow] = {}
    for session, high_pct, low_pct in rows:
        s = str(session)
        session_map[s] = SessionRow(
            session=s,
            high_pct=float(high_pct),
            low_pct=float(low_pct),
            by_dow=session_dow_map.get(s, {}),
        )

    ordered = [session_map[s] for s in session_order if s in session_map]
    return SessionResult(rows=ordered)


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


_ISODOW_TO_SHORT = ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


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

    return WeeklyP2Timing(
        low_still_ahead_by_dow=low_still_ahead,
        high_still_ahead_by_dow=high_still_ahead,
    )


def compute_all(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    days: int = 180,
) -> StatsBundle:
    """Compute all statistics and return a StatsBundle.

    Raises ValueError if no OHLCV data exists for the symbol.
    """
    p1p2 = compute_p1p2_daily(conn, symbol, days)
    hourly = compute_hourly_extremes(conn, symbol, days)
    adr = compute_adr(conn, symbol)
    dow = compute_dow_patterns(conn, symbol, days)
    sessions = compute_session_breakdown(conn, symbol, days)
    weekly_p1p2 = compute_weekly_p1p2(conn, symbol, days)
    weekly_p2_timing = compute_weekly_p2_timing(conn, symbol, days)

    return StatsBundle(
        symbol=symbol,
        days=days,
        computed_at_ms=int(time.time() * 1000),
        p1p2=p1p2,
        hourly=hourly,
        adr=adr,
        dow=dow,
        sessions=sessions,
        weekly_p1p2=weekly_p1p2,
        weekly_p2_timing=weekly_p2_timing,
    )

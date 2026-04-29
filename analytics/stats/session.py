"""Session breakdown — Asia/London/NY extreme analysis (MYT hours)."""

from dataclasses import dataclass

import duckdb

from analytics.stats._common import _DOW_SHORT, _start_ms


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


def compute_session_breakdown(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    days: int = 180,
) -> SessionResult:
    """Compute session breakdown: which session (Asia/London/NY) most often makes daily H/L.

    Sessions are in MYT hours (UTC+8):
    - Asia:   08-13  (Tokyo open 08:00 MYT, before London 14:00)
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
                    WHEN HOUR((epoch_ms(open_time) + INTERVAL 8 HOUR)::TIMESTAMP) BETWEEN 8  AND 13 THEN 'Asia'
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
                    WHEN HOUR((epoch_ms(open_time) + INTERVAL 8 HOUR)::TIMESTAMP) BETWEEN 8  AND 13 THEN 'Asia'
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

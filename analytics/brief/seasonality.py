"""Day-ahead seasonality strip distilled from analytics/stats computes.

All four sources window to a **completed-1h-bar** end
(``as_of_ms - TF_MS["1h"]``, not ``as_of_ms`` directly) so the strip never
admits the still-forming 1h candle when ``as_of`` lands mid-hour — the four
stats computes filter ``open_time <= end_ms`` internally, so passing
``as_of_ms`` verbatim would leak an incomplete bar into weekly-high/low
attribution. This mirrors the ``completed_bars`` invariant used elsewhere in
``analytics/brief/``. Each source is independent: a ValueError (no data)
drops that line, and only when ALL sources fail does the strip become None.
"""

from __future__ import annotations

import duckdb

from analytics.brief._common import TF_MS, day_ahead_dow
from analytics.brief.types import SeasonalityStrip
from analytics.stats.dow import compute_dow_patterns
from analytics.stats.session import compute_session_breakdown
from analytics.stats.weekly_p1p2 import compute_weekly_p1p2
from analytics.stats.weekly_p2_timing import compute_weekly_p2_timing

_DOW_ORDER = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

_ROUND_NDIGITS = 9  # kills float-non-associativity noise from parallel SQL
# AVG()/SUM() aggregation (observed as a 1-ULP diff across repeat calls on
# identical input); these are 0-1 fractions for display, so 9dp keeps far
# more precision than meaningful while making the strip byte-reproducible.


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, _ROUND_NDIGITS)


def _dominant_dow(by_dow: dict[str, float]) -> str | None:
    """Deterministic argmax over a DOW->fraction dict; ties break by Mon-Sun order.

    Bypasses ``WeeklyP1P2Result.high_day``/``low_day`` (backed by DuckDB
    ``MODE()``, which picks arbitrarily among tied DOWs and is observably
    non-deterministic across repeat calls on identical input) in favor of a
    stable reduction over the already-deterministic ``high_by_dow`` /
    ``low_by_dow`` aggregates.
    """
    if not by_dow:
        return None
    return max(by_dow, key=lambda d: (by_dow[d], -_DOW_ORDER.index(d)))


def build_strip(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    as_of_ms: int,
    stats_days: int,
) -> SeasonalityStrip | None:
    dow = day_ahead_dow(as_of_ms)
    stats_end_ms = as_of_ms - TF_MS["1h"]

    bull_pct: float | None = None
    avg_range_pct: float | None = None
    sample_days: int | None = None
    try:
        dow_res = compute_dow_patterns(conn, symbol, stats_days, end_ms=stats_end_ms)
        row = next((r for r in dow_res.rows if r.dow == dow), None)
        if row is not None:
            bull_pct = _round(row.bull_pct)
            avg_range_pct = _round(row.avg_range_pct)
            sample_days = row.sample_days
    except ValueError:
        pass

    high_session: str | None = None
    high_session_pct: float | None = None
    low_session: str | None = None
    low_session_pct: float | None = None
    try:
        ses = compute_session_breakdown(conn, symbol, stats_days, end_ms=stats_end_ms)
        if ses.rows:
            hi = max(ses.rows, key=lambda r: r.high_pct)
            lo = max(ses.rows, key=lambda r: r.low_pct)
            high_session, high_session_pct = hi.session, _round(hi.high_pct)
            low_session, low_session_pct = lo.session, _round(lo.low_pct)
    except ValueError:
        pass

    weekly_low_still_ahead: float | None = None
    weekly_high_still_ahead: float | None = None
    try:
        timing = compute_weekly_p2_timing(conn, symbol, stats_days, end_ms=stats_end_ms)
        weekly_low_still_ahead = _round(timing.low_still_ahead_by_dow.get(dow))
        weekly_high_still_ahead = _round(timing.high_still_ahead_by_dow.get(dow))
    except ValueError:
        pass

    typical_low_day: str | None = None
    typical_high_day: str | None = None
    try:
        p1p2 = compute_weekly_p1p2(conn, symbol, stats_days, end_ms=stats_end_ms)
        typical_low_day = _dominant_dow(p1p2.low_by_dow)
        typical_high_day = _dominant_dow(p1p2.high_by_dow)
    except ValueError:
        pass

    # weekly_p2_timing is deliberately excluded from this guard: its SQL
    # cross-joins generate_series(1, 7) so it never raises ValueError and
    # always yields a (possibly all-zero) value for every DOW, even with zero
    # underlying data — it can't signal "no data" the way the other three do.
    if bull_pct is None and high_session is None and typical_low_day is None:
        return None
    return SeasonalityStrip(
        dow=dow,
        bull_pct=bull_pct,
        avg_range_pct=avg_range_pct,
        sample_days=sample_days,
        high_session=high_session,
        high_session_pct=high_session_pct,
        low_session=low_session,
        low_session_pct=low_session_pct,
        weekly_low_still_ahead=weekly_low_still_ahead,
        weekly_high_still_ahead=weekly_high_still_ahead,
        typical_low_day=typical_low_day,
        typical_high_day=typical_high_day,
    )

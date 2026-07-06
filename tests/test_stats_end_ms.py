"""Additive end_ms windowing on the four brief-consumed stats computes."""

from analytics.stats.dow import compute_dow_patterns
from analytics.stats.session import compute_session_breakdown
from analytics.stats.weekly_p1p2 import compute_weekly_p1p2
from analytics.stats.weekly_p2_timing import compute_weekly_p2_timing
from tests._brief_fixtures import DAY_MS, START_MS, make_conn, seed_symbol

SYM = "BTCUSDT"


def test_dow_end_ms_cuts_window() -> None:
    conn = make_conn()
    seed_symbol(conn, SYM, START_MS, 60)
    full = compute_dow_patterns(conn, SYM, 3650, end_ms=START_MS + 60 * DAY_MS)
    half = compute_dow_patterns(conn, SYM, 3650, end_ms=START_MS + 30 * DAY_MS)
    assert sum(r.sample_days for r in half.rows) < sum(r.sample_days for r in full.rows)


def test_session_end_ms_accepted() -> None:
    conn = make_conn()
    seed_symbol(conn, SYM, START_MS, 60)
    res = compute_session_breakdown(conn, SYM, 3650, end_ms=START_MS + 60 * DAY_MS)
    assert len(res.rows) == 3


def test_weekly_p1p2_end_ms_cuts_window() -> None:
    conn = make_conn()
    seed_symbol(conn, SYM, START_MS, 60)
    full = compute_weekly_p1p2(conn, SYM, 3650, end_ms=START_MS + 60 * DAY_MS)
    half = compute_weekly_p1p2(conn, SYM, 3650, end_ms=START_MS + 30 * DAY_MS)
    assert half.sample_weeks < full.sample_weeks


def test_weekly_p2_timing_end_ms_accepted() -> None:
    conn = make_conn()
    seed_symbol(conn, SYM, START_MS, 60)
    res = compute_weekly_p2_timing(conn, SYM, 3650, end_ms=START_MS + 60 * DAY_MS)
    assert "Mon" in res.low_still_ahead_by_dow


def test_default_end_ms_still_works() -> None:
    """end_ms=None (default) keeps the current now-anchored behaviour."""
    conn = make_conn()
    seed_symbol(conn, SYM, START_MS, 60)
    # Seeded data is in 2024 — a now-anchored 180d window sees none of it,
    # so the compute raises (dow) or returns empty rows; either is 'unchanged'.
    try:
        res = compute_dow_patterns(conn, SYM, 180)
        assert sum(r.sample_days for r in res.rows) == 0
    except ValueError:
        pass

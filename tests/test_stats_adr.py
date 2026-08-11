"""ADR mean/median — compute_adr had no unit test before 2026-08-11.

The fixture is deliberately SKEWED. A uniform range distribution makes mean and
median identical, so a test built on one would pass against an implementation
that computed the mean twice — the vacuous-fixture trap this repo has hit three
times in a single session (abs() on uniform signs, a rename on an identity map,
an aggregation on self-built aggregates). Here 13 quiet days sit beside one 50%
outlier, so mean and median differ by ~4.5x and only a real median passes.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import duckdb
import pandas as pd
import pytest

from analytics.data_store import init_schema
from analytics.stats.adr import compute_adr
from analytics.store.market_data import upsert_ohlcv

SYM = "BTCUSDT"
QUIET = 0.01  # 1% day
LOUD = 0.50  # one 50% day, placed inside the 14d window
OUTLIER_AT = 5  # days back from today
N_DAYS = 20


def _seed(conn: duckdb.DuckDBPyConnection) -> None:
    """One 1h bar per day at 12:00 UTC, newest = today.

    12:00 UTC is chosen so the bar lands on the same calendar DATE under both UTC
    and the Asia/Kuala_Lumpur (+8) session timezone DuckDB uses here — 12+8=20 is
    still the same day. A midnight bar would bucket into different dates under the
    two zones and silently split the fixture.
    """
    today = datetime.now(tz=UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    rows: list[dict[str, object]] = []
    for back in range(N_DAYS):
        rng = LOUD if back == OUTLIER_AT else QUIET
        ts = int((today - timedelta(days=back)).timestamp() * 1000)
        rows.append(
            {
                "symbol": SYM,
                "timeframe": "1h",
                "open_time": ts,
                "open": 100.0,
                "high": 100.0 * (1.0 + rng),
                "low": 100.0,
                "close": 100.0,
                "volume": 1000.0,
                "taker_buy_volume": 500.0,
            }
        )
    upsert_ohlcv(conn, pd.DataFrame(rows))


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn)
    return conn


def test_adr_14_mean_is_dragged_by_the_outlier() -> None:
    res = compute_adr(_conn(), SYM)
    assert res.adr_14 == pytest.approx((13 * QUIET + LOUD) / 14)


def test_adr_14_median_ignores_the_outlier() -> None:
    res = compute_adr(_conn(), SYM)
    assert res.adr_14_median == pytest.approx(QUIET)


def test_adr_30_mean_and_median_both_present() -> None:
    res = compute_adr(_conn(), SYM)
    assert res.adr_30 == pytest.approx((19 * QUIET + LOUD) / N_DAYS)
    assert res.adr_30_median == pytest.approx(QUIET)


def test_mean_and_median_are_actually_different() -> None:
    """The non-degeneracy check: a mean computed twice would pass everything above."""
    res = compute_adr(_conn(), SYM)
    assert res.adr_14 > res.adr_14_median * 4
    assert res.adr_30 > res.adr_30_median * 3


def test_today_consumed_still_divides_by_the_MEAN() -> None:
    """Display-only guard.

    adr_14 feeds stop sizing in weekly_state, weekly_wick, stats_context and the
    alert formatter. Adding a median column must not move any of that. Today is a
    QUIET day, so consumed-vs-mean is ~0.22 while consumed-vs-median would be 1.0
    — this assertion fails loudly the day someone repoints it.
    """
    res = compute_adr(_conn(), SYM)
    assert res.today_range_pct == pytest.approx(QUIET)
    assert res.today_consumed_pct == pytest.approx(QUIET / ((13 * QUIET + LOUD) / 14))
    assert res.today_consumed_pct is not None
    assert res.today_consumed_pct < 0.5  # would be exactly 1.0 against the median


def test_median_survives_a_short_history() -> None:
    """Fewer bars than the window: both stats must still return, not divide by 14."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    today = datetime.now(tz=UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    ts = int(today.timestamp() * 1000)
    upsert_ohlcv(
        conn,
        pd.DataFrame(
            [
                {
                    "symbol": SYM,
                    "timeframe": "1h",
                    "open_time": ts,
                    "open": 100.0,
                    "high": 102.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 1000.0,
                    "taker_buy_volume": 500.0,
                }
            ]
        ),
    )
    res = compute_adr(conn, SYM)
    assert res.adr_14 == pytest.approx(0.02)
    assert res.adr_14_median == pytest.approx(0.02)


def test_no_data_still_raises() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    with pytest.raises(ValueError, match="No OHLCV data"):
        compute_adr(conn, "NOSUCHUSDT")


def test_seed_is_inside_the_35d_window() -> None:
    """Guards the fixture itself: compute_adr is now-anchored via _start_ms(35)."""
    assert N_DAYS < 35
    assert time.time() > 0

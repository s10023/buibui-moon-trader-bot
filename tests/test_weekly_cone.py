"""Tests for analytics/stats/weekly_cone.py using in-memory DuckDB.

Synthetic-week construction mirrors tests/test_path_cone.py: every bar opens
at 100 and closes at 100 + k. Bar 1 carries the week low (99); bar 2 carries
the week high (101). With k in [-0.5, +0.5] every other bar stays inside
[99.5, 100.5], so every complete week's range is exactly (101 - 99) / 100 =
0.02 and the trailing AWR14 of any week with 14 complete predecessors is
exactly 0.02. A week's normalized close path is then constant at k / 2:
((100 + k - 100) / 100) / 0.02 = k / 2.
"""

from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from analytics.data_store import init_schema
from analytics.stats import weekly_cone
from analytics.stats.weekly_cone import (
    WEEK_BARS,
    WeeklyConeBundle,
    WeekRecord,
    compute_current_week_path,
    compute_weekly_cone,
    week_records,
)

_SYMBOL = "WCONEUSDT"
# Fixed clock: Wednesday 2026-03-04 12:30 UTC (hour 60 of that week).
_NOW = datetime(2026, 3, 4, 12, 30, tzinfo=UTC)
_NOW_MS = int(_NOW.timestamp() * 1000)
_CURRENT_WEEK = date(2026, 3, 2)  # the Monday of _NOW


def _insert_ohlcv_rows(
    conn: duckdb.DuckDBPyConnection, n_rows: int, params: list[object]
) -> None:
    """Insert n_rows OHLCV rows in ONE statement.

    DuckDB pays a fixed per-statement cost that dwarfs the row itself, and its
    executemany just loops, so a bar-at-a-time seed is ~44x slower than folding
    the same rows into a single multi-row VALUES clause. These fixtures seed
    thousands of hourly bars, which made this file alone a quarter of the
    suite's runtime.
    """
    values = ",".join(["(?,?,?,?,?,?,?,?,?)"] * n_rows)
    conn.execute(
        "INSERT OR REPLACE INTO ohlcv "
        "(symbol, timeframe, open_time, open, high, low, close, volume, "
        f"taker_buy_volume) VALUES {values}",
        params,
    )


def _insert_week(
    conn: duckdb.DuckDBPyConnection,
    monday: date,
    *,
    k: float = 0.0,
    n_bars: int = WEEK_BARS,
) -> None:
    """Insert one synthetic Monday-anchored week of 1h bars."""
    base_ms = int(
        datetime(monday.year, monday.month, monday.day, tzinfo=UTC).timestamp() * 1000
    )
    close = 100.0 + k
    params: list[object] = []
    for h in range(n_bars):
        if h == 0:
            high, low = 100.5, 99.0
        elif h == 1:
            high, low = 101.0, 99.5
        else:
            high, low = max(100.5, close), min(99.5, close)
        params.extend(
            (
                _SYMBOL,
                "1h",
                base_ms + h * 3_600_000,
                100.0,
                high,
                low,
                close,
                100.0,
                50.0,
            )
        )
    _insert_ohlcv_rows(conn, n_bars, params)


def _seed_warmup(
    conn: duckdb.DuckDBPyConnection, start_monday: date, n: int = 14
) -> None:
    """n consecutive complete doji (k=0) weeks starting at start_monday."""
    for i in range(n):
        _insert_week(conn, start_monday + timedelta(weeks=i))


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


def test_warmup_weeks_produce_no_records(conn: duckdb.DuckDBPyConnection) -> None:
    """The first 14 qualifying weeks have no AWR window, so no records."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=14), n=14)
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert bundle.total_weeks == 0
    assert bundle.combos["all"].n == 0
    assert bundle.combos["all"].bands == []


def test_fifteenth_week_enters_population(conn: duckdb.DuckDBPyConnection) -> None:
    """One week past warmup yields exactly one record, direction bull."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=15), n=14)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=1), k=0.4)
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert bundle.total_weeks == 1
    assert bundle.combos["bull"].n == 1
    assert bundle.combos["bear"].n == 0
    assert bundle.combos["all"].n == 1


def test_normalization_identity(conn: duckdb.DuckDBPyConnection) -> None:
    """Normalized path at bar 168 equals week return / AWR14 (== k / 2)."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=15), n=14)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=1), k=0.4)
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    bands = bundle.combos["bull"].bands
    assert len(bands) == WEEK_BARS
    # Single member -> every percentile equals that member's value.
    assert bands[-1] == pytest.approx([0.2] * 5)


def test_short_week_excluded(conn: duckdb.DuckDBPyConnection) -> None:
    """A 167-bar week never enters the population."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=15), n=14)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=1), k=0.4, n_bars=167)
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert bundle.total_weeks == 0


def test_week_boundary_across_year_end(conn: duckdb.DuckDBPyConnection) -> None:
    """Monday anchoring holds across a year boundary (2025-12-29 is a Monday)."""
    _seed_warmup(conn, date(2025, 12, 29) - timedelta(weeks=14), n=14)
    _insert_week(conn, date(2025, 12, 29), k=-0.4)
    now = int(datetime(2026, 1, 7, 12, 0, tzinfo=UTC).timestamp() * 1000)
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=now)
    assert bundle.total_weeks == 1
    assert bundle.combos["bear"].n == 1


def test_thin_data_returns_empty_never_raises(conn: duckdb.DuckDBPyConnection) -> None:
    """No bars at all -> empty combos, no exception."""
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert isinstance(bundle, WeeklyConeBundle)
    assert bundle.total_weeks == 0
    assert set(bundle.combos) == {"all", "bull", "bear"}


def test_determinism(conn: duckdb.DuckDBPyConnection) -> None:
    """Same inputs -> identical bundle."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=16), n=15)
    a = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    b = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert a == b


def test_current_week_path_partial(conn: duckdb.DuckDBPyConnection) -> None:
    """Partial current week: elapsed_h counts only closed bars."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=14), n=14)
    _insert_week(conn, _CURRENT_WEEK, k=0.2, n_bars=61)
    path = compute_current_week_path(conn, _SYMBOL, now_ms=_NOW_MS)
    assert path is not None
    assert path.week_open == 100.0
    assert path.awr14_current == pytest.approx(0.02)
    # _NOW is 12:30 Wed = hour 60 of the week forming; 60 bars have closed.
    assert path.elapsed_h == 60
    assert len(path.points) == 61


def test_current_week_path_none_without_awr(conn: duckdb.DuckDBPyConnection) -> None:
    """Fewer than 14 complete prior weeks -> None."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=3), n=3)
    _insert_week(conn, _CURRENT_WEEK, k=0.2, n_bars=61)
    assert compute_current_week_path(conn, _SYMBOL, now_ms=_NOW_MS) is None


def test_low_high_timing_curves(conn: duckdb.DuckDBPyConnection) -> None:
    """Low is set at bar 1 and high at bar 2 by construction."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=15), n=14)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=1), k=0.4)
    combo = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS).combos["all"]
    assert combo.low_in_by[0] == pytest.approx(1.0)
    assert combo.high_in_by[0] == pytest.approx(0.0)
    assert combo.high_in_by[1] == pytest.approx(1.0)
    assert len(combo.low_in_by) == WEEK_BARS


def test_week_records_matches_cone_population(conn: duckdb.DuckDBPyConnection) -> None:
    """The public wrapper returns exactly the population the cone counts."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=17), n=14)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=3), k=0.4)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=2), k=-0.4)

    records = week_records(conn, _SYMBOL, now_ms=_NOW_MS)
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)

    assert len(records) == bundle.total_weeks == 2
    assert all(len(r.norm_path) == WEEK_BARS for r in records)
    assert {r.direction for r in records} == {"bull", "bear"}


def test_week_records_are_chronological(conn: duckdb.DuckDBPyConnection) -> None:
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=17), n=14)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=3), k=0.4)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=2), k=-0.4)

    records = week_records(conn, _SYMBOL, now_ms=_NOW_MS)
    assert [r.week for r in records] == sorted(r.week for r in records)


def test_week_records_thin_data_returns_empty(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Matches compute_weekly_cone: never raises on a short history."""
    assert week_records(conn, _SYMBOL, now_ms=_NOW_MS) == []


def test_private_alias_still_resolves() -> None:
    """The rename must not break internal references inside the module."""
    assert weekly_cone._WeekRecord is WeekRecord

"""Tests for analytics/stats_lib.py using in-memory DuckDB."""

from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from analytics.data_store import init_schema
from analytics.stats_lib import (
    StatsBundle,
    compute_adr,
    compute_all,
    compute_dow_patterns,
    compute_hourly_extremes,
    compute_p1p2_daily,
    compute_session_breakdown,
    compute_weekly_p1p2,
)

_SYMBOL = "TESTUSDT"
_TIMEFRAME = "1h"

# MYT is UTC+8.  We want:
# - daily high at hour 14 MYT = 06:00 UTC
# - daily low  at hour  2 MYT = 18:00 UTC (previous day)
# Low always comes before high in MYT (hour 2 < hour 14).
#
# Use a base date 20 days ago so the data falls within the 30-day window
# used by the _start_ms() calls in the lib functions.
_DAYS = 14
_OPEN_PRICE = 40000.0
_NORMAL_HIGH = 40500.0  # normal candle high
_NORMAL_LOW = 39500.0  # normal candle low
_PEAK_HIGH = 41000.0  # only candle at hour 14 MYT gets this high
_PEAK_LOW = 39000.0  # only candle at hour 2  MYT gets this low


def _myt_hour(utc_ts: int) -> int:
    """Return MYT hour for a UTC unix ms timestamp."""
    return (datetime.fromtimestamp(utc_ts / 1000, tz=UTC).hour + 8) % 24


def _make_candles() -> list[dict]:
    """Generate 14 days × 24 hourly candles with deterministic highs/lows.

    Base date is 20 days ago so candles fall within the 30-day lookback window.
    """
    base_utc = datetime.now(tz=UTC) - timedelta(days=20)
    # Align to midnight UTC
    base_utc = base_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = []
    for day in range(_DAYS):
        for hour in range(24):
            utc_dt = base_utc + timedelta(days=day, hours=hour)
            open_time_ms = int(utc_dt.timestamp() * 1000)
            myt_h = _myt_hour(open_time_ms)
            if myt_h == 14:
                high = _PEAK_HIGH
                low = _NORMAL_LOW
            elif myt_h == 2:
                high = _NORMAL_HIGH
                low = _PEAK_LOW
            else:
                high = _NORMAL_HIGH
                low = _NORMAL_LOW
            rows.append(
                {
                    "symbol": _SYMBOL,
                    "timeframe": _TIMEFRAME,
                    "open_time": open_time_ms,
                    "open": _OPEN_PRICE,
                    "high": high,
                    "low": low,
                    "close": _OPEN_PRICE,
                    "volume": 100.0,
                    "taker_buy_volume": 50.0,
                }
            )
    return rows


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with schema + synthetic OHLCV for TESTUSDT."""
    c = duckdb.connect(":memory:")
    init_schema(c)
    rows = _make_candles()
    for row in rows:
        c.execute(
            "INSERT OR REPLACE INTO ohlcv "
            "(symbol, timeframe, open_time, open, high, low, close, volume, taker_buy_volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                row["symbol"],
                row["timeframe"],
                row["open_time"],
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row["volume"],
                row["taker_buy_volume"],
            ],
        )
    return c


def test_compute_p1p2_daily_low_first(conn: duckdb.DuckDBPyConnection) -> None:
    """P1=Low should be ~1.0 since low (hour 2) always comes before high (hour 14) in MYT."""
    result = compute_p1p2_daily(conn, _SYMBOL, days=30)
    assert result.sample_days > 0
    # Low at hour 2 always precedes high at hour 14 → p1_low should be very high
    assert result.overall_p1_low_pct > 0.8, (
        f"Expected p1_low_pct > 0.8, got {result.overall_p1_low_pct}"
    )
    # by_dow should have entries for the DOWs present in the data
    assert len(result.by_dow) > 0


def test_compute_p1p2_daily_no_data(conn: duckdb.DuckDBPyConnection) -> None:
    """Should raise ValueError for unknown symbol."""
    with pytest.raises(ValueError, match="No OHLCV data"):
        compute_p1p2_daily(conn, "FAKESYMBOL", days=30)


def test_compute_hourly_extremes_peaks(conn: duckdb.DuckDBPyConnection) -> None:
    """Peak high should be at hour 14 MYT; peak low should be at hour 2 MYT."""
    result = compute_hourly_extremes(conn, _SYMBOL, days=30)
    assert len(result.rows) == 24, f"Expected 24 hourly rows, got {len(result.rows)}"
    assert result.peak_high_hour == 14, (
        f"Expected peak_high_hour=14, got {result.peak_high_hour}"
    )
    assert result.peak_low_hour == 2, (
        f"Expected peak_low_hour=2, got {result.peak_low_hour}"
    )


def test_compute_adr_positive(conn: duckdb.DuckDBPyConnection) -> None:
    """ADR(14) and ADR(30) should be positive for valid data."""
    result = compute_adr(conn, _SYMBOL)
    assert result.adr_14 > 0, f"adr_14={result.adr_14} should be > 0"
    assert result.adr_30 > 0, f"adr_30={result.adr_30} should be > 0"


def test_compute_dow_patterns_has_rows(conn: duckdb.DuckDBPyConnection) -> None:
    """DOW patterns should return rows for days present in the data."""
    result = compute_dow_patterns(conn, _SYMBOL, days=30)
    assert len(result.rows) > 0
    for row in result.rows:
        assert 0.0 <= row.bull_pct <= 1.0
        assert row.avg_range_pct > 0
        assert row.sample_days > 0


def test_compute_session_breakdown_has_sessions(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Session breakdown should include at least one session."""
    result = compute_session_breakdown(conn, _SYMBOL, days=30)
    assert len(result.rows) > 0
    sessions = {r.session for r in result.rows}
    # All three sessions should appear given 24h coverage
    assert "Asia" in sessions
    assert "London" in sessions
    assert "NY" in sessions
    for row in result.rows:
        assert 0.0 <= row.high_pct
        assert 0.0 <= row.low_pct


def test_compute_weekly_p1p2_positive(conn: duckdb.DuckDBPyConnection) -> None:
    """Weekly P1/P2 should return valid data with sample_weeks > 0."""
    result = compute_weekly_p1p2(conn, _SYMBOL, days=30)
    assert result.sample_weeks > 0
    assert 0.0 <= result.overall_p1_low_pct <= 1.0
    assert result.high_day != ""
    assert result.low_day != ""


def test_compute_all_returns_bundle(conn: duckdb.DuckDBPyConnection) -> None:
    """compute_all should return a complete StatsBundle with no exceptions."""
    bundle = compute_all(conn, _SYMBOL, days=30)
    assert isinstance(bundle, StatsBundle)
    assert bundle.symbol == _SYMBOL
    assert bundle.days == 30
    assert bundle.computed_at_ms > 0
    # All sub-results populated
    assert bundle.p1p2.sample_days > 0
    assert len(bundle.hourly.rows) == 24
    assert bundle.adr.adr_14 > 0
    assert len(bundle.dow.rows) > 0
    assert len(bundle.sessions.rows) > 0
    assert bundle.weekly_p1p2.sample_weeks > 0

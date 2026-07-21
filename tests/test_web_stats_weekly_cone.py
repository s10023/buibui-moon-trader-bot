"""API-level tests for the weekly cone fields on GET /api/stats/{symbol}."""

from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from analytics.data_store import init_schema
from analytics.stats.bundle import compute_all
from web.api.models.stats import (
    CurrentWeekPathResponse,
    StatsResponse,
    WeeklyConeResponse,
)
from web.api.routers.stats import _bundle_to_response

_SYMBOL = "WAPIUSDT"
_CURRENT_WEEK = date(2026, 3, 2)


def _insert_week(
    conn: duckdb.DuckDBPyConnection, monday: date, *, k: float = 0.0, n_bars: int = 168
) -> None:
    base_ms = int(
        datetime(monday.year, monday.month, monday.day, tzinfo=UTC).timestamp() * 1000
    )
    close = 100.0 + k
    for h in range(n_bars):
        if h == 0:
            high, low = 100.5, 99.0
        elif h == 1:
            high, low = 101.0, 99.5
        else:
            high, low = max(100.5, close), min(99.5, close)
        conn.execute(
            "INSERT OR REPLACE INTO ohlcv "
            "(symbol, timeframe, open_time, open, high, low, close, volume, "
            "taker_buy_volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                _SYMBOL,
                "1h",
                base_ms + h * 3_600_000,
                100.0,
                high,
                low,
                close,
                100.0,
                50.0,
            ],
        )


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    for i in range(20):
        _insert_week(c, _CURRENT_WEEK - timedelta(weeks=20 - i), k=0.2)
    # 1d bars so compute_all's other stats have something to chew on.
    for i in range(140):
        day_ms = (
            int(datetime(2025, 11, 1, tzinfo=UTC).timestamp() * 1000) + i * 86_400_000
        )
        c.execute(
            "INSERT OR REPLACE INTO ohlcv "
            "(symbol, timeframe, open_time, open, high, low, close, volume, "
            "taker_buy_volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [_SYMBOL, "1d", day_ms, 100.0, 101.0, 99.0, 100.2, 100.0, 50.0],
        )
    # Extra 1h weeks anchored on the REAL wall-clock "now" (not the frozen _NOW
    # above). compute_adr — and several sibling stats in compute_all — window
    # off datetime.now(tz=UTC) internally with no now_ms/end_ms override, so
    # without this block ADR's 35-day lookback finds zero rows and compute_all
    # raises ValueError long before weekly_cone is ever reached.
    real_now = datetime.now(tz=UTC)
    this_real_monday = (real_now - timedelta(days=real_now.weekday())).date()
    for i in range(1, 9):
        _insert_week(c, this_real_monday - timedelta(weeks=i), k=0.2)
    return c


def test_bundle_carries_weekly_cone(conn: duckdb.DuckDBPyConnection) -> None:
    """StatsBundle gains a populated weekly_cone field."""
    bundle = compute_all(conn, _SYMBOL, 180)
    assert set(bundle.weekly_cone.combos) == {"all", "bull", "bear"}
    assert bundle.weekly_cone.total_weeks > 0


def test_weekly_cone_response_roundtrip_empty() -> None:
    """The response model serializes and validates on the degenerate shell."""
    resp = WeeklyConeResponse(combos={}, total_weeks=0)
    assert WeeklyConeResponse.model_validate_json(resp.model_dump_json()) == resp


def test_weekly_cone_response_roundtrip_populated(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """A POPULATED weekly cone actually exercises _bundle_to_response's
    weekly-cone construction (the empty-combos test above never touches the
    nested WeeklyConeCombo fields — bands/low_in_by/mae_p/etc — because
    there is nothing in `combos` to iterate), and the nested payload must
    itself survive a JSON round-trip."""
    bundle = compute_all(conn, _SYMBOL, 180)
    assert bundle.weekly_cone.combos["all"].n > 0  # genuinely populated

    response = _bundle_to_response(bundle)
    assert response.weekly_cone is not None  # _bundle_to_response always sets it
    assert response.weekly_cone.total_weeks == bundle.weekly_cone.total_weeks
    resp_all = response.weekly_cone.combos["all"]
    src_all = bundle.weekly_cone.combos["all"]
    assert resp_all.n == src_all.n
    assert resp_all.bands == src_all.bands
    assert resp_all.bands  # non-empty nested payload actually exercised

    round_tripped = StatsResponse.model_validate_json(response.model_dump_json())
    assert round_tripped.weekly_cone == response.weekly_cone


def test_current_week_path_response_optional() -> None:
    """The live overlay model is independently constructible."""
    cw = CurrentWeekPathResponse(
        points=[0.1, 0.2], elapsed_h=1, awr14_current=0.02, week_open=100.0
    )
    assert cw.elapsed_h == 1

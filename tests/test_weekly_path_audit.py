"""H10 driver tests — in-memory DuckDB only, never the real analytics.db."""

from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from analytics.store.schema import init_schema
from tools import weekly_path_audit as wpa

_HOUR_MS = 3_600_000


def _seed(conn: duckdb.DuckDBPyConnection, symbol: str, n_weeks: int) -> None:
    """n_weeks complete Monday-anchored weeks of synthetic 1h bars."""
    start = datetime(2021, 1, 4, tzinfo=UTC)  # a Monday
    rows = []
    price = 100.0
    for w in range(n_weeks):
        for b in range(168):
            ts = int((start + timedelta(weeks=w, hours=b)).timestamp() * 1000)
            price *= 1.0005 if (w + b) % 3 else 0.9995
            rows.append(
                (
                    "binance",
                    symbol,
                    "1h",
                    ts,
                    price,
                    price * 1.01,
                    price * 0.99,
                    price,
                    1.0,
                    0.5,
                )
            )
    conn.executemany(
        "INSERT INTO ohlcv_all (venue, symbol, timeframe, open_time, open, high, low, "
        "close, volume, taker_buy_volume) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )


@pytest.fixture()
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


def test_load_symbol_weeks_returns_normalized_paths(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    _seed(conn, "BTCUSDT", 40)
    now_ms = int(datetime(2021, 11, 1, tzinfo=UTC).timestamp() * 1000)
    weeks = wpa.load_symbol_weeks(conn, ["BTCUSDT"], now_ms=now_ms)
    assert weeks
    assert all(len(w.norm_path) == 168 for w in weeks)
    assert all(w.symbol == "BTCUSDT" for w in weeks)


def test_load_symbol_weeks_skips_unknown_symbol(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    _seed(conn, "BTCUSDT", 40)
    now_ms = int(datetime(2021, 11, 1, tzinfo=UTC).timestamp() * 1000)
    weeks = wpa.load_symbol_weeks(conn, ["BTCUSDT", "NOPEUSDT"], now_ms=now_ms)
    assert {w.symbol for w in weeks} == {"BTCUSDT"}


def test_render_report_states_AWR_units_and_every_hour() -> None:
    from analytics import weekly_path as wp

    verdicts = [
        wp.HourVerdict(h, wp.VERDICT_NO_EDGE, 100, 0.0, -0.1, 0.1, 0.9, 0.0, 0.0, [])
        for h in wp.GATED_HOURS
    ]
    stamps = wp.FamilyStamps(5, 24, 0.1, 0.5, 0.4, 100.0)
    text = wpa.render_report(verdicts, stamps, [], [], label="universe")
    assert "AWR" in text
    for h in wp.GATED_HOURS:
        assert f"h{h}" in text

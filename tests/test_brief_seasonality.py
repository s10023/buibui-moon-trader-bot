"""Day-ahead seasonality strip distillation."""

import duckdb

from analytics.brief.seasonality import build_strip
from tests._brief_fixtures import DAY_MS, START_MS, make_conn, seed_symbol

SYM = "BTCUSDT"
AS_OF = START_MS + 60 * DAY_MS


def test_build_strip_happy_path() -> None:
    conn = make_conn()
    seed_symbol(conn, SYM, START_MS, 60)
    strip = build_strip(conn, SYM, AS_OF, 60)
    assert strip is not None
    assert strip.dow in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    assert strip.bull_pct is not None and 0.0 <= strip.bull_pct <= 1.0
    assert strip.sample_days is not None and strip.sample_days > 0
    assert strip.high_session in ("Asia", "London", "NY")
    assert strip.low_session in ("Asia", "London", "NY")


def test_build_strip_no_data_returns_none() -> None:
    conn: duckdb.DuckDBPyConnection = make_conn()
    strip = build_strip(conn, "NODATAUSDT", AS_OF, 60)
    assert strip is None


def test_build_strip_deterministic() -> None:
    conn = make_conn()
    seed_symbol(conn, SYM, START_MS, 60)
    assert build_strip(conn, SYM, AS_OF, 60) == build_strip(conn, SYM, AS_OF, 60)

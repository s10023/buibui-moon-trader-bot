from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from analytics.xsmom.replay import _drop_unclosed_daily, replay_targets

_DAY = 86_400_000


def _seed(conn: duckdb.DuckDBPyConnection, n: int = 400) -> list[str]:
    rng = np.random.default_rng(3)
    start = 1_609_459_200_000  # 2021-01-01 UTC
    syms = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    for i, sym in enumerate(syms):
        steps = rng.normal(0.0005 * (i - 1), 0.02, n)
        close = 100.0 * np.exp(np.cumsum(steps))
        rows = pd.DataFrame(
            {
                "symbol": sym,
                "timeframe": "1d",
                "open_time": [start + k * _DAY for k in range(n)],
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1000.0,
                "taker_buy_volume": 500.0,
            }
        )
        upsert_ohlcv(conn, rows)
    return syms


def test_replay_targets_builds_book() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn)
    book = replay_targets(conn, ForecastConfig(), 10_000.0, symbols=syms)
    assert book.capital == 10_000.0
    assert book.active_count >= 1
    assert len(book.positions) == book.active_count


def test_drop_unclosed_daily_drops_forming_keeps_closed() -> None:
    idx = pd.to_datetime(["2026-06-21", "2026-06-22", "2026-06-23"], utc=True)
    s = pd.Series([1.0, 2.0, 3.0], index=idx)
    # now = 6h into 2026-06-23; that bar closes 2026-06-24 00:00 -> dropped.
    now = pd.Timestamp("2026-06-23 06:00", tz="UTC")
    out = _drop_unclosed_daily({"X": s}, now)["X"]
    assert list(out.index) == list(idx[:2])


def test_drop_unclosed_daily_boundary_close_time_kept() -> None:
    idx = pd.to_datetime(["2026-06-22", "2026-06-23"], utc=True)
    s = pd.Series([2.0, 3.0], index=idx)
    # now == exactly the 2026-06-23 bar's close-time -> closed, kept.
    now = pd.Timestamp("2026-06-24 00:00", tz="UTC")
    out = _drop_unclosed_daily({"X": s}, now)["X"]
    assert list(out.index) == list(idx)


def test_replay_targets_drops_unclosed_trailing_bar() -> None:
    # Mimics the live field test: the daemon advanced ONE symbol onto a still
    # forming daily bar the others lack. Without the guard the book aligns to
    # that partial bar -> active_count degenerates to 1 (look-ahead). With it,
    # the book aligns on the last completed close shared by all symbols.
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = _seed(conn, n=400)  # all 3 symbols, day 0..399
    start = 1_609_459_200_000
    extra_open = start + 400 * _DAY  # a forming day-400 bar for one symbol only
    upsert_ohlcv(
        conn,
        pd.DataFrame(
            {
                "symbol": ["AAAUSDT"],
                "timeframe": ["1d"],
                "open_time": [extra_open],
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.0],
                "volume": [1000.0],
                "taker_buy_volume": [500.0],
            }
        ),
    )
    # now = 6h into day 400 -> the day-400 bar (closes at day 401 00:00) is unclosed.
    now = pd.Timestamp(extra_open, unit="ms", tz="UTC") + pd.Timedelta(hours=6)
    book = replay_targets(conn, ForecastConfig(), 10_000.0, symbols=syms, now=now)
    last_closed = pd.Timestamp(start + 399 * _DAY, unit="ms", tz="UTC")
    assert book.as_of_date == last_closed.date().isoformat()
    assert book.active_count == 3

import duckdb
import numpy as np
import pandas as pd

from analytics.cvd.replay import (
    cvd_universe,
    load_divergences,
    replay_cvd_ts_trials,
    replay_cvd_xs,
    replay_cvd_xs_trials,
)
from analytics.forecast.config import ForecastConfig
from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from analytics.store.spot_data import upsert_spot_ohlcv

DAY = 86_400_000


def _seed(conn: duckdb.DuckDBPyConnection, symbols: list[str], n: int = 500) -> None:
    rng = np.random.default_rng(4)
    for sym in symbols:
        times = [i * DAY for i in range(n)]
        close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
        vol = rng.uniform(50.0, 150.0, n)
        upsert_ohlcv(
            conn,
            pd.DataFrame(
                {
                    "symbol": sym,
                    "timeframe": "1d",
                    "open_time": times,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": vol,
                    "taker_buy_volume": vol * rng.uniform(0.3, 0.7, n),
                }
            ),
        )
        svol = rng.uniform(50.0, 150.0, n)
        upsert_spot_ohlcv(
            conn,
            pd.DataFrame(
                {
                    "symbol": sym,
                    "open_time": times,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": svol,
                    "taker_buy_volume": svol * rng.uniform(0.3, 0.7, n),
                }
            ),
        )


def _conn(symbols: list[str]) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, symbols)
    return conn


def test_cvd_universe_drops_the_two_perp_only_symbols() -> None:
    out = cvd_universe(["BTCUSDT", "HYPEUSDT", "VVVUSDT", "ETHUSDT"])
    assert out == ["BTCUSDT", "ETHUSDT"]


def test_load_divergences_returns_one_series_per_symbol() -> None:
    conn = _conn(["BTCUSDT", "ETHUSDT"])
    out = load_divergences(conn, ["BTCUSDT", "ETHUSDT"])
    assert set(out) == {"BTCUSDT", "ETHUSDT"}
    assert isinstance(out["BTCUSDT"].index, pd.DatetimeIndex)
    assert out["BTCUSDT"].abs().max() <= 2.0


def test_load_divergences_skips_a_symbol_with_no_spot_rows() -> None:
    conn = _conn(["BTCUSDT"])
    out = load_divergences(conn, ["BTCUSDT", "ETHUSDT"])
    assert set(out) == {"BTCUSDT"}


def test_xs_replay_produces_a_finite_return_series() -> None:
    conn = _conn(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    result = replay_cvd_xs(
        conn, ForecastConfig(), symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    )
    assert len(result.portfolio_return) > 0
    assert np.isfinite(result.portfolio_return).all()


def test_trial_family_has_the_five_declared_keys() -> None:
    conn = _conn(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    xs = replay_cvd_xs_trials(conn, ForecastConfig(), symbols=syms)
    ts = replay_cvd_ts_trials(conn, ForecastConfig(), symbols=syms)
    expected = {"span8", "span16", "span32", "span64", "combined"}
    assert set(xs) == expected
    assert set(ts) == expected


def test_trials_are_not_all_identical() -> None:
    """A family whose members are the same series makes PBO meaningless."""
    conn = _conn(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    trials = replay_cvd_xs_trials(
        conn, ForecastConfig(), symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    )
    assert not np.allclose(trials["span8"], trials["span64"])

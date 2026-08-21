import duckdb
import numpy as np
import pandas as pd

from analytics.cvd.forecast import cvd_forecast
from analytics.cvd.replay import (
    _ts_forecasts,
    cvd_universe,
    load_divergences,
    replay_cvd_ts,
    replay_cvd_ts_trials,
    replay_cvd_xs,
    replay_cvd_xs_trials,
    replay_xsmom_benchmark,
)
from analytics.forecast.book import run_forecast_backtest
from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs
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
            venue="binance",
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


# ---------------------------------------------------------------------------
# FINDING 2 — mutation-tested coverage: the reviewer found that deleting
# `forecasts=` from `replay_cvd_xs` or `replay_cvd_ts` (silently swapping the
# CVD forecast for the incumbent EWMAC book) left 169 / 121 tests green
# respectively, and that bypassing the FDM-applying helper for the TS
# single-span trials did too. Each test below is proven non-vacuous by
# applying the exact corresponding mutation and confirming it fails — see
# the final-fix-report for the before/after evidence.
# ---------------------------------------------------------------------------

_SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def test_xs_book_differs_from_the_plain_xsmom_benchmark() -> None:
    """`replay_cvd_xs` must inject the CVD forecast matrix; if `forecasts=`
    were ever dropped, Shape A would silently become plain XS-momentum on the
    same 23-symbol universe — a restatement of the incumbent sleeve wearing
    the D1 study's name."""
    conn = _conn(_SYMS)
    cfg = ForecastConfig()
    cvd = replay_cvd_xs(conn, cfg, symbols=_SYMS)
    bench = replay_xsmom_benchmark(conn, cfg, symbols=_SYMS)
    assert not np.allclose(cvd.portfolio_return, bench.portfolio_return)


def test_ts_book_differs_from_the_plain_ewmac_book() -> None:
    """`replay_cvd_ts` must inject the CVD forecast; if `forecasts=` were
    ever dropped, Shape B would silently become the shelved EWMAC trend
    sleeve run over the CVD universe."""
    conn = _conn(_SYMS)
    cfg = ForecastConfig()
    cvd = replay_cvd_ts(conn, cfg, symbols=_SYMS)
    closes, fundings = load_daily_inputs(conn, _SYMS)
    ewmac = run_forecast_backtest(closes, fundings, cfg)
    assert not np.allclose(cvd.portfolio_return, ewmac.portfolio_return)


def test_ts_single_span_trial_routes_through_the_fdm_matching_helper() -> None:
    """The TS single-span trials must be built through `_ts_forecasts` (->
    `_matrix` -> `cvd_combined_forecast`) — the SAME path the XS single-span
    trials use — so the FDM is applied identically on both shapes (plan
    defect #3). Calling `cvd_forecast` directly instead would skip the FDM on
    the TS side only, making any XS-vs-TS difference partly a forecast
    artefact rather than purely a book-construction one."""
    conn = _conn(_SYMS)
    cfg = ForecastConfig()
    xs = load_divergences(conn, _SYMS)
    closes, fundings = load_daily_inputs(conn, list(xs))

    trials = replay_cvd_ts_trials(conn, cfg, symbols=_SYMS)

    fdm_routed = run_forecast_backtest(
        closes, fundings, cfg, forecasts=_ts_forecasts(xs, cfg, (8,))
    ).portfolio_return
    assert np.allclose(trials["span8"], fdm_routed)

    bare_forecasts = {
        sym: cvd_forecast(x, 8, cfg.vol_span, cfg.cap) for sym, x in xs.items()
    }
    bare = run_forecast_backtest(
        closes, fundings, cfg, forecasts=bare_forecasts
    ).portfolio_return
    assert not np.allclose(trials["span8"], bare)

"""Read-only DuckDB front door for the D1 spot-perp CVD sleeve.

Reuses the trend sleeve's ``load_daily_inputs`` for closes and funding, adds the
spot leg, and runs both book shapes. The only module in ``analytics/cvd/`` that
touches the DB; never writes.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from analytics.cvd.fetch import spot_symbol_for
from analytics.cvd.forecast import DEFAULT_SPANS, cvd_forecast_matrix
from analytics.cvd.imbalance import divergence
from analytics.forecast.book import ForecastBookResult, run_forecast_backtest
from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs
from analytics.store.market_data import get_ohlcv
from analytics.store.spot_data import get_spot_ohlcv
from analytics.universe import load_universe
from analytics.xsmom.book import XSBookResult, run_xs_backtest

_FAR_PAST = 0
_FAR_FUTURE = 9_999_999_999_999


def cvd_universe(symbols: list[str] | None = None) -> list[str]:
    """Universe symbols that have a spot pair — 23 of the committed 25."""
    syms = symbols if symbols is not None else load_universe()
    return [s for s in syms if spot_symbol_for(s) is not None]


def load_divergences(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> dict[str, pd.Series]:
    """``imb_spot - imb_perp`` per symbol. Symbols missing either leg are skipped."""
    out: dict[str, pd.Series] = {}
    for sym in symbols:
        spot_sym = spot_symbol_for(sym)
        if spot_sym is None:
            continue
        perp = get_ohlcv(conn, sym, "1d", _FAR_PAST, _FAR_FUTURE)
        spot = get_spot_ohlcv(conn, sym, _FAR_PAST, _FAR_FUTURE)
        if perp.empty or spot.empty:
            continue
        x = divergence(spot, perp)
        if not x.empty:
            out[sym] = x
    return out


def _inputs(
    conn: duckdb.DuckDBPyConnection, symbols: list[str] | None
) -> tuple[dict[str, pd.Series], dict[str, pd.Series], dict[str, pd.Series]]:
    syms = cvd_universe(symbols)
    xs = load_divergences(conn, syms)
    closes, fundings = load_daily_inputs(conn, list(xs))
    keep = set(closes) & set(xs)
    return (
        {k: v for k, v in closes.items() if k in keep},
        {k: v for k, v in fundings.items() if k in keep},
        {k: v for k, v in xs.items() if k in keep},
    )


def _matrix(
    x_by_symbol: dict[str, pd.Series], cfg: ForecastConfig, spans: tuple[int, ...]
) -> pd.DataFrame:
    return cvd_forecast_matrix(
        x_by_symbol, spans=spans, fdm=cfg.fdm, vol_span=cfg.vol_span, cap=cfg.cap
    )


def replay_cvd_xs(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    *,
    symbols: list[str] | None = None,
    spans: tuple[int, ...] = DEFAULT_SPANS,
) -> XSBookResult:
    """Shape A — the dollar-neutral cross-sectional book over the CVD forecast."""
    closes, fundings, xs = _inputs(conn, symbols)
    return run_xs_backtest(closes, fundings, cfg, forecasts=_matrix(xs, cfg, spans))


def replay_cvd_xs_trials(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    *,
    symbols: list[str] | None = None,
    spans: tuple[int, ...] = DEFAULT_SPANS,
) -> dict[str, np.ndarray]:
    """Daily XS portfolio returns per single-span sleeve + the combined book.

    The declared multiple-testing family for DSR/PBO. Keys: `span{n}` per span,
    plus `combined`.
    """
    closes, fundings, xs = _inputs(conn, symbols)
    trials: dict[str, np.ndarray] = {}
    for span in spans:
        one = _matrix(xs, cfg, (span,))
        trials[f"span{span}"] = run_xs_backtest(
            closes, fundings, cfg, forecasts=one
        ).portfolio_return
    trials["combined"] = run_xs_backtest(
        closes, fundings, cfg, forecasts=_matrix(xs, cfg, spans)
    ).portfolio_return
    return trials


def _ts_forecasts(
    x_by_symbol: dict[str, pd.Series],
    cfg: ForecastConfig,
    spans: tuple[int, ...],
) -> dict[str, pd.Series]:
    mat = _matrix(x_by_symbol, cfg, spans)
    return {sym: mat[sym] for sym in mat.columns}


def replay_cvd_ts(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    *,
    symbols: list[str] | None = None,
    spans: tuple[int, ...] = DEFAULT_SPANS,
) -> ForecastBookResult:
    """Shape B — the per-symbol time-series book over the CVD forecast."""
    closes, fundings, xs = _inputs(conn, symbols)
    return run_forecast_backtest(
        closes, fundings, cfg, forecasts=_ts_forecasts(xs, cfg, spans)
    )


def replay_cvd_ts_trials(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    *,
    symbols: list[str] | None = None,
    spans: tuple[int, ...] = DEFAULT_SPANS,
) -> dict[str, np.ndarray]:
    """Daily TS portfolio returns per single-span sleeve + the combined book."""
    closes, fundings, xs = _inputs(conn, symbols)
    trials: dict[str, np.ndarray] = {}
    for span in spans:
        # Built through the SAME function as the XS single-span trials
        # (`_ts_forecasts` -> `_matrix` -> `cvd_combined_forecast`), so the FDM
        # and cap are applied identically in both shapes. Calling `cvd_forecast`
        # directly here would skip the FDM on the TS side only, and then any
        # measured difference between the two shapes would be partly a forecast
        # difference rather than purely a book difference — which is the one
        # comparison this study exists to make. It also matches the house
        # pattern: `replay_xs_trials` runs its single-speed trials through
        # `combine_forecasts`, which applies the FDM to a one-element family too.
        trials[f"span{span}"] = run_forecast_backtest(
            closes, fundings, cfg, forecasts=_ts_forecasts(xs, cfg, (span,))
        ).portfolio_return
    trials["combined"] = run_forecast_backtest(
        closes, fundings, cfg, forecasts=_ts_forecasts(xs, cfg, spans)
    ).portfolio_return
    return trials


def replay_xsmom_benchmark(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    *,
    symbols: list[str] | None = None,
) -> XSBookResult:
    """XS-momentum on the SAME 23 symbols, for a like-for-like comparison.

    The published +1.375 Sharpe is a 25-symbol number; comparing a 23-symbol CVD
    book against it directly would attribute a universe difference to the signal.
    """
    closes, fundings, _ = _inputs(conn, symbols)
    return run_xs_backtest(closes, fundings, cfg)

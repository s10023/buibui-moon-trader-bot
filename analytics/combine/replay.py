"""Read-only DuckDB front door for the trend×XS combine layer.

Runs both validated sleeves over the shared 1d inputs, then the combine book. The
only module in `analytics/combine/` that touches the DB; never writes. The XS book
uses `cfg.sleeve_cfg` (the validated original, dollar-neutral off); the trend book
ignores the XS-only flag.
"""

from __future__ import annotations

import duckdb
import numpy as np

from analytics.combine.book import CombinedBookResult, combine_books
from analytics.combine.config import CombineConfig
from analytics.forecast.book import ForecastBookResult, run_forecast_backtest
from analytics.forecast.replay import load_daily_inputs
from analytics.universe import load_universe
from analytics.xsmom.book import XSBookResult, run_xs_backtest


def load_sleeves(
    conn: duckdb.DuckDBPyConnection,
    cfg: CombineConfig,
    symbols: list[str] | None = None,
) -> tuple[XSBookResult, ForecastBookResult]:
    """Run both sleeves once over the shared 1d inputs (read-only)."""
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)
    xs = run_xs_backtest(closes, fundings, cfg.sleeve_cfg)
    trend = run_forecast_backtest(closes, fundings, cfg.sleeve_cfg)
    return xs, trend


def replay_combined(
    conn: duckdb.DuckDBPyConnection,
    cfg: CombineConfig,
    symbols: list[str] | None = None,
) -> CombinedBookResult:
    """Load both sleeves and run the combine book (read-only)."""
    xs, trend = load_sleeves(conn, cfg, symbols)
    return combine_books(xs, trend, cfg)


def replay_combined_trials(
    conn: duckdb.DuckDBPyConnection,
    cfg: CombineConfig,
    symbols: list[str] | None = None,
) -> dict[str, np.ndarray]:
    """The honest gate family for DSR/PBO: {trend, XS, combined} daily returns."""
    xs, trend = load_sleeves(conn, cfg, symbols)
    combined = combine_books(xs, trend, cfg)
    return {
        "trend": trend.portfolio_return,
        "xs": xs.portfolio_return,
        "combined": combined.portfolio_return,
    }

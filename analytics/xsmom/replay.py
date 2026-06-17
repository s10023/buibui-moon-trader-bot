"""Read-only DuckDB front door for the cross-sectional momentum sleeve.

Reuses the trend sleeve's `load_daily_inputs` (1d closes + summed daily funding)
and runs the XS book. The only module in `analytics/xsmom/` that touches the DB;
never writes.
"""

from __future__ import annotations

import dataclasses

import duckdb
import numpy as np

from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs
from analytics.universe import load_universe
from analytics.xsmom.book import XSBookResult, run_xs_backtest


def replay_xs(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
) -> XSBookResult:
    """Load the universe's 1d inputs and run the XS book (read-only)."""
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)
    return run_xs_backtest(closes, fundings, cfg)


def replay_xs_trials(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
) -> dict[str, np.ndarray]:
    """Daily XS portfolio returns per single-speed sleeve + the combined book.

    The honest multiple-testing family for DSR/PBO. Keys:
    `s{fast}_{slow}` per speed in `cfg.speeds`, plus `combined`.
    """
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)

    trials: dict[str, np.ndarray] = {}
    for fast, slow, scalar in cfg.speeds:
        single_cfg = dataclasses.replace(cfg, speeds=((fast, slow, scalar),))
        result = run_xs_backtest(closes, fundings, single_cfg)
        trials[f"s{fast}_{slow}"] = result.portfolio_return

    combined = run_xs_backtest(closes, fundings, cfg)
    trials["combined"] = combined.portfolio_return
    return trials

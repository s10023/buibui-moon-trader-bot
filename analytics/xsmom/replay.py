"""Read-only DuckDB front door for the cross-sectional momentum sleeve.

Reuses the trend sleeve's `load_daily_inputs` (1d closes + summed daily funding)
and runs the XS book. The only module in `analytics/xsmom/` that touches the DB;
never writes.
"""

from __future__ import annotations

import dataclasses

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs
from analytics.store.market_data import get_ohlcv
from analytics.universe import load_universe
from analytics.xsmom.book import XSBookResult, run_xs_backtest
from analytics.xsmom.execution import (
    ExecutionCostConfig,
    dollar_adv,
    run_xs_with_costs,
)

_FAR_PAST = 0
_FAR_FUTURE = 9_999_999_999_999


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


def load_daily_dollar_volumes(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
) -> dict[str, pd.Series]:
    """Per-symbol daily dollar volume (`volume * close`), day-indexed.

    Read-only sibling of `load_daily_inputs`; the impact term's ADV source.
    Symbols with no OHLCV are silently skipped.
    """
    out: dict[str, pd.Series] = {}
    for sym in symbols:
        bars = get_ohlcv(conn, sym, "1d", _FAR_PAST, _FAR_FUTURE)
        if bars.empty:
            continue
        idx = pd.to_datetime(bars["open_time"], unit="ms", utc=True).dt.normalize()
        dv = pd.Series(
            bars["volume"].to_numpy(dtype=float) * bars["close"].to_numpy(dtype=float),
            index=idx,
        )
        out[sym] = dv[~dv.index.duplicated(keep="last")].sort_index()
    return out


def replay_xs_capacity(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    exec_cfg: ExecutionCostConfig,
    capitals: list[float],
    symbols: list[str] | None = None,
) -> dict[float, dict[str, object]]:
    """Run the XS book + its DSR/PBO trial family under size-aware costs per capital.

    For each target capital `C`: rebuild each trial's own cost-rate (cost depends
    on that trial's `|Δlev|`), run the headline combined book and every
    single-speed sleeve. Returns `{C: {"result": XSBookResult, "trials": {...}}}`.
    The dollar-ADV is independent of `C`, so it is computed once.
    """
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)
    dvol = load_daily_dollar_volumes(conn, syms)
    adv = dollar_adv(dvol, exec_cfg.adv_window)

    out: dict[float, dict[str, object]] = {}
    for capital in capitals:
        ec = dataclasses.replace(exec_cfg, capital=capital)
        result = run_xs_with_costs(closes, fundings, cfg, ec, adv)
        trials: dict[str, np.ndarray] = {}
        for fast, slow, scalar in cfg.speeds:
            single = dataclasses.replace(cfg, speeds=((fast, slow, scalar),))
            trials[f"s{fast}_{slow}"] = run_xs_with_costs(
                closes, fundings, single, ec, adv
            ).portfolio_return
        trials["combined"] = result.portfolio_return
        out[capital] = {"result": result, "trials": trials}
    return out

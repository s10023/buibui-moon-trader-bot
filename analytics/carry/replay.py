"""Read-only DuckDB front door for the carry sleeve.

Reuses the trend sleeve's ``load_daily_inputs`` (1d closes + summed daily funding)
and runs the carry book. The only module in ``analytics/carry/`` that touches the DB;
never writes.
"""

from __future__ import annotations

import dataclasses

import duckdb
import numpy as np

from analytics.carry.book import CarryBookResult, run_carry_backtest
from analytics.carry.config import CarryConfig
from analytics.forecast.replay import load_daily_inputs
from analytics.universe import load_universe


def replay_carry(
    conn: duckdb.DuckDBPyConnection,
    cfg: CarryConfig,
    symbols: list[str] | None = None,
) -> CarryBookResult:
    """Load the universe's 1d inputs and run the carry book (read-only)."""
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)
    return run_carry_backtest(closes, fundings, cfg)


def replay_carry_trials(
    conn: duckdb.DuckDBPyConnection,
    cfg: CarryConfig,
    symbols: list[str] | None = None,
) -> dict[str, np.ndarray]:
    """Daily carry portfolio returns per single-span book + the combined book.

    The honest multiple-testing family for DSR/PBO, all under ``cfg.cross_sectional``.
    Keys: ``span{s}`` per span in ``cfg.carry_spans``, plus ``combined``.
    """
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)

    trials: dict[str, np.ndarray] = {}
    for s in cfg.carry_spans:
        single = dataclasses.replace(cfg, carry_spans=(s,))
        trials[f"span{s}"] = run_carry_backtest(
            closes, fundings, single
        ).portfolio_return
    trials["combined"] = run_carry_backtest(closes, fundings, cfg).portfolio_return
    return trials

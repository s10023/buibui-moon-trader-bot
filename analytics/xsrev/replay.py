"""Read-only DuckDB front door for the cross-sectional reversal sleeve.

Reuses the trend sleeve's ``load_daily_inputs`` (1d closes + summed daily funding)
and runs the reversal book. Read-only; never writes.
"""

from __future__ import annotations

import dataclasses

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.replay import load_daily_inputs
from analytics.universe import load_universe
from analytics.xsmom.book import XSBookResult
from analytics.xsrev.book import run_xsrev_backtest
from analytics.xsrev.config import ReversalConfig


def replay_xsrev(
    conn: duckdb.DuckDBPyConnection,
    cfg: ReversalConfig,
    symbols: list[str] | None = None,
) -> XSBookResult:
    """Load the universe's 1d inputs and run the reversal book (read-only)."""
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)
    return run_xsrev_backtest(closes, fundings, cfg)


def replay_xsrev_trials(
    conn: duckdb.DuckDBPyConnection,
    cfg: ReversalConfig,
    symbols: list[str] | None = None,
) -> dict[str, np.ndarray]:
    """Daily reversal portfolio returns per single-window sleeve + the combined book.

    The honest multiple-testing family for DSR/PBO. Keys: ``k{w}`` per window in
    ``cfg.formation_windows``, plus ``combined``.
    """
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)

    trials: dict[str, np.ndarray] = {}
    for w in cfg.formation_windows:
        single = dataclasses.replace(cfg, formation_windows=(w,))
        trials[f"k{w}"] = run_xsrev_backtest(closes, fundings, single).portfolio_return

    combined = run_xsrev_backtest(closes, fundings, cfg)
    trials["combined"] = combined.portfolio_return
    return trials


def load_daily_open_interest(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
) -> dict[str, pd.Series]:
    """Per-symbol daily open interest (last ``oi_usd`` per UTC day), day-indexed.

    DESCRIPTIVE-ONLY: the ``open_interest`` table is shallow (Binance recent-only,
    ~2026-02-28 onward). Read-only. Symbols with no OI rows are skipped.
    """
    out: dict[str, pd.Series] = {}
    for sym in symbols:
        df = conn.execute(
            "SELECT timestamp, oi_usd FROM open_interest "
            "WHERE symbol = ? ORDER BY timestamp",
            [sym],
        ).df()
        if df.empty:
            continue
        idx = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.normalize()
        s = pd.Series(df["oi_usd"].to_numpy(dtype=float), index=idx)
        out[sym] = s[~s.index.duplicated(keep="last")].sort_index()
    return out

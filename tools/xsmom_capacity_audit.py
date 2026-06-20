"""XS-momentum execution-realism capacity audit (P3) — read-only verdict.

Re-scores the XS sleeve's fixed (causal) position path under a per-instrument,
size-aware cost model (a-priori tiered half-spread + sqrt/linear market impact
vs trailing dollar-ADV) across a grid of target capital, and prints the AUM at
which the +1.375 edge decays below the de-biased gate, plus impact-k, spread-tier
and sqrt-vs-linear sensitivities.

Read-only — no writes, no schema changes.

Usage::

    PYTHONPATH=. poetry run python tools/xsmom_capacity_audit.py
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

import duckdb
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.store import DEFAULT_DB_PATH
from analytics.universe import load_universe
from analytics.xsmom import (
    ExecutionCostConfig,
    evaluate_xs_capacity,
    replay_xs_capacity,
)

_CAPITALS = [1e5, 1e6, 5e6, 1e7, 2.5e7, 5e7, 1e8]


def _print_df(title: str, df: pd.DataFrame) -> None:
    print(f"\n=== {title} ===")
    if df.empty:
        print("(no rows)")
        return
    print(df.to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    args = parser.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")
    syms = load_universe()
    cfg = ForecastConfig()

    # 1. Headline capacity sweep (base k, sqrt impact).
    base = ExecutionCostConfig()
    runs = replay_xs_capacity(conn, cfg, base, _CAPITALS, symbols=syms)
    _print_df("Capacity sweep (base k, sqrt)", evaluate_xs_capacity(runs, cfg))

    # 2. Impact-k sensitivity.
    for k in (0.05, 0.1, 0.2):
        ec = dataclasses.replace(base, k=k)
        runs_k = replay_xs_capacity(conn, cfg, ec, _CAPITALS, symbols=syms)
        _print_df(f"Capacity sweep (k={k})", evaluate_xs_capacity(runs_k, cfg))

    # 3. Spread-tier sensitivity (tighter / wider).
    tight = dataclasses.replace(base, major_bps=0.5, mid_bps=1.5, alt_bps=4.0)
    wide = dataclasses.replace(base, major_bps=2.0, mid_bps=6.0, alt_bps=16.0)
    _print_df(
        "Capacity sweep (tight spreads)",
        evaluate_xs_capacity(
            replay_xs_capacity(conn, cfg, tight, _CAPITALS, symbols=syms), cfg
        ),
    )
    _print_df(
        "Capacity sweep (wide spreads)",
        evaluate_xs_capacity(
            replay_xs_capacity(conn, cfg, wide, _CAPITALS, symbols=syms), cfg
        ),
    )

    # 4. sqrt vs linear impact form.
    lin = dataclasses.replace(base, impact="linear")
    _print_df(
        "Capacity sweep (linear impact)",
        evaluate_xs_capacity(
            replay_xs_capacity(conn, cfg, lin, _CAPITALS, symbols=syms), cfg
        ),
    )

    conn.close()


if __name__ == "__main__":
    main()

"""EWMAC trend sleeve audit (P2) — read-only G2 verdict.

Runs the continuous multi-speed EWMAC trend forecast across the N3 universe
through the portfolio-vol-targeted paper book and prints the gate-G2 read:
portfolio Sharpe/Sortino/max-DD with DSR/PBO/bootstrap-CI/MinTRL stamps, a
cost-sensitivity sweep, a breadth (universe vs majors-only) contrast, and the
per-speed Sharpes (the H2 cycle-bias check).

Read-only — no writes, no schema changes.

Usage::

    PYTHONPATH=. poetry run python tools/forecast_audit.py
    PYTHONPATH=. poetry run python tools/forecast_audit.py --majors BTCUSDT,ETHUSDT,SOLUSDT
"""

from __future__ import annotations

import argparse
import dataclasses
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast import (
    ForecastConfig,
    evaluate,
    replay_trials,
    replay_universe,
)
from analytics.store import DEFAULT_DB_PATH
from analytics.universe import load_universe

_MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def build_report_row(
    conn: duckdb.DuckDBPyConnection,
    label: str,
    symbols: list[str],
    slippage_bps: float,
) -> dict[str, object]:
    cfg = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    result = replay_universe(conn, cfg, symbols=symbols)
    trials = replay_trials(conn, cfg, symbols=symbols)
    rep = evaluate(result, cfg, trial_returns=trials)
    return {
        "label": label,
        "n_inst": len(result.per_instrument_net),
        "days": rep.n_obs,
        "sharpe": rep.sharpe_annual,
        "sortino": rep.sortino_annual,
        "max_dd": rep.max_dd,
        "ann_ret": rep.annual_return,
        "ann_vol": rep.annual_vol,
        "dsr": rep.dsr,
        "pbo": rep.pbo,
        "boot_lo": rep.boot_lo,
        "boot_hi": rep.boot_hi,
        "min_trl": rep.min_trl,
    }


def _per_speed_sharpes(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.DataFrame:
    cfg = ForecastConfig()
    trials = replay_trials(conn, cfg, symbols=symbols)
    ann = math.sqrt(cfg.annualization_days)
    rows = []
    for name, r in trials.items():
        sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
        sr = (float(np.mean(r)) / sd * ann) if sd > 1e-12 else 0.0
        rows.append({"trial": name, "sharpe": sr})
    return pd.DataFrame(rows)


def _print_df(title: str, df: pd.DataFrame) -> None:
    print(f"\n=== {title} ===")
    if df.empty:
        print("(no rows)")
        return
    print(df.to_string(index=False, float_format=lambda x: f"{x:+.3f}"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    parser.add_argument(
        "--majors",
        type=str,
        default=",".join(_MAJORS),
        help="comma-separated majors-only contrast set",
    )
    args = parser.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")

    universe = load_universe()
    majors = [s.strip().upper() for s in args.majors.split(",") if s.strip()]

    rows = [
        build_report_row(conn, "universe @2bps", universe, 2.0),
        build_report_row(conn, "majors @2bps", majors, 2.0),
    ]
    _print_df("Gate G2 — breadth contrast", pd.DataFrame(rows))

    sweep = [
        build_report_row(conn, f"universe @{b:g}bps", universe, b)
        for b in (0.0, 2.0, 8.0, 16.0)
    ]
    _print_df("Cost sensitivity (universe)", pd.DataFrame(sweep))

    _print_df(
        "Per-speed Sharpe (H2: s64_256 vs combined)", _per_speed_sharpes(conn, universe)
    )

    print(
        "\nG2 = trend-sleeve OOS Sharpe >= ~1 on the universe, costs in, "
        "DSR/PBO-gated. Read boot_lo (annualised Sharpe CI lower bound) and pbo "
        "alongside the headline before calling PASS/MARGINAL/FAIL."
    )


if __name__ == "__main__":
    main()

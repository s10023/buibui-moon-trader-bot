"""Cross-sectional momentum sleeve audit (P3) — read-only verdict.

Replays the demeaned-EWMAC relative-strength book across the N3 universe (1d) and
prints: a breadth contrast (universe vs majors-only), a cost-sensitivity sweep,
the per-speed XS Sharpes, and the diversification read (correlation to the trend
sleeve) — each with DSR/PBO/bootstrap-CI/MinTRL stamps.

Read-only — no writes, no schema changes.

Usage::

    PYTHONPATH=. poetry run python tools/xsmom_audit.py
    PYTHONPATH=. poetry run python tools/xsmom_audit.py --majors BTCUSDT,ETHUSDT,SOLUSDT
"""

from __future__ import annotations

import argparse
import dataclasses
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast import ForecastConfig, replay_universe
from analytics.store import DEFAULT_DB_PATH
from analytics.universe import load_universe
from analytics.xsmom import evaluate_xs, replay_xs, replay_xs_trials

_MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def build_xs_report_row(
    conn: duckdb.DuckDBPyConnection,
    label: str,
    symbols: list[str],
    slippage_bps: float,
) -> dict[str, object]:
    cfg = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    result = replay_xs(conn, cfg, symbols=symbols)
    trials = replay_xs_trials(conn, cfg, symbols=symbols)
    trend = replay_universe(conn, cfg, symbols=symbols).portfolio_return
    rep = evaluate_xs(result, cfg, trial_returns=trials, trend_returns=trend)
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
        "corr_to_trend": rep.corr_to_trend,
        "trend_sharpe": rep.trend_sharpe,
    }


def _per_speed_xs_sharpes(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.DataFrame:
    cfg = ForecastConfig()
    ann = math.sqrt(cfg.annualization_days)
    trials = replay_xs_trials(conn, cfg, symbols=symbols)
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
        build_xs_report_row(conn, "universe @2bps", universe, 2.0),
        build_xs_report_row(conn, "majors @2bps", majors, 2.0),
    ]
    _print_df("Gate G3 — XS breadth contrast", pd.DataFrame(rows))

    sweep = [
        build_xs_report_row(conn, f"universe @{b:g}bps", universe, b)
        for b in (0.0, 2.0, 8.0, 16.0)
    ]
    _print_df("Cost sensitivity (universe)", pd.DataFrame(sweep))

    _print_df("Per-speed XS Sharpe", _per_speed_xs_sharpes(conn, universe))

    print(
        "\nG3 read: is the XS sleeve positive, cost-robust, DSR/PBO-survivable, AND "
        "low-correlated to trend (corr_to_trend near 0)? A modest-Sharpe XS sleeve "
        "uncorrelated with trend is a real combine win (P3 IDM layer). Read "
        "corr_to_trend + boot_lo + pbo alongside the headline before calling it."
    )


if __name__ == "__main__":
    main()

"""Trend×XS combine-layer audit (P3) — read-only IDM portfolio verdict.

Replays the two validated sleeves over the N3 universe (1d), combines them in
book-return space with a causal-rolling Carver IDM, and prints: the gate verdict
({trend, XS, combined} headline + DSR/PBO/bootstrap-CI/MinTRL + PASS/FAIL), a
diversification read (correlation, realized IDM, vol reduction, sleeve
contribution), and sensitivity panels — sleeve weights, IDM mode (causal vs
static), and cost (with the combined cost-drag check) — plus a breadth contrast.

Read-only — no writes, no schema changes.

Usage::

    PYTHONPATH=. poetry run python tools/combine_audit.py
    PYTHONPATH=. poetry run python tools/combine_audit.py --majors BTCUSDT,ETHUSDT,SOLUSDT
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

import duckdb
import pandas as pd

from analytics.combine import (
    CombineConfig,
    combine_gate_verdict,
    evaluate_combined,
    load_sleeves,
)
from analytics.combine.book import combine_books
from analytics.forecast.config import ForecastConfig
from analytics.store import DEFAULT_DB_PATH
from analytics.universe import load_universe

_MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def _cfg(
    slippage_bps: float,
    w_xs: float = 0.5,
    w_trend: float = 0.5,
    idm_mode: str = "causal",
) -> CombineConfig:
    sleeve = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    return CombineConfig(
        sleeve_cfg=sleeve, w_xs=w_xs, w_trend=w_trend, idm_mode=idm_mode
    )


def build_combine_report_row(
    conn: duckdb.DuckDBPyConnection,
    label: str,
    symbols: list[str],
    slippage_bps: float,
    w_xs: float = 0.5,
    w_trend: float = 0.5,
    idm_mode: str = "causal",
) -> dict[str, object]:
    cfg = _cfg(slippage_bps, w_xs, w_trend, idm_mode)
    xs, trend = load_sleeves(conn, cfg, symbols=symbols)
    combined = combine_books(xs, trend, cfg)
    trials = {
        "trend": trend.portfolio_return,
        "xs": xs.portfolio_return,
        "combined": combined.portfolio_return,
    }
    rep = evaluate_combined(
        combined, cfg, trials, xs.portfolio_return, trend.portfolio_return
    )
    return {
        "label": label,
        "days": rep.n_obs,
        "sharpe": rep.sharpe_annual,
        "sharpe_xs": rep.sharpe_xs,
        "sharpe_trend": rep.sharpe_trend,
        "max_dd": rep.max_dd,
        "ann_vol": rep.annual_vol,
        "dsr": rep.dsr,
        "pbo": rep.pbo,
        "boot_lo": rep.boot_lo,
        "boot_hi": rep.boot_hi,
        "min_trl": rep.min_trl,
        "corr_xs_trend": rep.corr_xs_trend,
        "realized_idm": rep.realized_idm,
        "div_mult": rep.diversification_mult,
        "gate": "PASS" if combine_gate_verdict(rep) else "FAIL",
    }


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

    _print_df(
        "Gate — trend×XS combine (universe vs majors @2bps)",
        pd.DataFrame(
            [
                build_combine_report_row(conn, "universe @2bps", universe, 2.0),
                build_combine_report_row(conn, "majors @2bps", majors, 2.0),
            ]
        ),
    )

    _print_df(
        "Weights sensitivity (universe @2bps)",
        pd.DataFrame(
            [
                build_combine_report_row(
                    conn, "equal 0.5/0.5", universe, 2.0, 0.5, 0.5
                ),
                build_combine_report_row(
                    conn, "xs-heavy 0.7/0.3", universe, 2.0, 0.7, 0.3
                ),
                build_combine_report_row(
                    conn, "xs-heavy 0.79/0.21", universe, 2.0, 0.79, 0.21
                ),
            ]
        ),
    )

    _print_df(
        "IDM-mode sensitivity (universe @2bps)",
        pd.DataFrame(
            [
                build_combine_report_row(
                    conn, "causal", universe, 2.0, idm_mode="causal"
                ),
                build_combine_report_row(
                    conn, "static", universe, 2.0, idm_mode="static"
                ),
            ]
        ),
    )

    _print_df(
        "Cost sensitivity (universe)",
        pd.DataFrame(
            [
                build_combine_report_row(conn, f"universe @{b:g}bps", universe, b)
                for b in (0.0, 2.0, 8.0, 16.0)
            ]
        ),
    )

    print(
        "\nCombine read: does the combined book BEAT the best single sleeve "
        "(sharpe vs sharpe_xs) AND clear the gate (dsr≥0.95 ∧ pbo≤0.5 ∧ boot_lo>0)? "
        "Read div_mult (>1 = real diversification) + realized_idm + the cost sweep "
        "(if the combined Sharpe decays much faster with cost than each sleeve alone, "
        "double-counted turnover is binding → forecast-space netting is the next lever)."
    )


if __name__ == "__main__":
    main()

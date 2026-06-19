"""Carry sleeve audit (P3) — read-only verdict.

Replays the funding-carry book across the N3 universe (1d) and prints, each with
DSR/PBO/bootstrap-CI/MinTRL stamps where applicable:

1. Headline cross-sectional carry gate (per-span + combined family) + corr_to_xs /
   corr_to_trend (the diversification read against the XS deploy core).
2. Absolute-vs-cross-sectional contrast (Sharpe each).
3. Breadth contrast (universe vs majors-only).
4. Cost sensitivity (0 / 2 / 8 / 16 bps per leg).
5. Per-span Sharpe (which smoothing horizon carries the edge).
6. Scalar sensitivity (robustness to the un-fit carry scalar).

Read-only — no writes, no schema changes.

Usage::

    PYTHONPATH=. poetry run python tools/carry_audit.py
    PYTHONPATH=. poetry run python tools/carry_audit.py --majors BTCUSDT,ETHUSDT,SOLUSDT
"""

from __future__ import annotations

import argparse
import dataclasses
import math
from pathlib import Path

import duckdb
import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.carry import (
    CarryConfig,
    carry_gate_verdict,
    evaluate_carry,
    replay_carry,
    replay_carry_trials,
)
from analytics.forecast import ForecastConfig, replay_universe
from analytics.store import DEFAULT_DB_PATH
from analytics.universe import load_universe
from analytics.xsmom import replay_xs

_MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def _cfg(
    slippage_bps: float, *, cross_sectional: bool, scalar: float = 30.0
) -> CarryConfig:
    sleeve = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    return CarryConfig(
        sleeve_cfg=sleeve, cross_sectional=cross_sectional, carry_scalar=scalar
    )


def _sharpe(r: npt.NDArray[np.float64], ann: float) -> float:
    sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
    return (float(np.mean(r)) / sd * ann) if sd > 1e-12 else 0.0


def build_report_row(
    conn: duckdb.DuckDBPyConnection,
    label: str,
    symbols: list[str],
    slippage_bps: float,
    *,
    cross_sectional: bool = True,
    scalar: float = 30.0,
) -> dict[str, object]:
    cfg = _cfg(slippage_bps, cross_sectional=cross_sectional, scalar=scalar)
    result = replay_carry(conn, cfg, symbols=symbols)
    trials = replay_carry_trials(conn, cfg, symbols=symbols)
    xs = replay_xs(conn, cfg.sleeve_cfg, symbols=symbols).portfolio_return
    trend = replay_universe(conn, cfg.sleeve_cfg, symbols=symbols).portfolio_return
    rep = evaluate_carry(
        result, cfg, trial_returns=trials, xs_returns=xs, trend_returns=trend
    )
    return {
        "label": label,
        "n_inst": len(result.per_instrument_net),
        "days": rep.n_obs,
        "sharpe": rep.sharpe_annual,
        "max_dd": rep.max_dd,
        "ann_ret": rep.annual_return,
        "ann_vol": rep.annual_vol,
        "dsr": rep.dsr,
        "pbo": rep.pbo,
        "boot_lo": rep.boot_lo,
        "boot_hi": rep.boot_hi,
        "min_trl": rep.min_trl,
        "corr_to_xs": rep.corr_to_xs,
        "xs_sharpe": rep.xs_sharpe,
        "corr_to_trend": rep.corr_to_trend,
        "gate": "CLEAR" if carry_gate_verdict(rep) else "FAIL",
    }


def _per_span_sharpe(
    conn: duckdb.DuckDBPyConnection, symbols: list[str], cross_sectional: bool
) -> pd.DataFrame:
    cfg = _cfg(2.0, cross_sectional=cross_sectional)
    ann = math.sqrt(cfg.annualization_days)
    trials = replay_carry_trials(conn, cfg, symbols=symbols)
    rows = [{"trial": name, "sharpe": _sharpe(r, ann)} for name, r in trials.items()]
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
    parser.add_argument("--majors", type=str, default=",".join(_MAJORS))
    args = parser.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")
    universe = load_universe()
    majors = [s.strip().upper() for s in args.majors.split(",") if s.strip()]

    _print_df(
        "Gate — headline cross-sectional carry (universe @2bps)",
        pd.DataFrame(
            [build_report_row(conn, "xs-carry universe @2bps", universe, 2.0)]
        ),
    )

    _print_df(
        "Absolute vs cross-sectional contrast (universe @2bps)",
        pd.DataFrame(
            [
                build_report_row(
                    conn, "cross-sectional", universe, 2.0, cross_sectional=True
                ),
                build_report_row(
                    conn, "absolute", universe, 2.0, cross_sectional=False
                ),
            ]
        ),
    )

    _print_df(
        "Breadth contrast (cross-sectional @2bps)",
        pd.DataFrame(
            [
                build_report_row(conn, "universe", universe, 2.0),
                build_report_row(conn, "majors", majors, 2.0),
            ]
        ),
    )

    _print_df(
        "Cost sensitivity (cross-sectional universe)",
        pd.DataFrame(
            [
                build_report_row(conn, f"universe @{b:g}bps", universe, b)
                for b in (0.0, 2.0, 8.0, 16.0)
            ]
        ),
    )

    _print_df(
        "Per-span carry Sharpe (cross-sectional universe)",
        _per_span_sharpe(conn, universe, True),
    )

    _print_df(
        "Scalar sensitivity (cross-sectional universe @2bps)",
        pd.DataFrame(
            [
                build_report_row(conn, f"scalar={s:g}", universe, 2.0, scalar=s)
                for s in (15.0, 30.0, 60.0)
            ]
        ),
    )

    print(
        "\nCarry read: is the cross-sectional carry sleeve positive, cost-robust, "
        "DSR/PBO-survivable, AND low/negative-correlated to the XS deploy core "
        "(corr_to_xs near 0 or below)? A comparable-Sharpe carry sleeve uncorrelated "
        "with XS is the second edge the combine layer needs. Read corr_to_xs + boot_lo "
        "+ pbo + dsr alongside the headline before calling it."
    )


if __name__ == "__main__":
    main()

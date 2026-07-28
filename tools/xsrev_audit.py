"""Cross-sectional reversal sleeve audit (P3) — read-only verdict.

Replays the sign-flipped short-horizon reversal book across the N3 universe (1d)
and prints: a breadth contrast (universe vs majors), a cost-sensitivity sweep, the
per-window (k) Sharpes, the k=1 bid-ask-bounce diagnostic, a scalar-sensitivity
table, and the correlation to the XS-momentum deploy core — each with
DSR/PBO/bootstrap-CI/MinTRL stamps. Read-only; no writes, no schema changes.

Usage::

    PYTHONPATH=. poetry run python tools/xsrev_audit.py
    PYTHONPATH=. poetry run python tools/xsrev_audit.py --majors BTCUSDT,ETHUSDT,SOLUSDT
"""

from __future__ import annotations

import argparse
import dataclasses
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs
from analytics.store import DEFAULT_DB_PATH
from analytics.universe import load_universe
from analytics.xsmom import evaluate_xs, replay_xs
from analytics.xsmom.book import run_xs_backtest
from analytics.xsrev import ReversalConfig, replay_xsrev, replay_xsrev_trials
from analytics.xsrev.forecast import crowding_forecast_matrix
from analytics.xsrev.replay import load_daily_open_interest
from portfolio import metrics

_MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def _rcfg(slippage_bps: float, scalar: float = 10.0) -> ReversalConfig:
    sleeve = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    return ReversalConfig(sleeve_cfg=sleeve, reversal_scalar=scalar)


def build_xsrev_report_row(
    conn: duckdb.DuckDBPyConnection,
    label: str,
    symbols: list[str],
    slippage_bps: float,
) -> dict[str, object]:
    rcfg = _rcfg(slippage_bps)
    result = replay_xsrev(conn, rcfg, symbols=symbols)
    trials = replay_xsrev_trials(conn, rcfg, symbols=symbols)
    xs_cfg = dataclasses.replace(ForecastConfig(), slippage_pct=slippage_bps / 10_000.0)
    xs_ret = replay_xs(conn, xs_cfg, symbols=symbols).portfolio_return
    rep = evaluate_xs(
        result, rcfg.sleeve_cfg, trial_returns=trials, trend_returns=xs_ret
    )
    return {
        "label": label,
        "n_inst": len(result.per_instrument_net),
        "days": rep.n_obs,
        "sharpe": rep.sharpe_annual,
        "max_dd": rep.max_dd,
        "ann_ret": rep.annual_return,
        "dsr": rep.dsr,
        "pbo": rep.pbo,
        "boot_lo": rep.boot_lo,
        "boot_hi": rep.boot_hi,
        "min_trl": rep.min_trl,
        "corr_to_xs": rep.corr_to_trend,
        "xs_sharpe": rep.trend_sharpe,
        "gate": bool(rep.dsr >= 0.95 and rep.pbo <= 0.5 and rep.boot_lo > 0.0),
    }


def _per_window_sharpes(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.DataFrame:
    cfg = ReversalConfig()
    ann = math.sqrt(cfg.sleeve_cfg.annualization_days)
    trials = replay_xsrev_trials(conn, cfg, symbols=symbols)
    rows = []
    for name, r in trials.items():
        sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
        sr = (float(np.mean(r)) / sd * ann) if sd > 1e-12 else 0.0
        rows.append({"trial": name, "sharpe": sr})
    return pd.DataFrame(rows)


def _k1_diagnostic_row(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> dict[str, object]:
    # k=1 is excluded from the headline family (bid-ask bounce). Report it alone
    # so a "k=1-only, dies with cost" pattern is visible = microstructure, not alpha.
    cfg = dataclasses.replace(ReversalConfig(), formation_windows=(1,))
    result = replay_xsrev(conn, cfg, symbols=symbols)
    trials = replay_xsrev_trials(conn, cfg, symbols=symbols)
    xs_ret = replay_xs(conn, ForecastConfig(), symbols=symbols).portfolio_return
    rep = evaluate_xs(
        result, cfg.sleeve_cfg, trial_returns=trials, trend_returns=xs_ret
    )
    return {"label": "k=1 (diagnostic)", "sharpe": rep.sharpe_annual, "dsr": rep.dsr}


def _scalar_sensitivity(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.DataFrame:
    rows = []
    for s in (5.0, 10.0, 20.0):
        rcfg = _rcfg(2.0, scalar=s)
        sr = replay_xsrev(conn, rcfg, symbols=symbols)
        curve = (1.0 + pd.Series(sr.portfolio_return)).cumprod()
        rows.append({"scalar": s, "sharpe": metrics.sharpe(curve)})
    return pd.DataFrame(rows)


def _oi_crowding_panel(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> pd.DataFrame:
    """DESCRIPTIVE-ONLY OI-positioning read over the shallow OI overlap window."""
    closes, fundings = load_daily_inputs(conn, symbols)
    ois = load_daily_open_interest(conn, symbols)
    have = [s for s in closes if s in ois]
    if len(have) < 2:
        return pd.DataFrame()
    closes = {s: closes[s] for s in have}
    fundings = {s: fundings[s] for s in have}
    cfg = ReversalConfig()
    forecasts = crowding_forecast_matrix(closes, fundings, ois, cfg)
    res = run_xs_backtest(closes, fundings, cfg.sleeve_cfg, forecasts=forecasts)
    curve = (1.0 + pd.Series(res.portfolio_return)).cumprod()
    return pd.DataFrame(
        [
            {
                "panel": "OI crowding (DESCRIPTIVE)",
                "n_inst": len(have),
                "days": len(res.portfolio_return),
                "sharpe": metrics.sharpe(curve),
            }
        ]
    )


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
        "--majors", type=str, default=",".join(_MAJORS), help="majors-only set"
    )
    args = parser.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")
    universe = load_universe()
    majors = [s.strip().upper() for s in args.majors.split(",") if s.strip()]

    _print_df(
        "Reversal breadth contrast",
        pd.DataFrame(
            [
                build_xsrev_report_row(conn, "universe @2bps", universe, 2.0),
                build_xsrev_report_row(conn, "majors @2bps", majors, 2.0),
            ]
        ),
    )
    _print_df(
        "Cost sensitivity (universe)",
        pd.DataFrame(
            [
                build_xsrev_report_row(conn, f"universe @{b:g}bps", universe, b)
                for b in (0.0, 2.0, 8.0, 16.0)
            ]
        ),
    )
    _print_df("Per-window (k) Sharpe", _per_window_sharpes(conn, universe))
    _print_df(
        "k=1 bounce diagnostic", pd.DataFrame([_k1_diagnostic_row(conn, universe)])
    )
    _print_df(
        "Scalar sensitivity (universe @2bps)", _scalar_sensitivity(conn, universe)
    )

    oi = _oi_crowding_panel(conn, universe)
    _print_df("OI-positioning crowding — DESCRIPTIVE ONLY (underpowered)", oi)
    print(
        "\n[OI panel is DESCRIPTIVE ONLY] open_interest is ~144d (majors) / 30-60d "
        "(rest) — far short of MinTRL. No BUILD/SHELF verdict; a rigorous OI arm "
        "needs a deeper OI backfill (CoinGlass, not justified pre-gate)."
    )

    print(
        "\nRead: reversal BUILDs only if the universe book clears the gate "
        "(dsr>=0.95, pbo<=0.5, boot_lo>0) AND stays positive at 8bps AND is "
        "additive to XS (corr_to_xs low/negative, not a redundant -XS). A k=1-only "
        "edge that dies with cost is bid-ask bounce, not alpha."
    )


if __name__ == "__main__":
    main()

"""MFE/MAE diagnostic over the live alert ledger (exit spec §2) — diagnose mode.

Answers "is the 40%-expiry leak exit-fixable or entry-broken?" by reporting,
per cohort (win / loss / expired) and per (strategy, tf, direction) cell:
mean/median MFE_R and MAE_R, the share of trades whose MFE reached
>=0.5R / >=1.0R, the median tp_r they were asked to reach, and median bars
held. Read the tables against the 4-pattern verdict grid in
`docs/redesign/2026-06-05-exit-improvement-spec.md` §2.

Read-only — no writes, no schema changes. `--replay` runs the exit-policy A/B
(spec §3–§5): re-resolves the ledger under policy #0 (fixed) vs the composite
and reports portfolio Sharpe / max-DD via the P1 paper book.

Usage::

    PYTHONPATH=. poetry run python tools/exit_audit.py
    PYTHONPATH=. poetry run python tools/exit_audit.py --min-n 20
    PYTHONPATH=. poetry run python tools/exit_audit.py --csv /tmp/excursions.csv
    PYTHONPATH=. poetry run python tools/exit_audit.py --replay
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pandas as pd

from analytics.exits import aggregate_cohorts, compute_excursions
from analytics.exits.audit import run_exit_ab
from analytics.store import DEFAULT_DB_PATH
from portfolio.sizing import SizingConfig


def _print_df(title: str, df: pd.DataFrame) -> None:
    print(f"\n=== {title} ===")
    if df.empty:
        print("(no rows)")
        return
    print(df.to_string(index=False, float_format=lambda x: f"{x:+.3f}"))


def _run_replay(con: duckdb.DuckDBPyConnection) -> None:
    rows = run_exit_ab(con, SizingConfig())
    df = pd.DataFrame(
        {
            "policy": r.name,
            "n_sized": r.n_sized,
            "n_skip": r.n_skipped,
            "sharpe": r.sharpe,
            "sortino": r.sortino,
            "max_dd": r.max_dd,
            "expiry": r.expiry_rate,
            "win": r.win_rate,
            "hold_bars": r.avg_hold_bars,
            "avg_r": r.avg_r,
        }
        for r in rows
    )
    _print_df("Exit-policy A/B (portfolio via P1 paper book; gross of costs)", df)
    print(
        "\nHeadline = portfolio Sharpe (fixed basis). expiry/win/avg_r are over the "
        "FULL re-resolved population; the portfolio reflects only the cap-admitted "
        "subset. Composite 'expired' includes the deliberate time-stop exits."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    parser.add_argument(
        "--replay",
        action="store_true",
        help="run the exit-policy A/B (fixed vs composite) instead of the diagnostic",
    )
    parser.add_argument(
        "--min-n",
        type=int,
        default=30,
        help="hide cohort×cell rows with fewer than this many trades",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="optional path to dump the per-alert excursion rows",
    )
    args = parser.parse_args()

    con = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")

    if args.replay:
        _run_replay(con)
        return

    excursions = compute_excursions(con)
    resolved_row = con.execute(
        "SELECT count(*) FROM signal_alert_outcomes "
        "WHERE outcome IN ('win', 'loss', 'expired')"
    ).fetchone()
    resolved = int(resolved_row[0]) if resolved_row else 0
    print(
        f"Coverage: {len(excursions)} of {resolved} resolved alerts scored "
        f"({resolved - len(excursions)} skipped: zero-risk or missing OHLCV)"
    )
    if excursions.empty:
        print("(nothing to report)")
        return

    _print_df(
        "Cohort roll-up (all cells)", aggregate_cohorts(excursions, by=(), min_n=1)
    )
    _print_df(
        f"Cohort × (strategy, tf, direction) — min_n={args.min_n}",
        aggregate_cohorts(excursions, min_n=args.min_n),
    )
    print(
        "\nVerdict grid: docs/redesign/2026-06-05-exit-improvement-spec.md §2 — "
        "expired reach_10 high + tp_r_p50 higher => lower tp / partials; "
        "expired reach_05 low => entry problem, don't tune exits; "
        "loss mfe high => breakeven/trail candidate."
    )

    if args.csv is not None:
        excursions.to_csv(args.csv, index=False)
        print(f"\nPer-alert excursions written to {args.csv}")


if __name__ == "__main__":
    main()

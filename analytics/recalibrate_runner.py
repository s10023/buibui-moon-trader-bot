"""Recalibration runner — thin wrapper: opens DB, calls lib, prints report.

No business logic here. All logic lives in recalibrate_lib.py.
"""

import argparse
from pathlib import Path

import duckdb

from analytics.data_store import DEFAULT_DB_PATH, init_schema
from analytics.indicators_lib import STRATEGY_REGISTRY, patch_confidence_scores
from analytics.recalibrate_lib import (
    compute_recalibrated_ratings,
    format_recalibration_report,
    get_backtest_win_rates,
)


def run(args: argparse.Namespace, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Open DB, compute recalibrated ratings, print report.

    --dry-run (default): show what would change without modifying anything.
    --apply: apply the new ratings to STRATEGY_REGISTRY in-memory and confirm.
    """
    apply: bool = getattr(args, "apply", False)
    min_trades: int = getattr(args, "min_trades", 10)

    conn: duckdb.DuckDBPyConnection = duckdb.connect(str(db_path), read_only=True)
    try:
        init_schema(conn)
        win_rates = get_backtest_win_rates(conn)
        new_ratings = compute_recalibrated_ratings(conn, min_trades=min_trades)
    finally:
        conn.close()

    old_ratings = {name: spec.confidence for name, spec in STRATEGY_REGISTRY.items()}
    report = format_recalibration_report(old_ratings, new_ratings, win_rates)
    print(report)

    if apply:
        if not new_ratings:
            print("\n  Nothing to apply — no strategies had sufficient backtest data.")
            return
        patch_confidence_scores(new_ratings)
        print(
            f"\n  Applied {len(new_ratings)} confidence update(s) to STRATEGY_REGISTRY."
        )
        for name, stars in sorted(new_ratings.items()):
            old = old_ratings.get(name, 0)
            if stars != old:
                print(f"    {name}: {old}★ → {stars}★")
    else:
        print(
            "\n  Dry-run mode — no changes applied. Use --apply to patch STRATEGY_REGISTRY."
        )

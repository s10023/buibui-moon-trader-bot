"""Recalibration runner — thin wrapper: opens DB, calls lib, prints report.

No business logic here. All logic lives in recalibrate_lib.py.
"""

import argparse
from pathlib import Path

import duckdb

from analytics.data_store import DEFAULT_DB_PATH, init_schema
from analytics.indicators_lib import STRATEGY_REGISTRY
from analytics.recalibrate_lib import (
    compute_recalibrated_ratings,
    format_recalibration_report,
    get_backtest_win_rates,
    write_confidence_to_source,
)

_INDICATORS_LIB = Path(__file__).parent / "indicators_lib.py"


def run(
    args: argparse.Namespace,
    db_path: Path = DEFAULT_DB_PATH,
    source_path: Path = _INDICATORS_LIB,
) -> None:
    """Open DB, compute recalibrated ratings, print report.

    --dry-run (default): show what would change without modifying anything.
    --apply: patch confidence=N values directly in indicators_lib.py so that
             signal watch and all other consumers see the updated ratings on
             next startup — no in-memory-only patch.
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
        patched = write_confidence_to_source(new_ratings, source_path)
        changed = [n for n in patched if new_ratings[n] != old_ratings.get(n, 0)]
        print(
            f"\n  Patched {len(patched)} strategy/ies in {source_path.name}"
            f" ({len(changed)} changed)."
        )
        for name in sorted(changed):
            old = old_ratings.get(name, 0)
            print(f"    {name}: {old}★ → {new_ratings[name]}★")
        if patched:
            print("\n  Restart signal watch to pick up the new ratings.")
    else:
        print(
            "\n  Dry-run mode — no changes applied. Use --apply to write ratings to indicators_lib.py."
        )

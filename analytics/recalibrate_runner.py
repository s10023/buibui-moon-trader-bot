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
    write_confidence_to_db,
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
    --apply with --config: write ratings to confidence_ratings DB table keyed by config name.
    --apply without --config: legacy — patch confidence=N values directly in indicators_lib.py.
    """
    apply: bool = getattr(args, "apply", False)
    min_trades: int = getattr(args, "min_trades", 10)
    config_path: str | None = getattr(args, "config", None)
    day_filter: str | None = getattr(args, "day_filter", None)
    config_name: str | None = None

    # Derive day_filter and config_name from the TOML when --config is provided.
    if config_path:
        from analytics.signal_config import load_signal_config

        watch_cfg = load_signal_config(config_path)
        day_filter = watch_cfg.day_filter
        config_name = Path(config_path).stem

    conn: duckdb.DuckDBPyConnection = duckdb.connect(str(db_path))
    try:
        init_schema(conn)
        win_rates = get_backtest_win_rates(conn, day_filter=day_filter)
        new_ratings = compute_recalibrated_ratings(
            conn, min_trades=min_trades, day_filter=day_filter
        )

        old_ratings = {
            name: spec.confidence for name, spec in STRATEGY_REGISTRY.items()
        }
        report = format_recalibration_report(old_ratings, new_ratings, win_rates)
        print(report)

        if apply:
            if not new_ratings:
                print(
                    "\n  Nothing to apply — no strategies had sufficient backtest data."
                )
                return
            if config_name:
                write_confidence_to_db(conn, config_name, new_ratings, win_rates)
                print(
                    f"\n  Written to confidence_ratings table for config '{config_name}'."
                )
                for name in sorted(new_ratings):
                    print(f"    {name}: {new_ratings[name]}")
                print("\n  Restart signal watch to pick up the new ratings.")
            else:
                # Legacy: patch indicators_lib.py source directly.
                patched = write_confidence_to_source(new_ratings, source_path)
                print(f"\n  Patched {len(patched)} strategy/ies in {source_path.name}.")
                for name in sorted(patched):
                    print(f"    {name}: {new_ratings[name]}")
                if patched:
                    print("\n  Restart signal watch to pick up the new ratings.")
        else:
            if config_name:
                print(
                    f"\n  Dry-run mode — use --apply to write to DB for config '{config_name}'."
                )
            else:
                print(
                    "\n  Dry-run mode — no changes applied."
                    " Use --apply to write ratings to indicators_lib.py."
                    " Pass --config to write per-config ratings to DB instead."
                )
    finally:
        conn.close()

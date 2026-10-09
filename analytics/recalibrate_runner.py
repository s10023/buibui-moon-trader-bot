"""Recalibration runner — thin wrapper: opens DB, calls lib, prints report.

No business logic here. All logic lives in recalibrate_lib.py.
"""

import argparse
from collections.abc import Callable
from pathlib import Path

import duckdb

from analytics.data_store import DEFAULT_DB_PATH, init_schema
from analytics.db_retry import connect_with_retry
from analytics.eras import EraBoundary, load_boundaries, straddle_report
from analytics.recalibrate_lib import (
    PruneThresholdExceeded,
    UnratedPruneThresholdExceeded,
    compute_directional_ratings,
    compute_dsr_ratings,
    compute_recalibrated_ratings,
    format_recalibration_report,
    get_backtest_win_rates,
    prune_stale_ratings,
    prune_undeclared_ratings,
    prune_unrated_ratings,
    sample_run_times,
    write_confidence_to_db,
    write_confidence_to_source,
)
from analytics.signal_config import SignalWatchConfig
from analytics.strategies import STRATEGY_REGISTRY

_REGISTRY_PATH = Path(__file__).parent / "strategies" / "_registry.py"

BoundaryLoader = Callable[[], list[EraBoundary]]


def _backtest_boundaries() -> list[EraBoundary]:
    return load_boundaries(scopes=("backtest",))


def era_check_lines(
    conn: duckdb.DuckDBPyConnection,
    day_filter: str | None,
    adr_suppress_threshold: float | None,
    loader: BoundaryLoader = _backtest_boundaries,
) -> list[str]:
    """Era check for the run pool the stars above were computed from.

    Keyed on ``run_at_ms`` through :func:`sample_run_times`, the same pool and the
    same key the decay review uses, never ``entry_time`` (see ``analytics.eras``).

    A loader failure prints NOT RUN instead of raising. The decay review fails
    loudly because it is a read-only audit; recalibrate sits inside ``make
    db-update``, and a git hiccup there would block the ratings refresh over a
    line that writes nothing. NOT RUN is still never an empty answer, so a
    failure cannot read as a CLEAN sample.
    """
    try:
        boundaries = loader()
    except (OSError, RuntimeError, ValueError) as exc:
        return [
            f"  era check: NOT RUN — boundaries failed to load ({exc}); this "
            "report makes no claim about whether the pool spans a rule change."
        ]
    return straddle_report(
        boundaries, sample_run_times(conn, day_filter, adr_suppress_threshold)
    )


def run(
    args: argparse.Namespace,
    db_path: Path = DEFAULT_DB_PATH,
    source_path: Path = _REGISTRY_PATH,
    boundary_loader: BoundaryLoader = _backtest_boundaries,
) -> None:
    """Open DB, compute recalibrated ratings, print report.

    --dry-run (default): show what would change without modifying anything.
    --apply with --config: write ratings to confidence_ratings DB table keyed by config name.
    --apply without --config: legacy — patch confidence=N values directly in analytics/strategies/_registry.py.
    """
    apply: bool = getattr(args, "apply", False)
    min_trades: int = getattr(args, "min_trades", 10)
    config_path: str | None = getattr(args, "config", None)
    day_filter: str | None = getattr(args, "day_filter", None)
    config_name: str | None = None
    watch_cfg: SignalWatchConfig | None = None

    adr_suppress_threshold: float | None = None

    if config_path:
        from analytics.signal_config import load_signal_config

        watch_cfg = load_signal_config(config_path)
        day_filter = watch_cfg.day_filter
        config_name = Path(config_path).stem
        adr_suppress_threshold = watch_cfg.bias.adr_suppress_threshold

    conn: duckdb.DuckDBPyConnection = connect_with_retry(db_path)
    try:
        init_schema(conn)
        win_rates = get_backtest_win_rates(
            conn, day_filter=day_filter, adr_suppress_threshold=adr_suppress_threshold
        )
        new_ratings = compute_recalibrated_ratings(
            conn,
            min_trades=min_trades,
            day_filter=day_filter,
            adr_suppress_threshold=adr_suppress_threshold,
        )
        dir_ratings = compute_directional_ratings(
            conn,
            min_trades=max(min_trades // 2, 2),
            day_filter=day_filter,
            adr_suppress_threshold=adr_suppress_threshold,
        )
        dsr_ratings = compute_dsr_ratings(
            conn,
            day_filter=day_filter,
            adr_suppress_threshold=adr_suppress_threshold,
        )

        old_ratings = {
            name: spec.confidence for name, spec in STRATEGY_REGISTRY.items()
        }
        report = format_recalibration_report(
            old_ratings,
            new_ratings,
            win_rates,
            directional_ratings=dir_ratings,
            dsr_ratings=dsr_ratings,
        )
        print(report)
        print(
            "\n  Era check — the stars above pool runs saved under these rule changes:"
        )
        print(
            "\n".join(
                era_check_lines(
                    conn, day_filter, adr_suppress_threshold, boundary_loader
                )
            )
        )

        if apply:
            if not new_ratings:
                print(
                    "\n  Nothing to apply — no strategies had sufficient backtest data."
                )
                return
            if config_name:
                write_confidence_to_db(
                    conn,
                    config_name,
                    new_ratings,
                    win_rates,
                    day_filter=day_filter,
                    directional_ratings=dir_ratings,
                    dsr_ratings=dsr_ratings,
                )
                if day_filter is not None:
                    n_stale = prune_stale_ratings(conn, config_name, day_filter)
                    if n_stale:
                        print(
                            f"  Pruned {n_stale} stale rating row(s) "
                            f"with mismatched day_filter."
                        )
                if watch_cfg is not None:
                    # Ratings for cells this config no longer declares. Nothing
                    # else removes them: the upsert only inserts-or-replaces, so
                    # a dropped cell keeps its stars and collects a fresh
                    # timestamp on a stale value at every refresh. Inert at
                    # runtime (both read sites are keyed lookups) but it
                    # corrupts any population counted off confidence_ratings.
                    try:
                        n_undeclared = prune_undeclared_ratings(
                            conn, config_name, watch_cfg
                        )
                    except PruneThresholdExceeded as exc:
                        print(f"\n  ⚠ SKIPPED undeclared-rating prune: {exc}")
                    else:
                        if n_undeclared:
                            print(
                                f"  Pruned {n_undeclared} rating row(s) for cells "
                                f"'{config_name}' no longer declares."
                            )
                # Ratings this pass produced no value for. Omission from
                # compute_*_ratings (a cell under min_trades) is an upsert with
                # no delete counterpart, so a cell that stays DECLARED while
                # falling below the floor keeps its last stars forever — 24 of
                # 288 rows measured frozen 2026-08-20, oldest 2026-04-02.
                # Live-reachable, not cosmetic: the conflict resolver drops the
                # lower-confidence side on a two-direction co-fire, so a frozen
                # star can silence the correct direction. Runs LAST of the three
                # pruners, so the stale and undeclared rows are attributed to
                # their own pruner rather than inflating this one's share.
                try:
                    n_unrated = prune_unrated_ratings(
                        conn, config_name, new_ratings, dir_ratings
                    )
                except UnratedPruneThresholdExceeded as exc:
                    print(f"\n  ⚠ SKIPPED unrated-rating prune: {exc}")
                else:
                    if n_unrated:
                        print(
                            f"  Pruned {n_unrated} rating row(s) for cells "
                            f"'{config_name}' no longer rated by this pass."
                        )
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
                    " Use --apply to write ratings to analytics/strategies/_registry.py."
                    " Pass --config to write per-config ratings to DB instead."
                )
    finally:
        conn.close()

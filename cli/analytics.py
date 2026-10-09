"""Buibui CLI — `analytics` subcommand (backfill, sync, oi-archive)."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from analytics import analytics_runner
from analytics.store import DEFAULT_DB_PATH
from analytics.universe import load_universe
from cli._common import parse_since_to_ms


def _resolve_symbol_args(args: argparse.Namespace) -> list[str] | None:
    """--universe → symbols from config/universe.toml; else passthrough --symbols."""
    if getattr(args, "universe", False):
        return load_universe()
    symbols: list[str] | None = args.symbols
    return symbols


def run_analytics_backfill(args: argparse.Namespace) -> None:
    analytics_runner.run_backfill(
        symbols=_resolve_symbol_args(args),
        timeframes=args.timeframes,
        since_ms=parse_since_to_ms(args.since),
    )


def run_analytics_sync(args: argparse.Namespace) -> None:
    analytics_runner.run_sync(
        symbols=_resolve_symbol_args(args),
        timeframes=args.timeframes,
    )


def run_analytics_oi_archive(args: argparse.Namespace) -> None:
    from analytics import oi_archive

    symbols = load_universe() if args.universe else list(args.symbols)
    until = date.fromisoformat(args.until) if args.until else None
    rc = oi_archive.run_oi_archive(
        symbols,
        db_path=Path(args.db),
        since=date.fromisoformat(args.since),
        until=until,
        cache_dir=None if args.no_cache else Path(args.cache_dir),
        workers=args.workers,
        keep_5m=args.with_5m,
        force=args.force,
        retry_missing=args.retry_missing,
        report_only=args.report_only,
    )
    if rc:
        sys.exit(rc)


def add_analytics_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    analytics_parser = subparsers.add_parser("analytics", help="Analytics data tools")
    analytics_subparsers = analytics_parser.add_subparsers(
        dest="analytics_command", required=True
    )

    # 'backfill' subcommand
    backfill_parser = analytics_subparsers.add_parser(
        "backfill", help="Full history backfill from Binance"
    )
    backfill_group = backfill_parser.add_mutually_exclusive_group()
    backfill_group.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Symbols to backfill (default: all from coins.json)",
    )
    backfill_group.add_argument(
        "--universe",
        action="store_true",
        help="Use the research universe from config/universe.toml",
    )
    backfill_parser.add_argument(
        "--timeframes",
        nargs="+",
        default=["1h", "4h"],
        help="Timeframes to backfill (default: 1h 4h)",
    )
    backfill_parser.add_argument(
        "--since",
        default="2023-01-01",
        help="Start date in YYYY-MM-DD format (default: 2023-01-01)",
    )
    backfill_parser.set_defaults(func=run_analytics_backfill)

    # 'sync' subcommand
    sync_parser = analytics_subparsers.add_parser(
        "sync", help="Incremental sync since last stored candle"
    )
    sync_group = sync_parser.add_mutually_exclusive_group()
    sync_group.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Symbols to sync (default: all from coins.json)",
    )
    sync_group.add_argument(
        "--universe",
        action="store_true",
        help="Use the research universe from config/universe.toml",
    )
    sync_parser.add_argument(
        "--timeframes",
        nargs="+",
        default=["1h", "4h"],
        help="Timeframes to sync (default: 1h 4h)",
    )
    sync_parser.set_defaults(func=run_analytics_sync)

    # 'oi-archive' subcommand (#936)
    oi_parser = analytics_subparsers.add_parser(
        "oi-archive",
        help="Backfill open interest from the data.binance.vision metrics archive",
    )
    oi_group = oi_parser.add_mutually_exclusive_group(required=True)
    oi_group.add_argument("--symbols", nargs="+", help="Symbols to load")
    oi_group.add_argument(
        "--universe",
        action="store_true",
        help="Use the research universe from config/universe.toml",
    )
    oi_parser.add_argument(
        "--since",
        default="2020-09-01",
        help="First day, YYYY-MM-DD (default: 2020-09-01, BTCUSDT's first file)",
    )
    oi_parser.add_argument(
        "--until", default=None, help="Last day, YYYY-MM-DD (default: yesterday UTC)"
    )
    oi_parser.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), help="DuckDB file (default: analytics.db)"
    )
    oi_parser.add_argument(
        "--cache-dir",
        default=".cache/oi-archive",
        help="Zip cache directory (default: .cache/oi-archive, gitignored)",
    )
    oi_parser.add_argument("--no-cache", action="store_true", help="Do not cache zips")
    oi_parser.add_argument(
        "--workers", type=int, default=4, help="Concurrent downloads (default: 4)"
    )
    oi_parser.add_argument(
        "--with-5m",
        action="store_true",
        help="Also store the raw 5-minute rows (+~340 MB for the 25-symbol universe)",
    )
    oi_parser.add_argument(
        "--force", action="store_true", help="Reload days the ledger already has"
    )
    oi_parser.add_argument(
        "--retry-missing",
        action="store_true",
        help="Re-request old missing days (recent ones are always retried)",
    )
    oi_parser.add_argument(
        "--report-only", action="store_true", help="Print coverage, download nothing"
    )
    oi_parser.set_defaults(func=run_analytics_oi_archive)

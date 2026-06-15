"""Buibui CLI — `portfolio replay` subcommand (read-only paper replay)."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import duckdb

from analytics.data_store import DEFAULT_DB_PATH
from portfolio.replay import replay_ledger
from portfolio.report import format_report
from portfolio.sizing import SizingConfig


def run_portfolio_replay(args: argparse.Namespace) -> None:
    cfg = SizingConfig.from_toml(args.config) if args.config else SizingConfig()
    if args.capital is not None:
        cfg = replace(cfg, capital=float(args.capital))
    if args.vol_target is not None:
        cfg = replace(cfg, vol_target_annual=float(args.vol_target))
    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        res = replay_ledger(conn, cfg)
        print(format_report(res, cfg))
    finally:
        conn.close()


def add_portfolio_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    p = subparsers.add_parser(
        "portfolio", help="Paper-portfolio replay of the live outcome ledger"
    )
    sub = p.add_subparsers(dest="portfolio_command", required=True)
    replay_p = sub.add_parser(
        "replay", help="Replay signal_alert_outcomes into a sized book"
    )
    replay_p.add_argument(
        "--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path"
    )
    replay_p.add_argument(
        "--config",
        type=str,
        default=None,
        help="TOML with a [portfolio] block (optional)",
    )
    replay_p.add_argument(
        "--capital", type=float, default=None, help="paper capital override"
    )
    replay_p.add_argument(
        "--vol-target",
        type=float,
        default=None,
        dest="vol_target",
        help="annual vol target override (e.g. 0.20)",
    )
    replay_p.set_defaults(func=run_portfolio_replay)

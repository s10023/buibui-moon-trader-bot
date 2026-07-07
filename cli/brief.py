"""Buibui CLI — `brief` subcommand (daily market brief, read-only)."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import duckdb

from analytics.brief._common import parse_as_of_ms
from analytics.brief.bundle import compute_brief
from analytics.brief.config import BriefConfig, default_symbols
from analytics.brief.render import render_markdown
from analytics.brief.types import bundle_to_dict
from analytics.data_store import DEFAULT_DB_PATH


def run_brief_cmd(args: argparse.Namespace) -> None:
    as_of_ms = parse_as_of_ms(args.as_of) if args.as_of else int(time.time() * 1000)
    notes: list[str] = []
    if args.symbols:
        symbols = tuple(args.symbols)
    else:
        symbols, notes = default_symbols()
    cfg = BriefConfig(symbols=symbols, as_of_ms=as_of_ms, stats_days=args.days)
    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        bundle = compute_brief(conn, cfg, extra_notes=notes)
    finally:
        conn.close()
    markdown = render_markdown(bundle)
    print(markdown)
    if args.json:
        Path(args.json).write_text(json.dumps(bundle_to_dict(bundle), indent=2))
    if args.markdown:
        Path(args.markdown).write_text(markdown + "\n")
    if bundle.panels and all(p.error is not None for p in bundle.panels):
        raise SystemExit(1)


def add_brief_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    p = subparsers.add_parser(
        "brief",
        help="Daily market brief: levels, zones, regime, seasonality, pundit board",
    )
    p.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Symbols to panel (default: coins.json keys)",
    )
    p.add_argument("--db", default=str(DEFAULT_DB_PATH), help="DuckDB path")
    p.add_argument(
        "--as-of",
        default=None,
        dest="as_of",
        help="ISO8601 anchor, e.g. 2026-07-04T00:10:00Z (default: now)",
    )
    p.add_argument("--days", type=int, default=180, help="Seasonality window in days")
    p.add_argument(
        "--json", default=None, help="Also write the bundle JSON to this path"
    )
    p.add_argument(
        "--markdown", default=None, help="Also write the markdown to this path"
    )
    p.set_defaults(func=run_brief_cmd)

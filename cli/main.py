"""Buibui CLI entry — assembles argparse tree, dispatches to subcommand handlers."""

from __future__ import annotations

import argparse
import logging

from colorama import just_fix_windows_console
from dotenv import load_dotenv

from cli import (
    analytics,
    backtest,
    brief,
    card,
    card_orders,
    digest,
    monitor,
    param,
    portfolio,
    recalibrate,
    signal,
    web,
)
from utils.stdio import utf8_stdio


def build_parser() -> argparse.ArgumentParser:
    """The full CLI argparse tree, with no side effects.

    Split out of :func:`main` so `tools/sanity_checks.py` can enumerate the
    subcommands from the parser itself rather than from a hand-written list —
    the README's command list is exactly the surface that rots with no signal.
    `main` still owns dotenv/logging, so importing this runs nothing.
    """
    parser = argparse.ArgumentParser(description="Buibui Moon Trader CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    monitor.add_monitor_subparser(subparsers)
    signal.add_signal_subparser(subparsers)
    analytics.add_analytics_subparser(subparsers)
    backtest.add_backtest_subparser(subparsers)
    brief.add_brief_subparser(subparsers)
    card.add_card_subparser(subparsers)
    card_orders.add_card_orders_subparsers(subparsers)
    digest.add_digest_subparser(subparsers)
    param.add_param_sweep_subparser(subparsers)
    param.add_param_audit_subparser(subparsers)
    portfolio.add_portfolio_subparser(subparsers)
    recalibrate.add_recalibrate_subparser(subparsers)
    web.add_web_subparser(subparsers)
    return parser


def main() -> None:
    # Before anything prints: a redirected stdout is cp1252 on Windows, and the
    # sweep's closing `═` table killed a run that had already saved (utils/stdio.py).
    utf8_stdio()
    # Enables ANSI colour on a legacy Windows console for the monitors. Unlike the
    # `colorama.init()` this replaces, it never wraps a redirected stream.
    just_fix_windows_console()
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

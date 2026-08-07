"""Buibui CLI — `web` subcommand (FastAPI server entry)."""

from __future__ import annotations

import argparse
from pathlib import Path


def run_web_server(args: argparse.Namespace) -> None:
    import os
    import sys

    import uvicorn

    from analytics.signal_config import pick_default_config_for_today

    config = getattr(args, "config", None)
    if config is None:
        # Mirror `buibui signal watch`: with no --config the UI would otherwise
        # serve GET /api/active-config as empty, so the operator reads "no
        # config" when the daemon beside it is running a real one. UTC weekday
        # (not local) so the pick matches the day_filter the daemon applies.
        config = str(pick_default_config_for_today())
        print(
            f"📅 No --config provided; auto-selected {Path(config).name} "
            "for today's UTC weekday.",
            file=sys.stderr,
        )
    os.environ["BUIBUI_CONFIG"] = config

    uvicorn.run(
        "web.api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


def add_web_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    web_parser = subparsers.add_parser("web", help="Run FastAPI web backend")
    web_parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)"
    )
    web_parser.add_argument(
        "--port", type=int, default=8000, help="Bind port (default: 8000)"
    )
    web_parser.add_argument(
        "--reload", action="store_true", help="Enable auto-reload (dev mode)"
    )
    web_parser.add_argument(
        "--config",
        default=None,
        metavar="FILE",
        help="Path to signal-watch TOML config (e.g. config/signal_watch.toml). "
        "Exposes config defaults to the UI via GET /api/active-config. "
        "When omitted, auto-picks today's config by UTC weekday "
        "(Mon/Fri→signal_watch_weekdays, Tue–Thu→signal_watch, "
        "Sat/Sun→signal_watch_all), matching `buibui signal watch`.",
    )
    web_parser.set_defaults(func=run_web_server)

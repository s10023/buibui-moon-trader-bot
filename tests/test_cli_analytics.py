"""Tests for cli/analytics.py — --universe flag wiring."""

import argparse
from unittest.mock import patch

import pytest

from cli.analytics import add_analytics_subparser


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    add_analytics_subparser(sub)
    return parser


class TestUniverseFlag:
    def test_universe_and_symbols_mutually_exclusive(self) -> None:
        parser = _make_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                ["analytics", "backfill", "--universe", "--symbols", "BTCUSDT"]
            )

    def test_backfill_universe_resolves_symbols_from_toml(self) -> None:
        parser = _make_parser()
        args = parser.parse_args(["analytics", "backfill", "--universe"])
        with (
            patch("cli.analytics.load_universe", return_value=["AAAUSDT", "BBBUSDT"]),
            patch("cli.analytics.analytics_runner.run_backfill") as mock_run,
        ):
            args.func(args)
        assert mock_run.call_args.kwargs["symbols"] == ["AAAUSDT", "BBBUSDT"]

    def test_backfill_default_passes_none_through(self) -> None:
        parser = _make_parser()
        args = parser.parse_args(["analytics", "backfill"])
        with patch("cli.analytics.analytics_runner.run_backfill") as mock_run:
            args.func(args)
        assert mock_run.call_args.kwargs["symbols"] is None

    def test_sync_universe_resolves_symbols_from_toml(self) -> None:
        parser = _make_parser()
        args = parser.parse_args(["analytics", "sync", "--universe"])
        with (
            patch("cli.analytics.load_universe", return_value=["AAAUSDT"]),
            patch("cli.analytics.analytics_runner.run_sync") as mock_run,
        ):
            args.func(args)
        assert mock_run.call_args.kwargs["symbols"] == ["AAAUSDT"]

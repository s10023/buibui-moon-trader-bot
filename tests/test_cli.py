"""Tests for buibui.py CLI argument parsing and dispatch."""

import argparse
from unittest.mock import patch

import pytest


class TestCLIParsing:
    """Tests for CLI argument parsing in buibui.py."""

    def test_price_subcommand_defaults(self) -> None:
        """Price subcommand parses with correct defaults."""
        from monitor import price_monitor

        with patch.object(price_monitor, "main") as mock_main:
            with patch("sys.argv", ["buibui.py", "monitor", "price"]):
                from buibui import main

                main()

            mock_main.assert_called_once_with(
                live=False, telegram=False, sort="default"
            )

    def test_price_with_live_flag(self) -> None:
        """Price subcommand with --live flag."""
        from monitor import price_monitor

        with patch.object(price_monitor, "main") as mock_main:
            with patch("sys.argv", ["buibui.py", "monitor", "price", "--live"]):
                from buibui import main

                main()

            mock_main.assert_called_once_with(live=True, telegram=False, sort="default")

    def test_price_with_sort(self) -> None:
        """Price subcommand with --sort flag."""
        from monitor import price_monitor

        with patch.object(price_monitor, "main") as mock_main:
            with patch(
                "sys.argv",
                ["buibui.py", "monitor", "price", "--sort", "change_15m:desc"],
            ):
                from buibui import main

                main()

            mock_main.assert_called_once_with(
                live=False, telegram=False, sort="change_15m:desc"
            )

    def test_position_subcommand_defaults(self) -> None:
        """Position subcommand parses with correct defaults."""
        from monitor import position_monitor

        with patch.object(position_monitor, "main") as mock_main:
            with patch("sys.argv", ["buibui.py", "monitor", "position"]):
                from buibui import main

                main()

            mock_main.assert_called_once_with(
                sort="default",
                telegram=False,
                hide_empty=False,
                compact=False,
                live=False,
            )

    def test_position_with_all_flags(self) -> None:
        """Position subcommand with all flags."""
        from monitor import position_monitor

        with patch.object(position_monitor, "main") as mock_main:
            with patch(
                "sys.argv",
                [
                    "buibui.py",
                    "monitor",
                    "position",
                    "--sort",
                    "pnl_pct:desc",
                    "--telegram",
                    "--hide-empty",
                    "--compact",
                    "--live",
                ],
            ):
                from buibui import main

                main()

            mock_main.assert_called_once_with(
                sort="pnl_pct:desc",
                telegram=True,
                hide_empty=True,
                compact=True,
                live=True,
            )

    def test_missing_subcommand_exits(self) -> None:
        """Missing subcommand causes SystemExit."""
        with patch("sys.argv", ["buibui.py"]):
            from buibui import main

            with pytest.raises(SystemExit):
                main()

    def test_missing_monitor_subcommand_exits(self) -> None:
        """'monitor' without price/position causes SystemExit."""
        with patch("sys.argv", ["buibui.py", "monitor"]):
            from buibui import main

            with pytest.raises(SystemExit):
                main()


class TestBacktestMinSlPctFlag:
    """`--min-sl-pct` must reach BOTH backtest modes.

    `cli/backtest.py` read it behind a `hasattr(args, "min_sl_pct")` guard the
    backtest parser could never satisfy — the flag existed only on
    `buibui signal`. Single-combo was therefore pinned at 0.0 however it was
    invoked, which is the no-stop-floor condition AGENTS.md's ST104 measured at
    0.215R -> 0.427R mean drag.
    """

    @staticmethod
    def _args(argv: list[str]) -> argparse.Namespace:
        """Parse a real backtest argv, so the parser itself is under test."""
        from cli.backtest import add_backtest_subparser

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_backtest_subparser(sub)
        return parser.parse_args(["backtest", *argv])

    def test_flag_reaches_single_combo(self) -> None:
        from analytics import backtest_runner
        from cli.backtest import run_backtest

        with patch.object(backtest_runner, "run_backtest_cmd") as mock:
            run_backtest(
                self._args(
                    [
                        "--symbol",
                        "BTCUSDT",
                        "--strategy",
                        "fvg",
                        "--min-sl-pct",
                        "0.005",
                    ]
                )
            )
        assert mock.call_args.kwargs["min_sl_pct"] == 0.005

    def test_flag_reaches_sweep(self) -> None:
        from analytics import backtest_runner
        from cli.backtest import run_backtest

        with patch.object(backtest_runner, "run_backtest_sweep") as mock:
            run_backtest(self._args(["--symbols", "BTCUSDT", "--min-sl-pct", "0.005"]))
        assert mock.call_args.args[0].min_sl_pct == 0.005

    def test_single_combo_defaults_to_disabled(self) -> None:
        """Positive control.

        Without the two default cases, the two flag cases above would still
        pass against a build that always forced a floor on regardless of what
        the operator asked for.
        """
        from analytics import backtest_runner
        from cli.backtest import run_backtest

        with patch.object(backtest_runner, "run_backtest_cmd") as mock:
            run_backtest(self._args(["--symbol", "BTCUSDT", "--strategy", "fvg"]))
        assert mock.call_args.kwargs["min_sl_pct"] == 0.0

    def test_sweep_default_leaves_the_config_value_alone(self) -> None:
        """Positive control: no flag must NOT overwrite the TOML value."""
        from analytics import backtest_runner
        from analytics.backtest_config import BacktestSweepConfig
        from cli.backtest import run_backtest

        sentinel = BacktestSweepConfig()
        sentinel.min_sl_pct = 0.02
        with (
            patch.object(backtest_runner, "run_backtest_sweep") as mock,
            patch("cli.backtest.BacktestSweepConfig", return_value=sentinel),
        ):
            run_backtest(self._args(["--symbols", "BTCUSDT"]))
        assert mock.call_args.args[0].min_sl_pct == 0.02

    def test_the_flag_is_actually_declared_on_the_parser(self) -> None:
        """The defect was an absent flag, not a bad read — pin the declaration.

        Before the fix this argv failed at parse time with
        `unrecognized arguments: --min-sl-pct`.
        """
        parsed = self._args(["--min-sl-pct", "0.005"])
        assert parsed.min_sl_pct == 0.005

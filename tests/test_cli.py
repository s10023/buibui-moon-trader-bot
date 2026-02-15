"""Tests for buibui.py CLI argument parsing and dispatch."""

import pytest
from unittest.mock import patch


class TestCLIParsing:
    """Tests for CLI argument parsing in buibui.py."""

    def test_price_subcommand_defaults(self):
        """Price subcommand parses with correct defaults."""
        from monitor import price_monitor

        with patch.object(price_monitor, "main") as mock_main:
            with patch("sys.argv", ["buibui.py", "monitor", "price"]):
                from buibui import main

                main()

            mock_main.assert_called_once_with(
                live=False, telegram=False, sort="default"
            )

    def test_price_with_live_flag(self):
        """Price subcommand with --live flag."""
        from monitor import price_monitor

        with patch.object(price_monitor, "main") as mock_main:
            with patch("sys.argv", ["buibui.py", "monitor", "price", "--live"]):
                from buibui import main

                main()

            mock_main.assert_called_once_with(
                live=True, telegram=False, sort="default"
            )

    def test_price_with_sort(self):
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

    def test_position_subcommand_defaults(self):
        """Position subcommand parses with correct defaults."""
        from monitor import position_monitor

        with patch.object(position_monitor, "main") as mock_main:
            with patch("sys.argv", ["buibui.py", "monitor", "position"]):
                from buibui import main

                main()

            mock_main.assert_called_once_with(
                sort="default", telegram=False, hide_empty=False, compact=False
            )

    def test_position_with_all_flags(self):
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
                ],
            ):
                from buibui import main

                main()

            mock_main.assert_called_once_with(
                sort="pnl_pct:desc", telegram=True, hide_empty=True, compact=True
            )

    def test_missing_subcommand_exits(self):
        """Missing subcommand causes SystemExit."""
        with patch("sys.argv", ["buibui.py"]):
            from buibui import main

            with pytest.raises(SystemExit):
                main()

    def test_missing_monitor_subcommand_exits(self):
        """'monitor' without price/position causes SystemExit."""
        with patch("sys.argv", ["buibui.py", "monitor"]):
            from buibui import main

            with pytest.raises(SystemExit):
                main()

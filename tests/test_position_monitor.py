"""Tests for monitor/position_lib.py — pure position monitor logic."""

import re
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from monitor.position_lib import (
    color_risk_usd,
    color_sl_size,
    colorize,
    colorize_dollar,
    display_progress_bar,
    display_table,
    fetch_open_positions,
    get_stop_loss_for_symbol,
    get_wallet_balance,
)
from tests.conftest import SAMPLE_COIN_ORDER, SAMPLE_COINS_CONFIG, strip_ansi


class TestColorize:
    """Tests for colorize()."""

    def test_positive_value(self) -> None:
        assert strip_ansi(colorize(5.0)) == "+5.00%"

    def test_negative_value(self) -> None:
        assert strip_ansi(colorize(-3.0)) == "-3.00%"

    def test_zero_value(self) -> None:
        assert strip_ansi(colorize(0)) == "+0.00%"

    def test_with_threshold(self) -> None:
        result = colorize(0.5, threshold=1.0)
        assert "\033[93m" in result  # yellow

    def test_above_threshold(self) -> None:
        result = colorize(2.0, threshold=1.0)
        assert "\033[92m" in result  # green

    def test_below_negative_threshold(self) -> None:
        result = colorize(-2.0, threshold=1.0)
        assert "\033[91m" in result  # red

    def test_non_numeric_returns_input(self) -> None:
        assert colorize("N/A") == "N/A"


class TestColorizeDollar:
    """Tests for colorize_dollar()."""

    def test_positive(self) -> None:
        assert strip_ansi(colorize_dollar(174.73)) == "$174.73"

    def test_negative(self) -> None:
        assert strip_ansi(colorize_dollar(-50.0)) == "-$50.00"

    def test_zero(self) -> None:
        assert strip_ansi(colorize_dollar(0)) == "$0.00"

    def test_large_number_with_commas(self) -> None:
        assert strip_ansi(colorize_dollar(14899.70)) == "$14,899.70"

    def test_non_numeric_returns_dollar_string(self) -> None:
        assert colorize_dollar("N/A") == "$N/A"


class TestColorSlSize:
    """Tests for color_sl_size()."""

    def test_tight_sl_red(self) -> None:
        result = color_sl_size(1.5)
        assert "\033[91m" in result
        assert "1.50%" in strip_ansi(result)

    def test_medium_sl_yellow(self) -> None:
        result = color_sl_size(2.5)
        assert "\033[93m" in result
        assert "2.50%" in strip_ansi(result)

    def test_wide_sl_green(self) -> None:
        result = color_sl_size(4.0)
        assert "\033[92m" in result
        assert "4.00%" in strip_ansi(result)

    def test_boundary_2_is_yellow(self) -> None:
        assert "\033[93m" in color_sl_size(2.0)

    def test_boundary_3_5_is_green(self) -> None:
        assert "\033[92m" in color_sl_size(3.5)


class TestColorRiskUsd:
    """Tests for color_risk_usd()."""

    def test_low_risk_green(self) -> None:
        assert "\033[92m" in color_risk_usd(-200, 1000)

    def test_medium_risk_yellow(self) -> None:
        assert "\033[93m" in color_risk_usd(-400, 1000)

    def test_high_risk_red(self) -> None:
        assert "\033[91m" in color_risk_usd(-600, 1000)

    def test_zero_balance(self) -> None:
        assert "0.00%" in strip_ansi(color_risk_usd(100, 0))


class TestDisplayProgressBar:
    """Tests for display_progress_bar()."""

    def test_zero_progress(self) -> None:
        result = display_progress_bar(0, 2000)
        assert "0.0%" in result
        assert "$0.00" in strip_ansi(result)

    def test_half_progress(self) -> None:
        result = display_progress_bar(1000, 2000)
        assert "50.0%" in result
        assert "\033[93m" in result  # yellow at 50%

    def test_full_progress(self) -> None:
        result = display_progress_bar(2000, 2000)
        assert "100.0%" in result
        assert "\033[92m" in result  # green at 100%

    def test_over_target_capped(self) -> None:
        assert "100.0%" in display_progress_bar(3000, 2000)

    def test_zero_target_returns_empty(self) -> None:
        assert display_progress_bar(1000, 0) == ""

    def test_negative_target_returns_empty(self) -> None:
        assert display_progress_bar(1000, -100) == ""

    def test_custom_bar_length(self) -> None:
        stripped = strip_ansi(display_progress_bar(1000, 2000, bar_length=10))
        assert "\u2588" * 5 + "-----" in stripped


class TestGetWalletBalance:
    """Tests for get_wallet_balance()."""

    def test_returns_usdt_balance(
        self, mock_futures_balance: list[dict[str, Any]]
    ) -> None:
        mock_client = MagicMock()
        mock_client.futures_account_balance.return_value = mock_futures_balance
        balance, unrealized = get_wallet_balance(mock_client)
        assert balance == 1123.15
        assert unrealized == 290.29

    def test_no_usdt_returns_zeros(self) -> None:
        mock_client = MagicMock()
        mock_client.futures_account_balance.return_value = [
            {"asset": "BNB", "balance": "10.0", "crossUnPnl": "0"}
        ]
        balance, unrealized = get_wallet_balance(mock_client)
        assert balance == 0.0
        assert unrealized == 0.0

    def test_empty_balance_list(self) -> None:
        mock_client = MagicMock()
        mock_client.futures_account_balance.return_value = []
        balance, unrealized = get_wallet_balance(mock_client)
        assert balance == 0.0
        assert unrealized == 0.0


class TestGetStopLossForSymbol:
    """Tests for get_stop_loss_for_symbol()."""

    def test_returns_sl_price(
        self, mock_stop_loss_orders: list[dict[str, Any]]
    ) -> None:
        mock_client = MagicMock()
        mock_client.futures_get_open_orders.return_value = mock_stop_loss_orders
        assert get_stop_loss_for_symbol(mock_client, "BTCUSDT") == 109970.0

    def test_no_sl_orders(self) -> None:
        mock_client = MagicMock()
        mock_client.futures_get_open_orders.return_value = []
        assert get_stop_loss_for_symbol(mock_client, "BTCUSDT") is None

    def test_non_sl_orders_ignored(self) -> None:
        mock_client = MagicMock()
        mock_client.futures_get_open_orders.return_value = [
            {"type": "LIMIT", "reduceOnly": True, "stopPrice": "50000.0"},
        ]
        assert get_stop_loss_for_symbol(mock_client, "BTCUSDT") is None

    def test_api_error_returns_none(self) -> None:
        mock_client = MagicMock()
        mock_client.futures_get_open_orders.side_effect = Exception("API error")
        assert get_stop_loss_for_symbol(mock_client, "BTCUSDT") is None

    def test_stop_type_without_reduce_only(self) -> None:
        mock_client = MagicMock()
        mock_client.futures_get_open_orders.return_value = [
            {"type": "STOP_MARKET", "reduceOnly": False, "stopPrice": "50000.0"},
        ]
        assert get_stop_loss_for_symbol(mock_client, "BTCUSDT") is None

    def test_close_position_flag_detected_as_sl(
        self, mock_close_position_sl_orders: list[dict[str, Any]]
    ) -> None:
        """Binance UI sets SL orders with closePosition=True, not reduceOnly=True."""
        mock_client = MagicMock()
        mock_client.futures_get_open_orders.return_value = mock_close_position_sl_orders
        assert get_stop_loss_for_symbol(mock_client, "BTCUSDT") == 109970.0

    def test_zero_stop_price_ignored(self) -> None:
        mock_client = MagicMock()
        mock_client.futures_get_open_orders.return_value = [
            {"type": "STOP_MARKET", "closePosition": True, "stopPrice": "0"},
        ]
        assert get_stop_loss_for_symbol(mock_client, "BTCUSDT") is None


class TestFetchOpenPositions:
    """Tests for fetch_open_positions()."""

    def test_returns_open_positions(
        self,
        mock_positions_data: list[dict[str, Any]],
        mock_futures_balance: list[dict[str, Any]],
    ) -> None:
        mock_client = MagicMock()
        mock_client.futures_position_information.return_value = mock_positions_data
        mock_client.futures_account_balance.return_value = mock_futures_balance
        mock_client.futures_get_open_orders.return_value = []

        positions, total_risk, wallet, unrealized = fetch_open_positions(
            mock_client, SAMPLE_COINS_CONFIG, SAMPLE_COIN_ORDER
        )

        open_rows = [r for r in positions if r[1] != "-"]
        assert len(open_rows) == 2
        symbols = [r[0] for r in open_rows]
        assert "BTCUSDT" in symbols
        assert "ETHUSDT" in symbols

    def test_hide_empty_excludes_placeholders(
        self,
        mock_positions_data: list[dict[str, Any]],
        mock_futures_balance: list[dict[str, Any]],
    ) -> None:
        mock_client = MagicMock()
        mock_client.futures_position_information.return_value = mock_positions_data
        mock_client.futures_account_balance.return_value = mock_futures_balance
        mock_client.futures_get_open_orders.return_value = []

        positions, _, _wallet, _unrealized = fetch_open_positions(
            mock_client, SAMPLE_COINS_CONFIG, SAMPLE_COIN_ORDER, hide_empty=True
        )
        assert all(r[1] != "-" for r in positions)

    def test_sort_by_pnl_pct(
        self,
        mock_positions_data: list[dict[str, Any]],
        mock_futures_balance: list[dict[str, Any]],
    ) -> None:
        mock_client = MagicMock()
        mock_client.futures_position_information.return_value = mock_positions_data
        mock_client.futures_account_balance.return_value = mock_futures_balance
        mock_client.futures_get_open_orders.return_value = []

        positions, _, _wallet, _unrealized = fetch_open_positions(
            mock_client,
            SAMPLE_COINS_CONFIG,
            SAMPLE_COIN_ORDER,
            sort_by="pnl_pct",
            descending=True,
            hide_empty=True,
        )
        # ETHUSDT has higher PnL% than BTCUSDT
        assert positions[0][0] == "ETHUSDT"

    def test_default_sort_follows_coin_order(
        self,
        mock_positions_data: list[dict[str, Any]],
        mock_futures_balance: list[dict[str, Any]],
    ) -> None:
        mock_client = MagicMock()
        mock_client.futures_position_information.return_value = mock_positions_data
        mock_client.futures_account_balance.return_value = mock_futures_balance
        mock_client.futures_get_open_orders.return_value = []

        positions, _, _wallet, _unrealized = fetch_open_positions(
            mock_client,
            SAMPLE_COINS_CONFIG,
            SAMPLE_COIN_ORDER,
            sort_by="default",
            hide_empty=True,
        )
        assert positions[0][0] == "BTCUSDT"

    def test_total_risk_with_stop_loss(
        self,
        mock_positions_data: list[dict[str, Any]],
        mock_futures_balance: list[dict[str, Any]],
        mock_stop_loss_orders: list[dict[str, Any]],
    ) -> None:
        mock_client = MagicMock()
        mock_client.futures_position_information.return_value = mock_positions_data
        mock_client.futures_account_balance.return_value = mock_futures_balance
        mock_client.futures_get_open_orders.return_value = mock_stop_loss_orders

        _, total_risk, _wallet, _unrealized = fetch_open_positions(
            mock_client,
            SAMPLE_COINS_CONFIG,
            SAMPLE_COIN_ORDER,
            hide_empty=True,
        )
        assert isinstance(total_risk, float)


class TestDisplayTable:
    """Tests for display_table()."""

    def test_compact_mode_no_table(
        self,
        mock_positions_data: list[dict[str, Any]],
        mock_futures_balance: list[dict[str, Any]],
    ) -> None:
        mock_client = MagicMock()
        mock_client.futures_position_information.return_value = mock_positions_data
        mock_client.futures_account_balance.return_value = mock_futures_balance
        mock_client.futures_get_open_orders.return_value = []

        result = display_table(
            mock_client, SAMPLE_COINS_CONFIG, SAMPLE_COIN_ORDER, 2000.0, compact=True
        )
        assert "Wallet Balance" in result
        assert "\u2552" not in result

    def test_full_mode_has_table(
        self,
        mock_positions_data: list[dict[str, Any]],
        mock_futures_balance: list[dict[str, Any]],
    ) -> None:
        mock_client = MagicMock()
        mock_client.futures_position_information.return_value = mock_positions_data
        mock_client.futures_account_balance.return_value = mock_futures_balance
        mock_client.futures_get_open_orders.return_value = []

        result = display_table(
            mock_client, SAMPLE_COINS_CONFIG, SAMPLE_COIN_ORDER, 2000.0, compact=False
        )
        assert "Wallet Balance" in result
        assert "\u2552" in result

    def test_telegram_sends_message(
        self,
        mock_positions_data: list[dict[str, Any]],
        mock_futures_balance: list[dict[str, Any]],
    ) -> None:
        mock_client = MagicMock()
        mock_client.futures_position_information.return_value = mock_positions_data
        mock_client.futures_account_balance.return_value = mock_futures_balance
        mock_client.futures_get_open_orders.return_value = []

        with patch("monitor.position_lib.send_telegram_message") as mock_tg:
            display_table(
                mock_client,
                SAMPLE_COINS_CONFIG,
                SAMPLE_COIN_ORDER,
                2000.0,
                telegram=True,
            )
            mock_tg.assert_called_once()
            msg = mock_tg.call_args[0][0]
            assert "Wallet Balance" in msg


class TestPositionBugFixes:
    """Regression tests for correctness bug fixes."""

    def test_fetch_raises_runtime_error_on_api_failure(self) -> None:
        mock_client = MagicMock()
        mock_client.futures_position_information.side_effect = Exception("API error")

        with pytest.raises(RuntimeError, match="Failed to fetch position information"):
            fetch_open_positions(mock_client, SAMPLE_COINS_CONFIG, SAMPLE_COIN_ORDER)

    def test_short_sl_risk_usd_is_positive(
        self, mock_futures_balance: list[dict[str, Any]]
    ) -> None:
        # SHORT position with SL above entry (correct real-world setup)
        short_with_sl_above_entry = [
            {
                "symbol": "BTCUSDT",
                "positionAmt": "-0.135",
                "entryPrice": "110032.0",
                "markPrice": "108757.0",
                "notional": "-14899.70",
                "positionInitialMargin": "595.99",
                "unRealizedProfit": "174.73",
            },
        ]
        mock_client = MagicMock()
        mock_client.futures_position_information.return_value = (
            short_with_sl_above_entry
        )
        mock_client.futures_account_balance.return_value = mock_futures_balance
        mock_client.futures_get_open_orders.return_value = [
            {
                "symbol": "BTCUSDT",
                "type": "STOP_MARKET",
                "reduceOnly": True,
                "stopPrice": "111000.0",  # above entry — correct SHORT SL
            }
        ]

        _, total_risk, _wallet, _unrealized = fetch_open_positions(
            mock_client, SAMPLE_COINS_CONFIG, SAMPLE_COIN_ORDER, hide_empty=True
        )
        assert total_risk > 0, "SHORT sl_risk_usd must be positive (it is a loss)"

    def test_wallet_balance_zero_no_division_error(
        self, mock_positions_data: list[dict[str, Any]]
    ) -> None:
        mock_client = MagicMock()
        mock_client.futures_position_information.return_value = mock_positions_data
        mock_client.futures_account_balance.return_value = [
            {"asset": "USDT", "balance": "0.0", "crossUnPnl": "0.0"}
        ]
        mock_client.futures_get_open_orders.return_value = []

        # Must not raise ZeroDivisionError
        positions, _, wallet, _ = fetch_open_positions(
            mock_client, SAMPLE_COINS_CONFIG, SAMPLE_COIN_ORDER, hide_empty=True
        )
        assert wallet == 0.0
        for row in positions:
            assert row[9] == "0.00%"  # Risk% column must default to 0.00%

    def test_available_balance_uses_wallet_not_total(
        self,
        mock_positions_data: list[dict[str, Any]],
        mock_futures_balance: list[dict[str, Any]],
    ) -> None:
        mock_client = MagicMock()
        mock_client.futures_position_information.return_value = mock_positions_data
        mock_client.futures_account_balance.return_value = mock_futures_balance
        mock_client.futures_get_open_orders.return_value = []

        result = display_table(
            mock_client, SAMPLE_COINS_CONFIG, SAMPLE_COIN_ORDER, 0.0, compact=True
        )
        # wallet=1123.15, used_margin=595.99+591.11=1187.10
        # Correct: available = 1123.15 - 1187.10 = -63.95 (negative — over-margined)
        # Wrong (old): available = (1123.15+290.29) - 1187.10 = 226.34 (overstated)
        match = re.search(r"Available Balance: \$(-?[\d,]+\.\d+)", result)
        assert match is not None, "Available Balance not found in output"
        available = float(match.group(1).replace(",", ""))
        assert available < 0, (
            f"Available balance should be wallet-based (~-63.95), got {available}"
        )

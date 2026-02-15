"""Tests for monitor/position_monitor.py."""

from unittest.mock import patch

from tests.conftest import strip_ansi
from monitor.position_monitor import (
    colorize,
    colorize_dollar,
    color_sl_size,
    color_risk_usd,
    display_progress_bar,
    get_wallet_balance,
    get_stop_loss_for_symbol,
    fetch_open_positions,
    display_table,
)


class TestColorize:
    """Tests for colorize()."""

    def test_positive_value(self):
        assert strip_ansi(colorize(5.0)) == "+5.00%"

    def test_negative_value(self):
        assert strip_ansi(colorize(-3.0)) == "-3.00%"

    def test_zero_value(self):
        assert strip_ansi(colorize(0)) == "+0.00%"

    def test_with_threshold(self):
        result = colorize(0.5, threshold=1.0)
        assert "\033[93m" in result  # yellow

    def test_above_threshold(self):
        result = colorize(2.0, threshold=1.0)
        assert "\033[92m" in result  # green

    def test_below_negative_threshold(self):
        result = colorize(-2.0, threshold=1.0)
        assert "\033[91m" in result  # red

    def test_non_numeric_returns_input(self):
        assert colorize("N/A") == "N/A"


class TestColorizeDollar:
    """Tests for colorize_dollar()."""

    def test_positive(self):
        assert strip_ansi(colorize_dollar(174.73)) == "$174.73"

    def test_negative(self):
        assert strip_ansi(colorize_dollar(-50.0)) == "-$50.00"

    def test_zero(self):
        assert strip_ansi(colorize_dollar(0)) == "$0.00"

    def test_large_number_with_commas(self):
        assert strip_ansi(colorize_dollar(14899.70)) == "$14,899.70"

    def test_non_numeric_returns_dollar_string(self):
        assert colorize_dollar("N/A") == "$N/A"


class TestColorSlSize:
    """Tests for color_sl_size()."""

    def test_tight_sl_red(self):
        result = color_sl_size(1.5)
        assert "\033[91m" in result
        assert "1.50%" in strip_ansi(result)

    def test_medium_sl_yellow(self):
        result = color_sl_size(2.5)
        assert "\033[93m" in result
        assert "2.50%" in strip_ansi(result)

    def test_wide_sl_green(self):
        result = color_sl_size(4.0)
        assert "\033[92m" in result
        assert "4.00%" in strip_ansi(result)

    def test_boundary_2_is_yellow(self):
        assert "\033[93m" in color_sl_size(2.0)

    def test_boundary_3_5_is_green(self):
        assert "\033[92m" in color_sl_size(3.5)


class TestColorRiskUsd:
    """Tests for color_risk_usd()."""

    def test_low_risk_green(self):
        assert "\033[92m" in color_risk_usd(-200, 1000)

    def test_medium_risk_yellow(self):
        assert "\033[93m" in color_risk_usd(-400, 1000)

    def test_high_risk_red(self):
        assert "\033[91m" in color_risk_usd(-600, 1000)

    def test_zero_balance(self):
        assert "0.00%" in strip_ansi(color_risk_usd(100, 0))


class TestDisplayProgressBar:
    """Tests for display_progress_bar()."""

    def test_zero_progress(self):
        result = display_progress_bar(0, 2000)
        assert "0.0%" in result
        assert "$0.00" in strip_ansi(result)

    def test_half_progress(self):
        result = display_progress_bar(1000, 2000)
        assert "50.0%" in result
        assert "\033[93m" in result  # yellow at 50%

    def test_full_progress(self):
        result = display_progress_bar(2000, 2000)
        assert "100.0%" in result
        assert "\033[92m" in result  # green at 100%

    def test_over_target_capped(self):
        assert "100.0%" in display_progress_bar(3000, 2000)

    def test_zero_target_returns_empty(self):
        assert display_progress_bar(1000, 0) == ""

    def test_negative_target_returns_empty(self):
        assert display_progress_bar(1000, -100) == ""

    def test_custom_bar_length(self):
        stripped = strip_ansi(display_progress_bar(1000, 2000, bar_length=10))
        assert "█████-----" in stripped


class TestGetWalletBalance:
    """Tests for get_wallet_balance()."""

    def test_returns_usdt_balance(self, mock_futures_balance):
        with patch("monitor.position_monitor.client") as mock_client:
            mock_client.futures_account_balance.return_value = mock_futures_balance
            balance, unrealized = get_wallet_balance()
            assert balance == 1123.15
            assert unrealized == 290.29

    def test_no_usdt_returns_zeros(self):
        with patch("monitor.position_monitor.client") as mock_client:
            mock_client.futures_account_balance.return_value = [
                {"asset": "BNB", "balance": "10.0", "crossUnPnl": "0"}
            ]
            balance, unrealized = get_wallet_balance()
            assert balance == 0.0
            assert unrealized == 0.0

    def test_empty_balance_list(self):
        with patch("monitor.position_monitor.client") as mock_client:
            mock_client.futures_account_balance.return_value = []
            balance, unrealized = get_wallet_balance()
            assert balance == 0.0
            assert unrealized == 0.0


class TestGetStopLossForSymbol:
    """Tests for get_stop_loss_for_symbol()."""

    def test_returns_sl_price(self, mock_stop_loss_orders):
        with patch("monitor.position_monitor.client") as mock_client:
            mock_client.futures_get_open_orders.return_value = mock_stop_loss_orders
            assert get_stop_loss_for_symbol("BTCUSDT") == 109970.0

    def test_no_sl_orders(self):
        with patch("monitor.position_monitor.client") as mock_client:
            mock_client.futures_get_open_orders.return_value = []
            assert get_stop_loss_for_symbol("BTCUSDT") is None

    def test_non_sl_orders_ignored(self):
        with patch("monitor.position_monitor.client") as mock_client:
            mock_client.futures_get_open_orders.return_value = [
                {"type": "LIMIT", "reduceOnly": True, "stopPrice": "50000.0"},
            ]
            assert get_stop_loss_for_symbol("BTCUSDT") is None

    def test_api_error_returns_none(self):
        with patch("monitor.position_monitor.client") as mock_client:
            mock_client.futures_get_open_orders.side_effect = Exception("API error")
            assert get_stop_loss_for_symbol("BTCUSDT") is None

    def test_stop_type_without_reduce_only(self):
        with patch("monitor.position_monitor.client") as mock_client:
            mock_client.futures_get_open_orders.return_value = [
                {"type": "STOP_MARKET", "reduceOnly": False, "stopPrice": "50000.0"},
            ]
            assert get_stop_loss_for_symbol("BTCUSDT") is None


class TestFetchOpenPositions:
    """Tests for fetch_open_positions()."""

    def test_returns_open_positions(self, mock_positions_data, mock_futures_balance):
        with patch("monitor.position_monitor.client") as mock_client:
            mock_client.futures_position_information.return_value = mock_positions_data
            mock_client.futures_account_balance.return_value = mock_futures_balance
            mock_client.futures_get_open_orders.return_value = []

            positions, total_risk = fetch_open_positions()

            open_rows = [r for r in positions if r[1] != "-"]
            assert len(open_rows) == 2
            symbols = [r[0] for r in open_rows]
            assert "BTCUSDT" in symbols
            assert "ETHUSDT" in symbols

    def test_hide_empty_excludes_placeholders(self, mock_positions_data, mock_futures_balance):
        with patch("monitor.position_monitor.client") as mock_client:
            mock_client.futures_position_information.return_value = mock_positions_data
            mock_client.futures_account_balance.return_value = mock_futures_balance
            mock_client.futures_get_open_orders.return_value = []

            positions, _ = fetch_open_positions(hide_empty=True)
            assert all(r[1] != "-" for r in positions)

    def test_sort_by_pnl_pct(self, mock_positions_data, mock_futures_balance):
        with patch("monitor.position_monitor.client") as mock_client:
            mock_client.futures_position_information.return_value = mock_positions_data
            mock_client.futures_account_balance.return_value = mock_futures_balance
            mock_client.futures_get_open_orders.return_value = []

            positions, _ = fetch_open_positions(
                sort_by="pnl_pct", descending=True, hide_empty=True
            )
            # ETHUSDT has higher PnL% than BTCUSDT
            assert positions[0][0] == "ETHUSDT"

    def test_default_sort_follows_coin_order(self, mock_positions_data, mock_futures_balance):
        with patch("monitor.position_monitor.client") as mock_client:
            mock_client.futures_position_information.return_value = mock_positions_data
            mock_client.futures_account_balance.return_value = mock_futures_balance
            mock_client.futures_get_open_orders.return_value = []

            positions, _ = fetch_open_positions(sort_by="default", hide_empty=True)
            assert positions[0][0] == "BTCUSDT"

    def test_total_risk_with_stop_loss(self, mock_positions_data, mock_futures_balance, mock_stop_loss_orders):
        with patch("monitor.position_monitor.client") as mock_client:
            mock_client.futures_position_information.return_value = mock_positions_data
            mock_client.futures_account_balance.return_value = mock_futures_balance
            mock_client.futures_get_open_orders.return_value = mock_stop_loss_orders

            _, total_risk = fetch_open_positions(hide_empty=True)
            assert isinstance(total_risk, float)


class TestDisplayTable:
    """Tests for display_table()."""

    def test_compact_mode_no_table(self, mock_positions_data, mock_futures_balance):
        with patch("monitor.position_monitor.client") as mock_client:
            mock_client.futures_position_information.return_value = mock_positions_data
            mock_client.futures_account_balance.return_value = mock_futures_balance
            mock_client.futures_get_open_orders.return_value = []

            result = display_table(compact=True)
            assert "Wallet Balance" in result
            assert "╒" not in result

    def test_full_mode_has_table(self, mock_positions_data, mock_futures_balance):
        with patch("monitor.position_monitor.client") as mock_client:
            mock_client.futures_position_information.return_value = mock_positions_data
            mock_client.futures_account_balance.return_value = mock_futures_balance
            mock_client.futures_get_open_orders.return_value = []

            result = display_table(compact=False)
            assert "Wallet Balance" in result
            assert "╒" in result

    def test_telegram_sends_message(self, mock_positions_data, mock_futures_balance):
        with patch("monitor.position_monitor.client") as mock_client:
            mock_client.futures_position_information.return_value = mock_positions_data
            mock_client.futures_account_balance.return_value = mock_futures_balance
            mock_client.futures_get_open_orders.return_value = []

            with patch("monitor.position_monitor.send_telegram_message") as mock_tg:
                display_table(telegram=True)
                mock_tg.assert_called_once()
                msg = mock_tg.call_args[0][0]
                assert "Wallet Balance" in msg

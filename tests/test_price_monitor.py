"""Tests for monitor/price_monitor.py."""

import pytest
from unittest.mock import patch, MagicMock

from tests.conftest import strip_ansi
from monitor.price_monitor import (
    format_pct,
    format_pct_simple,
    sort_table,
    clear_screen,
    sync_binance_time,
    get_klines,
    get_price_changes,
)


class TestFormatPct:
    """Tests for format_pct()."""

    def test_positive_value(self):
        assert strip_ansi(str(format_pct(2.5))) == "+2.50%"

    def test_negative_value(self):
        assert strip_ansi(str(format_pct(-1.3))) == "-1.30%"

    def test_zero_value(self):
        assert strip_ansi(str(format_pct(0))) == "+0.00%"

    def test_string_number(self):
        assert strip_ansi(str(format_pct("3.14"))) == "+3.14%"

    def test_non_numeric_returns_input(self):
        assert format_pct("N/A") == "N/A"

    def test_positive_has_color_codes(self):
        result = str(format_pct(1.0))
        assert "\033[" in result or "\x1b[" in result

    def test_negative_has_color_codes(self):
        result = str(format_pct(-1.0))
        assert "\033[" in result or "\x1b[" in result


class TestFormatPctSimple:
    """Tests for format_pct_simple()."""

    def test_positive(self):
        assert format_pct_simple(2.5) == "+2.50%"

    def test_negative(self):
        assert format_pct_simple(-1.3) == "-1.30%"

    def test_zero(self):
        assert format_pct_simple(0) == "+0.00%"

    def test_string_number(self):
        assert format_pct_simple("3.14") == "+3.14%"

    def test_non_numeric_returns_string(self):
        assert format_pct_simple("N/A") == "N/A"


class TestSortTable:
    """Tests for sort_table()."""

    def setup_method(self):
        self.headers = [
            "Symbol",
            "Last Price",
            "15m %",
            "1h %",
            "Since Asia 8AM",
            "24h %",
        ]
        self.table = [
            ["BTCUSDT", "62457.10", "+0.53%", "+1.42%", "+0.88%", "+2.31%"],
            ["ETHUSDT", "3408.50", "+0.22%", "+1.05%", "+0.71%", "+1.74%"],
            ["SOLUSDT", "143.22", "-0.08%", "+0.34%", "+0.11%", "+0.89%"],
        ]

    def test_sort_by_15m_descending(self):
        result = sort_table(self.table, self.headers, "change_15m", True)
        assert result[0][0] == "BTCUSDT"
        assert result[-1][0] == "SOLUSDT"

    def test_sort_by_15m_ascending(self):
        result = sort_table(self.table, self.headers, "change_15m", False)
        assert result[0][0] == "SOLUSDT"
        assert result[-1][0] == "BTCUSDT"

    def test_sort_by_1h(self):
        result = sort_table(self.table, self.headers, "change_1h", True)
        assert result[0][0] == "BTCUSDT"

    def test_sort_by_24h(self):
        result = sort_table(self.table, self.headers, "change_24h", True)
        assert result[0][0] == "BTCUSDT"

    def test_sort_by_asia(self):
        result = sort_table(self.table, self.headers, "change_asia", True)
        assert result[0][0] == "BTCUSDT"

    def test_sort_preserves_row_integrity(self):
        result = sort_table(self.table, self.headers, "change_15m", True)
        btc_row = [r for r in result if r[0] == "BTCUSDT"][0]
        assert btc_row[1] == "62457.10"

    def test_sort_with_ansi_codes(self):
        """Sort should strip ANSI codes before comparing."""
        table_with_ansi = [
            ["BTCUSDT", "62457", "\033[32m+0.53%\033[0m", "+1.42%", "+0.88%", "+2.31%"],
            ["ETHUSDT", "3408", "\033[32m+0.22%\033[0m", "+1.05%", "+0.71%", "+1.74%"],
        ]
        result = sort_table(table_with_ansi, self.headers, "change_15m", True)
        assert result[0][0] == "BTCUSDT"

    def test_sort_invalid_column_raises(self):
        with pytest.raises(KeyError):
            sort_table(self.table, self.headers, "invalid_col", True)


class TestClearScreen:
    """Tests for clear_screen()."""

    @patch("monitor.price_monitor.os.system")
    @patch("monitor.price_monitor.os.name", "posix")
    def test_unix_clear(self, mock_system):
        clear_screen()
        mock_system.assert_called_once_with("clear")

    @patch("monitor.price_monitor.os.system")
    @patch("monitor.price_monitor.os.name", "nt")
    def test_windows_clear(self, mock_system):
        clear_screen()
        mock_system.assert_called_once_with("cls")


class TestSyncBinanceTime:
    """Tests for sync_binance_time()."""

    @patch("monitor.price_monitor.time.time", return_value=1700000000.0)
    def test_sets_time_offset(self, _mock_time):
        mock_client = MagicMock()
        mock_client.get_server_time.return_value = {"serverTime": 1700000001000}
        sync_binance_time(mock_client)
        assert mock_client.TIME_OFFSET == 1000

    @patch("monitor.price_monitor.time.time", return_value=1700000001.0)
    def test_negative_offset(self, _mock_time):
        mock_client = MagicMock()
        mock_client.get_server_time.return_value = {"serverTime": 1700000000000}
        sync_binance_time(mock_client)
        assert mock_client.TIME_OFFSET == -1000


class TestGetKlines:
    """Tests for get_klines()."""

    def test_returns_last_kline(self):
        kline1 = ["first_kline"]
        kline2 = ["second_kline"]
        with patch("monitor.price_monitor.client") as mock_client:
            mock_client.get_klines.return_value = [kline1, kline2]
            result = get_klines("BTCUSDT", "15m", 15)
            assert result == kline2

    def test_returns_none_on_error(self):
        with patch("monitor.price_monitor.client") as mock_client:
            mock_client.get_klines.side_effect = Exception("API error")
            assert get_klines("BTCUSDT", "15m", 15) is None


class TestGetPriceChanges:
    """Tests for get_price_changes()."""

    def test_returns_table_for_valid_symbols(self, mock_ticker_data, mock_kline_data):
        with patch("monitor.price_monitor.client") as mock_client:
            mock_client.get_ticker.return_value = mock_ticker_data
            mock_client.get_klines.return_value = [mock_kline_data]
            with patch(
                "monitor.price_monitor.get_open_price_asia", return_value=62000.0
            ):
                table, invalid = get_price_changes(["BTCUSDT"])
                assert len(table) == 1
                assert table[0][0] == "BTCUSDT"
                assert len(invalid) == 0

    def test_invalid_symbol_in_ticker(self, mock_ticker_data):
        with patch("monitor.price_monitor.client") as mock_client:
            mock_client.get_ticker.return_value = mock_ticker_data
            with patch("monitor.price_monitor.batch_get_klines", return_value={}):
                with patch(
                    "monitor.price_monitor.get_open_price_asia", return_value=None
                ):
                    table, invalid = get_price_changes(["XYZUSDT"])
                    assert len(table) == 1
                    assert table[0][1] == "Error"
                    assert len(invalid) == 1

    def test_ticker_api_failure(self):
        with patch("monitor.price_monitor.client") as mock_client:
            mock_client.get_ticker.side_effect = Exception("API down")
            table, invalid = get_price_changes(["BTCUSDT"])
            assert table[0][1] == "Error"

    def test_telegram_mode_uses_simple_format(self, mock_ticker_data, mock_kline_data):
        with patch("monitor.price_monitor.client") as mock_client:
            mock_client.get_ticker.return_value = mock_ticker_data
            mock_client.get_klines.return_value = [mock_kline_data]
            with patch(
                "monitor.price_monitor.get_open_price_asia", return_value=62000.0
            ):
                table, _ = get_price_changes(["BTCUSDT"], telegram=True)
                for cell in table[0][2:]:
                    assert "\033[" not in str(cell)

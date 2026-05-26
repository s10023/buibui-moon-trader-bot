"""Tests for the OKX market-data adapter (utils/okx_client.py)."""

import pytest

from utils.okx_client import _okx_row_to_binance, _to_okx_bar, _to_okx_inst_id


def test_okx_row_to_binance_maps_and_sets_neutral_taker_volume() -> None:
    # OKX row: ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm
    okx = [
        "1726128000000",
        "60000.0",
        "60500.0",
        "59800.0",
        "60250.0",
        "1234.5",
        "74000000",
        "74000000",
        "1",
    ]
    row = _okx_row_to_binance(okx)
    assert row[0] == 1726128000000  # open_time (int ms)
    assert row[1] == "60000.0"  # open
    assert row[2] == "60500.0"  # high
    assert row[3] == "59800.0"  # low
    assert row[4] == "60250.0"  # close
    assert row[5] == "1234.5"  # volume
    # index 9 = taker_buy_volume = volume / 2 (neutral CVD)
    assert float(row[9]) == 1234.5 / 2
    assert len(row) == 10


def test_okx_row_to_binance_open_time_is_int() -> None:
    okx = ["1726128000000", "1", "2", "0.5", "1.5", "10", "x", "y", "1"]
    row = _okx_row_to_binance(okx)
    assert isinstance(row[0], int)


def test_to_okx_inst_id_maps_usdt_perps() -> None:
    assert _to_okx_inst_id("BTCUSDT") == "BTC-USDT-SWAP"
    assert _to_okx_inst_id("ETHUSDT") == "ETH-USDT-SWAP"
    assert _to_okx_inst_id("SOLUSDT") == "SOL-USDT-SWAP"


def test_to_okx_inst_id_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="cannot map"):
        _to_okx_inst_id("FOOBAR")


def test_to_okx_bar_uses_utc_daily() -> None:
    assert _to_okx_bar("15m") == "15m"
    assert _to_okx_bar("1h") == "1H"
    assert _to_okx_bar("4h") == "4H"
    # 1Dutc so daily candle open aligns to 00:00 UTC like Binance open_time
    assert _to_okx_bar("1d") == "1Dutc"


def test_to_okx_bar_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        _to_okx_bar("2h")

"""Tests for the OKX market-data adapter (utils/okx_client.py)."""

from utils.okx_client import _okx_row_to_binance


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

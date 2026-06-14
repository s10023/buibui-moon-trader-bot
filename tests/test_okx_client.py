"""Tests for the OKX market-data adapter (utils/okx_client.py)."""

from typing import Any

import pytest

from utils.okx_client import (
    OKXClient,
    _okx_row_to_binance,
    _to_okx_bar,
    _to_okx_inst_id,
)


class _FakeResp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    """Returns one page of OKX candles then an empty page (end of history)."""

    def __init__(self, pages: list[list[list[str]]]) -> None:
        self._pages = pages
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any], timeout: float) -> _FakeResp:
        self.calls.append(params)
        data = self._pages.pop(0) if self._pages else []
        return _FakeResp({"code": "0", "msg": "", "data": data})


def _candle(ts: int, confirm: str = "1") -> list[str]:
    return [str(ts), "1", "2", "0.5", "1.5", "100", "x", "y", confirm]


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
    # 1Wutc likewise anchors the weekly open to UTC (Binance weekly = Mon 00:00 UTC)
    assert _to_okx_bar("1w") == "1Wutc"


def test_to_okx_bar_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        _to_okx_bar("2h")


def test_futures_klines_filters_by_start_sorts_ascending_drops_unconfirmed() -> None:
    # newest-first page: ts 3000 (unconfirmed), 2000, 1000
    session = _FakeSession([[_candle(3000, "0"), _candle(2000), _candle(1000)]])
    client = OKXClient(session=session)
    df = client.futures_klines("BTCUSDT", "1h", start_time=2000, limit=1000)
    # 3000 dropped (unconfirmed); 1000 dropped (< start); only 2000 kept
    assert list(df["open_time"]) == [2000]
    assert df["symbol"].iloc[0] == "BTCUSDT"
    assert df["timeframe"].iloc[0] == "1h"  # stored as the Binance tf, not OKX bar
    assert float(df["taker_buy_volume"].iloc[0]) == 100 / 2


def test_futures_klines_paginates_until_start_reached() -> None:
    # page 1 newest: 3000,2000 ; page 2: 1000 ; want start=1000 -> all 3
    session = _FakeSession([[_candle(3000), _candle(2000)], [_candle(1000)], []])
    client = OKXClient(session=session)
    df = client.futures_klines("BTCUSDT", "1h", start_time=1000, limit=1000)
    assert list(df["open_time"]) == [1000, 2000, 3000]
    # second call must carry an `after` cursor = oldest ts of page 1
    assert session.calls[1]["after"] == "2000"

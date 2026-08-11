from typing import Any

import pandas as pd

from analytics.cvd.fetch import (
    fetch_spot_ohlcv_daily,
    fetch_trading_spot_symbols,
    spot_symbol_for,
)

DAY = 86_400_000


def _kline(open_time: int, close: float, vol: float, tbv: float) -> list[Any]:
    """A Binance kline row. Field 5 is volume, field 9 is taker buy base volume."""
    return [
        open_time,
        "100.0",
        "110.0",
        "90.0",
        str(close),
        str(vol),
        open_time + DAY - 1,
        "0",
        0,
        str(tbv),
        "0",
        "0",
    ]


class _FakeGet:
    def __init__(self, pages: list[list[Any]]) -> None:
        self.pages = pages
        self.urls: list[str] = []

    def __call__(self, url: str) -> Any:
        self.urls.append(url)
        return self.pages.pop(0) if self.pages else []


def test_symbol_map_handles_the_three_special_cases() -> None:
    assert spot_symbol_for("BTCUSDT") == "BTCUSDT"
    assert spot_symbol_for("1000PEPEUSDT") == "PEPEUSDT"
    assert spot_symbol_for("HYPEUSDT") is None
    assert spot_symbol_for("VVVUSDT") is None


def test_fetch_returns_volume_and_taker_buy_columns() -> None:
    get = _FakeGet([[_kline(0, 101.0, 10.0, 6.0)]])
    df = fetch_spot_ohlcv_daily("BTCUSDT", 0, get=get)
    assert list(df.columns) == [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "taker_buy_volume",
    ]
    assert df["volume"].iloc[0] == 10.0
    assert df["taker_buy_volume"].iloc[0] == 6.0


def test_request_shape_is_the_keyless_spot_endpoint() -> None:
    get = _FakeGet([[_kline(0, 101.0, 10.0, 6.0)]])
    fetch_spot_ohlcv_daily("ETHUSDT", 123, get=get)
    url = get.urls[0]
    assert url.startswith("https://api.binance.com/api/v3/klines?")
    assert "symbol=ETHUSDT" in url
    assert "interval=1d" in url
    assert "startTime=123" in url
    assert "limit=1000" in url
    assert "apiKey" not in url and "signature" not in url


def test_paging_advances_the_cursor_past_the_last_bar() -> None:
    full = [_kline(i * DAY, 1.0, 1.0, 1.0) for i in range(1000)]
    get = _FakeGet([full, [_kline(1000 * DAY, 2.0, 1.0, 1.0)]])
    df = fetch_spot_ohlcv_daily("BTCUSDT", 0, get=get)
    assert len(df) == 1001
    assert f"startTime={1000 * DAY}" in get.urls[1]


def test_stale_cursor_page_terminates_instead_of_looping() -> None:
    """A full page whose last open_time does not advance must not loop forever."""
    stale = [_kline(0, 1.0, 1.0, 1.0) for _ in range(1000)]
    get = _FakeGet([stale] * 5)
    df = fetch_spot_ohlcv_daily("BTCUSDT", 10 * DAY, get=get)
    assert len(get.urls) == 1
    assert isinstance(df, pd.DataFrame)


def test_trading_symbols_excludes_non_trading_status() -> None:
    payload = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING"},
            {"symbol": "TONUSDT", "status": "BREAK"},
        ]
    }
    got = fetch_trading_spot_symbols(get=lambda _url: payload)
    assert "BTCUSDT" in got
    assert "TONUSDT" not in got

"""Tests for analytics/venue_fetch.py — keyless daily-close fetchers for H14.

Every test injects a fake ``get`` so the suite never touches the network. A
network-free suite structurally cannot catch a wrong argument to a real
endpoint, so several tests assert on the *shape* of the request URL (host,
path, granularity/interval, paging params) rather than only on the parsed
rows that come back.
"""

from typing import Any

import pandas as pd

from analytics.venue_fetch import fetch_binance_spot_daily, fetch_coinbase_daily

DAY = 86_400_000


def test_coinbase_pages_and_normalizes() -> None:
    """A range under the 300-day cap is a single request; the response (newest
    first, per the Coinbase Exchange candles contract) is normalized to
    ascending open_time/close.
    """
    calls: list[str] = []

    def fake_get(url: str) -> Any:
        calls.append(url)
        # Coinbase returns newest-first; both 2020-01-01 and 2020-01-02
        # candles come back in ONE response since the whole range fits
        # under the 300-day/request cap.
        return [
            [1_577_923_200, 1.0, 2.0, 1.5, 7_100.0, 10.0],  # 2020-01-02
            [1_577_836_800, 1.0, 2.0, 1.5, 7_000.0, 10.0],  # 2020-01-01
        ]

    df = fetch_coinbase_daily(
        "BTC-USD", 1_577_836_800_000, 1_578_009_600_000, get=fake_get
    )
    assert list(df.columns) == ["open_time", "close"]
    assert list(df["open_time"]) == [1_577_836_800_000, 1_577_923_200_000]
    assert list(df["close"]) == [7_000.0, 7_100.0]
    assert len(calls) == 1, "a 2-day range fits in one 300-day-capped request"


def test_coinbase_request_shape() -> None:
    """The request URL must carry the right host, path, product, granularity,
    and ISO start/end — a network-free suite can't otherwise catch a wrong
    query param reaching the real endpoint.
    """
    seen: list[str] = []

    def fake_get(url: str) -> Any:
        seen.append(url)
        return []

    fetch_coinbase_daily("BTC-USD", 1_577_836_800_000, 1_578_009_600_000, get=fake_get)

    assert len(seen) == 1
    url = seen[0]
    assert url.startswith("https://api.exchange.coinbase.com/products/BTC-USD/candles?")
    assert "granularity=86400" in url
    assert "start=2020-01-01" in url
    assert "end=2020-01-03" in url


def test_coinbase_pages_across_300_day_cap_and_dedupes() -> None:
    """A range spanning more than 300 days must page into multiple requests,
    and an overlapping boundary candle returned by both pages must collapse
    to one row.
    """
    start_ms = 1_577_836_800_000  # 2020-01-01
    end_ms = start_ms + 400 * DAY  # 2021-02-04 -> two windows: 300d + 100d

    def fake_get(url: str) -> Any:
        if "start=2020-01-01&end=2020-10-27" in url:
            return [
                [1_577_836_800, 1.0, 2.0, 1.5, 100.0, 1.0],  # 2020-01-01
                [1_603_756_800, 1.0, 2.0, 1.5, 200.0, 1.0],  # 2020-10-27 (boundary)
            ]
        if "start=2020-10-27&end=2021-02-04" in url:
            return [
                [
                    1_603_756_800,
                    1.0,
                    2.0,
                    1.5,
                    200.0,
                    1.0,
                ],  # 2020-10-27 (same boundary candle, duplicate)
                [1_612_396_800, 1.0, 2.0, 1.5, 300.0, 1.0],  # 2021-02-04
            ]
        raise AssertionError(f"unexpected url: {url}")

    df = fetch_coinbase_daily("BTC-USD", start_ms, end_ms, get=fake_get)
    assert list(df["open_time"]) == [
        1_577_836_800_000,
        1_603_756_800_000,
        1_612_396_800_000,
    ]
    assert list(df["close"]) == [100.0, 200.0, 300.0]


def test_binance_stops_when_page_not_full() -> None:
    def fake_get(url: str) -> Any:
        return [[1_577_836_800_000, "1", "2", "0.5", "7000.0", "1"]]

    df = fetch_binance_spot_daily("BTCUSDT", 1_577_836_800_000, get=fake_get)
    assert list(df["close"]) == [7000.0]
    assert df["open_time"].dtype.kind == "i"


def test_binance_request_shape() -> None:
    """Assert host/path/symbol/interval/startTime/limit reach the URL, not
    just that the parsed rows come back normalized.
    """
    seen: list[str] = []

    def fake_get(url: str) -> Any:
        seen.append(url)
        return []

    fetch_binance_spot_daily("BTCUSDT", 1_577_836_800_000, get=fake_get)

    assert len(seen) == 1
    url = seen[0]
    assert url.startswith("https://api.binance.com/api/v3/klines?")
    assert "symbol=BTCUSDT" in url
    assert "interval=1d" in url
    assert "startTime=1577836800000" in url
    assert "limit=1000" in url


def test_binance_pages_full_page_and_advances_starttime() -> None:
    """A full page (len == limit) must trigger a second request whose
    startTime is the prior page's last open_time + one day.
    """
    seen: list[str] = []
    start_ms = 1_577_836_800_000
    last_open_first_page = start_ms + 999 * DAY

    def fake_get(url: str) -> Any:
        seen.append(url)
        if len(seen) == 1:
            return [
                [start_ms + i * DAY, "1", "2", "0.5", "100.0", "1"] for i in range(1000)
            ]
        return [[last_open_first_page + DAY, "1", "2", "0.5", "200.0", "1"]]

    df = fetch_binance_spot_daily("BTCUSDT", start_ms, get=fake_get)
    assert len(seen) == 2
    assert f"startTime={last_open_first_page + DAY}" in seen[1]
    assert len(df) == 1001
    assert df["close"].iloc[-1] == 200.0


def test_binance_stops_when_full_page_cursor_does_not_advance() -> None:
    """Guard against an infinite loop: if the API ever returns a full page
    whose last open_time does not advance past the requested cursor, the
    loop must break instead of re-requesting forever. Simulated with a
    'stuck' feed that always reports a candle one day behind the cursor it
    was asked for, so the naive next-cursor computation never clears the
    cursor already used.
    """
    call_count = 0
    start_ms = 1_577_836_800_000
    stuck_open_time = start_ms - DAY

    def fake_get(url: str) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count > 3:
            raise AssertionError(
                "loop did not terminate: non-advancing-cursor guard failed"
            )
        return [[stuck_open_time, "1", "2", "0.5", "7000.0", "1"] for _ in range(1000)]

    df = fetch_binance_spot_daily("BTCUSDT", start_ms, get=fake_get)
    assert call_count == 1
    assert len(df) == 1  # 1000 identical open_times de-duplicate to one row


def test_returned_frames_are_plain_pandas_dataframes() -> None:
    def fake_get(url: str) -> Any:
        return []

    cb = fetch_coinbase_daily("BTC-USD", 0, DAY, get=fake_get)
    bn = fetch_binance_spot_daily("BTCUSDT", 0, get=fake_get)
    assert isinstance(cb, pd.DataFrame)
    assert isinstance(bn, pd.DataFrame)
    assert list(cb.columns) == ["open_time", "close"]
    assert list(bn.columns) == ["open_time", "close"]

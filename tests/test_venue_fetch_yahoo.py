"""Yahoo chart parsing — network-free via an injected getter."""

from __future__ import annotations

import urllib.error
from typing import Any

import pytest

from analytics.venue_fetch import YahooFetchError, fetch_yahoo_daily

_START_MS = 1_546_300_800_000  # 2019-01-01
_END_MS = 1_546_646_400_000  # 2019-01-05


def _payload(ts: list[int], close: list[float | None]) -> dict[str, Any]:
    return {
        "chart": {
            "result": [{"timestamp": ts, "indicators": {"quote": [{"close": close}]}}],
            "error": None,
        }
    }


def test_parses_seconds_into_millisecond_open_times() -> None:
    got = fetch_yahoo_daily(
        "JPY=X",
        _START_MS,
        _END_MS,
        get=lambda url: _payload([1_546_300_800, 1_546_387_200], [109.5, 109.7]),
    )
    assert list(got["open_time"]) == [1_546_300_800_000, 1_546_387_200_000]
    assert list(got["close"]) == [109.5, 109.7]
    assert got["open_time"].dtype == "int64"


def test_drops_null_closes_rather_than_filling() -> None:
    got = fetch_yahoo_daily(
        "JPY=X",
        _START_MS,
        _END_MS,
        get=lambda url: _payload([1, 2, 3], [109.5, None, 109.9]),
    )
    assert list(got["close"]) == [109.5, 109.9]


def test_requests_seconds_not_milliseconds() -> None:
    seen: list[str] = []

    def _get(url: str) -> Any:
        seen.append(url)
        return _payload([1], [109.5])

    fetch_yahoo_daily("JPY=X", _START_MS, _END_MS, get=_get)
    assert f"period1={_START_MS // 1000}" in seen[0]
    assert f"period2={_END_MS // 1000}" in seen[0]
    assert "interval=1d" in seen[0]


def test_rate_limit_raises_named_error_not_empty_frame() -> None:
    """A 429 must be loud. An empty frame would read as 'no data' downstream."""

    def _get(url: str) -> Any:
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]

    with pytest.raises(YahooFetchError, match="429"):
        fetch_yahoo_daily("JPY=X", _START_MS, _END_MS, get=_get)


def test_api_level_error_raises() -> None:
    def _get(url: str) -> Any:
        return {"chart": {"result": None, "error": {"description": "No data found"}}}

    with pytest.raises(YahooFetchError, match="No data found"):
        fetch_yahoo_daily("BOGUS=X", _START_MS, _END_MS, get=_get)


def test_symbol_is_url_quoted() -> None:
    seen: list[str] = []

    def _get(url: str) -> Any:
        seen.append(url)
        return _payload([1], [1.0])

    fetch_yahoo_daily("JPY=X", _START_MS, _END_MS, get=_get)
    assert "JPY%3DX" in seen[0]

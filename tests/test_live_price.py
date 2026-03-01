"""Tests for monitor/live_price.py."""

from typing import Any

from monitor.live_price import _handle_ws_msg
from utils.live_store import LiveDataStore


def _miniticker_msg(symbol: str, last: str, open_24h: str) -> dict[str, Any]:
    return {
        "stream": f"{symbol.lower()}@miniTicker",
        "data": {
            "e": "24hrMiniTicker",
            "s": symbol,
            "c": last,
            "o": open_24h,
        },
    }


class TestHandleWsMsg:
    def test_valid_miniticker_updates_store(self) -> None:
        store = LiveDataStore()
        _handle_ws_msg(_miniticker_msg("BTCUSDT", "67000.00", "65000.00"), store)
        result = store.snapshot(["BTCUSDT"])
        assert result.data["BTCUSDT"].ticker is not None
        assert result.data["BTCUSDT"].ticker.last_price == 67000.0
        assert result.ws_connected is True

    def test_valid_msg_sets_ws_connected(self) -> None:
        store = LiveDataStore()
        _handle_ws_msg(_miniticker_msg("BTCUSDT", "100.00", "90.00"), store)
        assert store.snapshot(["BTCUSDT"]).ws_connected is True

    def test_error_msg_sets_disconnected(self) -> None:
        store = LiveDataStore()
        store.set_ws_status(connected=True)
        _handle_ws_msg({"e": "error", "m": "stream error"}, store)
        assert store.snapshot(["BTCUSDT"]).ws_connected is False

    def test_error_msg_does_not_write_ticker(self) -> None:
        store = LiveDataStore()
        _handle_ws_msg({"e": "error", "m": "stream error"}, store)
        assert store.snapshot(["BTCUSDT"]).data["BTCUSDT"].ticker is None

    def test_unknown_event_type_is_ignored(self) -> None:
        store = LiveDataStore()
        _handle_ws_msg({"data": {"e": "trade", "s": "BTCUSDT"}}, store)
        assert store.snapshot(["BTCUSDT"]).data["BTCUSDT"].ticker is None

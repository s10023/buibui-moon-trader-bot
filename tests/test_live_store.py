"""Tests for utils/live_store.py."""

import threading
from datetime import datetime

from utils.live_store import LiveDataStore


class TestUpdateTicker:
    def test_computes_change_24h_correctly(self) -> None:
        store = LiveDataStore()
        store.update_ticker("BTCUSDT", last=110.0, open_24h=100.0)
        result = store.snapshot(["BTCUSDT"])
        assert result.data["BTCUSDT"].ticker is not None
        assert result.data["BTCUSDT"].ticker.last_price == 110.0
        assert abs(result.data["BTCUSDT"].ticker.change_24h - 10.0) < 0.001
        assert result.ws_connected is True

    def test_zero_open_does_not_crash(self) -> None:
        store = LiveDataStore()
        store.update_ticker("BTCUSDT", last=100.0, open_24h=0.0)
        result = store.snapshot(["BTCUSDT"])
        assert result.data["BTCUSDT"].ticker is not None
        assert result.data["BTCUSDT"].ticker.change_24h == 0.0

    def test_sets_last_update_timestamp(self) -> None:
        store = LiveDataStore()
        assert store.snapshot(["BTCUSDT"]).last_update is None
        store.update_ticker("BTCUSDT", last=100.0, open_24h=100.0)
        assert isinstance(store.snapshot(["BTCUSDT"]).last_update, datetime)


class TestUpdateKlines:
    def test_stores_and_retrieves(self) -> None:
        store = LiveDataStore()
        store.update_klines(
            "BTCUSDT", open_15m=99.0, open_1h=95.0, open_4h=93.0, asia_open=90.0
        )
        result = store.snapshot(["BTCUSDT"])
        klines = result.data["BTCUSDT"].klines
        assert klines is not None
        assert klines.open_15m == 99.0
        assert klines.open_1h == 95.0
        assert klines.open_4h == 93.0
        assert klines.asia_open == 90.0

    def test_none_values_allowed(self) -> None:
        store = LiveDataStore()
        store.update_klines(
            "BTCUSDT", open_15m=None, open_1h=None, open_4h=None, asia_open=None
        )
        result = store.snapshot(["BTCUSDT"])
        assert result.data["BTCUSDT"].klines is not None


class TestSnapshot:
    def test_missing_symbol_returns_none_fields(self) -> None:
        store = LiveDataStore()
        result = store.snapshot(["BTCUSDT"])
        assert result.data["BTCUSDT"].ticker is None
        assert result.data["BTCUSDT"].klines is None

    def test_returns_copy_not_live_reference(self) -> None:
        store = LiveDataStore()
        store.update_ticker("BTCUSDT", last=100.0, open_24h=100.0)
        snap1 = store.snapshot(["BTCUSDT"])
        store.update_ticker("BTCUSDT", last=200.0, open_24h=100.0)
        snap2 = store.snapshot(["BTCUSDT"])
        assert snap1.data["BTCUSDT"].ticker is not None
        assert snap1.data["BTCUSDT"].ticker.last_price == 100.0
        assert snap2.data["BTCUSDT"].ticker is not None
        assert snap2.data["BTCUSDT"].ticker.last_price == 200.0

    def test_includes_ws_status_and_last_update(self) -> None:
        store = LiveDataStore()
        result = store.snapshot(["BTCUSDT"])
        assert result.ws_connected is False
        assert result.last_update is None


class TestSetWsStatus:
    def test_transitions(self) -> None:
        store = LiveDataStore()
        assert store.snapshot(["BTCUSDT"]).ws_connected is False
        store.set_ws_status(connected=True)
        assert store.snapshot(["BTCUSDT"]).ws_connected is True
        store.set_ws_status(connected=False)
        assert store.snapshot(["BTCUSDT"]).ws_connected is False


class TestThreadSafety:
    def test_concurrent_writes_do_not_corrupt(self) -> None:
        store = LiveDataStore()
        errors: list[Exception] = []

        def writer(symbol: str, price: float) -> None:
            try:
                for _ in range(200):
                    store.update_ticker(symbol, last=price, open_24h=price)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(f"SYM{i}", float(i * 10)))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []

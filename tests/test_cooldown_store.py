"""Tests for signals/cooldown_store.py — two-layer dedup store."""

import json
from typing import Any
from unittest.mock import patch

from signals.cooldown_store import CooldownStore


class TestCandleWatermark:
    def test_unknown_symbol_is_new_candle(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        assert store.is_new_candle("BTCUSDT", "1h", "fvg", 1_000) is True

    def test_mark_then_same_open_time_is_not_new(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "1h", "fvg", 1_000)
        assert store.is_new_candle("BTCUSDT", "1h", "fvg", 1_000) is False

    def test_older_open_time_is_not_new(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "1h", "fvg", 2_000)
        assert store.is_new_candle("BTCUSDT", "1h", "fvg", 1_000) is False

    def test_newer_open_time_is_new(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "1h", "fvg", 1_000)
        assert store.is_new_candle("BTCUSDT", "1h", "fvg", 2_000) is True

    def test_different_strategy_is_independent(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "1h", "fvg", 1_000)
        assert store.is_new_candle("BTCUSDT", "1h", "bos", 1_000) is True

    def test_different_symbol_is_independent(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "1h", "fvg", 1_000)
        assert store.is_new_candle("ETHUSDT", "1h", "fvg", 1_000) is True

    def test_different_timeframe_is_independent(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "1h", "fvg", 1_000)
        assert store.is_new_candle("BTCUSDT", "4h", "fvg", 1_000) is True


class TestCooldownTimer:
    def test_unknown_key_is_off_cooldown(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        assert store.is_off_cooldown("BTCUSDT", "fvg", "long") is True

    def test_active_cooldown_blocks_alert(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.set_cooldown("BTCUSDT", "fvg", "long", 9999.0)
        assert store.is_off_cooldown("BTCUSDT", "fvg", "long") is False

    def test_expired_cooldown_allows_alert(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.set_cooldown("BTCUSDT", "fvg", "long", -1.0)
        assert store.is_off_cooldown("BTCUSDT", "fvg", "long") is True

    def test_cooldown_expires_after_window_using_time_mock(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        base_time = 1_700_000_000.0
        with patch("signals.cooldown_store.time.time", return_value=base_time):
            store.set_cooldown("BTCUSDT", "fvg", "long", 300.0)

        with patch("signals.cooldown_store.time.time", return_value=base_time + 100.0):
            assert store.is_off_cooldown("BTCUSDT", "fvg", "long") is False

        with patch("signals.cooldown_store.time.time", return_value=base_time + 301.0):
            assert store.is_off_cooldown("BTCUSDT", "fvg", "long") is True

    def test_cooldown_direction_is_independent(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.set_cooldown("BTCUSDT", "fvg", "long", 9999.0)
        assert store.is_off_cooldown("BTCUSDT", "fvg", "short") is True

    def test_cooldown_symbol_is_independent(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.set_cooldown("BTCUSDT", "fvg", "long", 9999.0)
        assert store.is_off_cooldown("ETHUSDT", "fvg", "long") is True


class TestJsonPersistence:
    def test_watermark_persists_across_instances(self, tmp_path: Any) -> None:
        path = str(tmp_path / "state.json")
        store1 = CooldownStore(path)
        store1.mark_candle("BTCUSDT", "1h", "fvg", 5_000)

        store2 = CooldownStore(path)
        assert store2.is_new_candle("BTCUSDT", "1h", "fvg", 5_000) is False

    def test_cooldown_persists_across_instances(self, tmp_path: Any) -> None:
        path = str(tmp_path / "state.json")
        base_time = 1_700_000_000.0
        with patch("signals.cooldown_store.time.time", return_value=base_time):
            store1 = CooldownStore(path)
            store1.set_cooldown("BTCUSDT", "fvg", "long", 3600.0)

        store2 = CooldownStore(path)
        with patch("signals.cooldown_store.time.time", return_value=base_time + 100.0):
            assert store2.is_off_cooldown("BTCUSDT", "fvg", "long") is False

    def test_state_file_is_valid_json(self, tmp_path: Any) -> None:
        path = tmp_path / "state.json"
        store = CooldownStore(str(path))
        store.mark_candle("BTCUSDT", "1h", "fvg", 1_000)
        data = json.loads(path.read_text())
        assert "watermarks" in data
        assert "cooldowns" in data
        assert data["watermarks"]["BTCUSDT:1h:fvg"] == 1_000

    def test_missing_file_starts_empty(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "nonexistent.json"))
        assert store.is_new_candle("BTCUSDT", "1h", "fvg", 1_000) is True
        assert store.is_off_cooldown("BTCUSDT", "fvg", "long") is True

    def test_corrupted_file_starts_empty(self, tmp_path: Any) -> None:
        path = tmp_path / "state.json"
        path.write_text("not valid json {{{{")
        store = CooldownStore(str(path))
        assert store.is_new_candle("BTCUSDT", "1h", "fvg", 1_000) is True


class TestRecordAlert:
    def test_record_alert_marks_candle_and_sets_cooldown(self, tmp_path: Any) -> None:
        base_time = 1_700_000_000.0
        store = CooldownStore(str(tmp_path / "state.json"))
        with patch("signals.cooldown_store.time.time", return_value=base_time):
            store.record_alert("BTCUSDT", "1h", "fvg", "long", 9_000, 300.0)

        assert store.is_new_candle("BTCUSDT", "1h", "fvg", 9_000) is False
        with patch("signals.cooldown_store.time.time", return_value=base_time + 100.0):
            assert store.is_off_cooldown("BTCUSDT", "fvg", "long") is False

    def test_record_alert_persists_both_layers(self, tmp_path: Any) -> None:
        path = str(tmp_path / "state.json")
        base_time = 1_700_000_000.0
        store1 = CooldownStore(path)
        with patch("signals.cooldown_store.time.time", return_value=base_time):
            store1.record_alert("ETHUSDT", "4h", "bos", "short", 7_000, 600.0)

        store2 = CooldownStore(path)
        assert store2.is_new_candle("ETHUSDT", "4h", "bos", 7_000) is False
        with patch("signals.cooldown_store.time.time", return_value=base_time + 50.0):
            assert store2.is_off_cooldown("ETHUSDT", "bos", "short") is False

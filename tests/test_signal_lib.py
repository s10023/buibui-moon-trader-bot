"""Tests for signal_lib components — in-memory DuckDB, no real network calls."""

from typing import Any

from signals.alert_formatter import SignalEvent, format_signal_alert
from signals.cooldown_store import CooldownStore


class TestCooldownStore:
    def test_new_candle_returns_true_initially(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        assert store.is_new_candle("BTCUSDT", "4h", "fvg", 1000) is True

    def test_mark_candle_deduplicates_same_open_time(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "4h", "fvg", 1000)
        assert store.is_new_candle("BTCUSDT", "4h", "fvg", 1000) is False

    def test_newer_candle_passes_after_mark(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "4h", "fvg", 1000)
        assert store.is_new_candle("BTCUSDT", "4h", "fvg", 2000) is True

    def test_cooldown_blocks_when_active(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.set_cooldown("BTCUSDT", "fvg", "long", 9999.0)
        assert store.is_off_cooldown("BTCUSDT", "fvg", "long") is False

    def test_cooldown_allows_by_default(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        assert store.is_off_cooldown("BTCUSDT", "fvg", "long") is True

    def test_expired_cooldown_allows(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.set_cooldown("BTCUSDT", "fvg", "long", -1.0)  # already expired
        assert store.is_off_cooldown("BTCUSDT", "fvg", "long") is True

    def test_state_persists_across_instances(self, tmp_path: Any) -> None:
        path = str(tmp_path / "state.json")
        store1 = CooldownStore(path)
        store1.mark_candle("BTCUSDT", "4h", "fvg", 5000)
        store2 = CooldownStore(path)
        assert store2.is_new_candle("BTCUSDT", "4h", "fvg", 5000) is False

    def test_different_strategies_are_independent(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "4h", "fvg", 1000)
        assert store.is_new_candle("BTCUSDT", "4h", "bos", 1000) is True

    def test_different_symbols_are_independent(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "4h", "fvg", 1000)
        assert store.is_new_candle("ETHUSDT", "4h", "fvg", 1000) is True

    def test_corrupted_state_file_starts_fresh(self, tmp_path: Any) -> None:
        path = tmp_path / "state.json"
        path.write_text("not valid json")
        store = CooldownStore(str(path))
        assert store.is_new_candle("BTCUSDT", "4h", "fvg", 1000) is True


class TestFormatSignalAlert:
    def test_long_alert_contains_symbol_and_direction(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="4h",
            strategy="fvg",
            direction="long",
            reason="fvg_long@43200.00-43350.00",
            open_time=1700000000000,
            price=43260.0,
        )
        msg = format_signal_alert(event, sl_pct=0.02, tp_r=2.0)
        assert "BTCUSDT" in msg
        assert "fvg" in msg
        assert "LONG" in msg
        assert "43,260.00" in msg

    def test_short_alert_contains_symbol_and_direction(self) -> None:
        event = SignalEvent(
            symbol="ETHUSDT",
            timeframe="1h",
            strategy="bos",
            direction="short",
            reason="bos_short@2500.00",
            open_time=1700000000000,
            price=2500.0,
        )
        msg = format_signal_alert(event)
        assert "SHORT" in msg
        assert "ETHUSDT" in msg
        assert "bos" in msg

    def test_long_sl_is_below_price(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="4h",
            strategy="fvg",
            direction="long",
            reason="fvg_long@100.00-110.00",
            open_time=1700000000000,
            price=1000.0,
        )
        msg = format_signal_alert(event, sl_pct=0.02)
        # SL = 1000 * 0.98 = 980
        assert "980.00" in msg

    def test_short_sl_is_above_price(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="4h",
            strategy="fvg",
            direction="short",
            reason="fvg_short@110.00-100.00",
            open_time=1700000000000,
            price=1000.0,
        )
        msg = format_signal_alert(event, sl_pct=0.02)
        # SL = 1000 * 1.02 = 1020
        assert "1,020.00" in msg

    def test_tp_r_reflected_in_message(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="4h",
            strategy="fvg",
            direction="long",
            reason="fvg_long@100.00-110.00",
            open_time=1700000000000,
            price=1000.0,
        )
        msg = format_signal_alert(event, sl_pct=0.02, tp_r=3.0)
        assert "3.0x R" in msg

"""Tests for signal_lib components — in-memory DuckDB, no real network calls."""

from typing import Any
from unittest.mock import patch

from analytics.signal_runner import _parse_timeframe_secs, _secs_until_next_boundary
from signals.alert_formatter import (
    SignalEvent,
    format_confluence_alert,
    format_signal_alert,
)
from signals.cooldown_store import CooldownStore


class TestParseTimeframeSecs:
    def test_minutes(self) -> None:
        assert _parse_timeframe_secs("15m") == 900

    def test_hours(self) -> None:
        assert _parse_timeframe_secs("4h") == 14400

    def test_one_hour(self) -> None:
        assert _parse_timeframe_secs("1h") == 3600

    def test_days(self) -> None:
        assert _parse_timeframe_secs("1d") == 86400


class TestSecsUntilNextBoundary:
    def test_wakes_at_next_4h_boundary(self) -> None:
        # now = 14:02:00 UTC → next 4h boundary = 16:00:00 + 10s buffer
        now = 14 * 3600 + 2 * 60  # 50520s since midnight
        with patch("analytics.signal_runner.time.time", return_value=float(now)):
            secs, wake_ts = _secs_until_next_boundary(["4h"])
        expected = (16 * 3600 + 10) - now  # 7090s
        assert secs == expected
        assert wake_ts == 16 * 3600 + 10

    def test_picks_earliest_boundary_across_timeframes(self) -> None:
        # now = 14:02:00 → next 1h boundary = 15:00:10, next 4h = 16:00:10
        now = 14 * 3600 + 2 * 60
        with patch("analytics.signal_runner.time.time", return_value=float(now)):
            secs, wake_ts = _secs_until_next_boundary(["4h", "1h"])
        expected = (15 * 3600 + 10) - now  # 3490s — the 1h boundary wins
        assert secs == expected
        assert wake_ts == 15 * 3600 + 10

    def test_never_returns_negative(self) -> None:
        # now is exactly on a boundary + buffer — result should be a full interval away
        now = 4 * 3600 + 10  # exactly at 04:00:10
        with patch("analytics.signal_runner.time.time", return_value=float(now)):
            secs, _ = _secs_until_next_boundary(["4h"])
        assert secs >= 0.0


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

    def test_structural_sl_used_when_valid_long(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="5m",
            strategy="fvg",
            direction="long",
            reason="fvg_long@90.00-95.00",
            open_time=1700000000000,
            price=100.0,
            sl_price=90.0,  # structural: below gap_bot
        )
        msg = format_signal_alert(event, sl_pct=0.02)
        # Structural SL = 90.0, not pct-based 98.0
        assert "90.00" in msg
        assert "980.00" not in msg

    def test_structural_sl_used_when_valid_short(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="5m",
            strategy="fvg",
            direction="short",
            reason="fvg_short@110.00-105.00",
            open_time=1700000000000,
            price=100.0,
            sl_price=110.0,  # structural: above gap_top
        )
        msg = format_signal_alert(event, sl_pct=0.02)
        assert "110.00" in msg
        assert "1,020.00" not in msg

    def test_fallback_to_pct_when_sl_price_zero(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="4h",
            strategy="funding_reversion",
            direction="long",
            reason="funding_short_extreme@-0.0012",
            open_time=1700000000000,
            price=1000.0,
            sl_price=0.0,
        )
        msg = format_signal_alert(event, sl_pct=0.02)
        assert "980.00" in msg

    def test_context_appears_in_single_event_alert(self) -> None:
        event = SignalEvent(
            symbol="SOLUSDT",
            timeframe="5m",
            strategy="fvg",
            direction="long",
            reason="fvg_long@94.59-94.73",
            open_time=1700000000000,
            price=94.67,
            sl_price=94.59,
            context="Gap: 17-Nov 10:00 · 17-Nov 10:05 · 17-Nov 10:10",
        )
        msg = format_signal_alert(event)
        assert "Gap: 17-Nov 10:00" in msg

    def test_signal_time_shown_in_alert(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="4h",
            strategy="fvg",
            direction="long",
            reason="fvg_long@100.00-110.00",
            open_time=1700000000000,
            price=1000.0,
        )
        msg = format_signal_alert(event)
        assert "SGT" in msg


class TestFormatConfluenceAlert:
    def _make_event(
        self,
        strategy: str,
        direction: str = "long",
        price: float = 100.0,
        sl_price: float = 0.0,
        context: str = "",
    ) -> SignalEvent:
        return SignalEvent(
            symbol="BTCUSDT",
            timeframe="5m",
            strategy=strategy,
            direction=direction,
            reason=f"{strategy}_{direction}@test",
            open_time=1700000000000,
            price=price,
            sl_price=sl_price,
            context=context,
        )

    def test_single_event_uses_strategy_label(self) -> None:
        event = self._make_event("fvg", sl_price=90.0)
        msg = format_confluence_alert([event])
        assert "Strategy: `fvg`" in msg
        assert "Confluence" not in msg

    def test_two_events_shows_confluence_header(self) -> None:
        events = [
            self._make_event("fvg", sl_price=90.0),
            self._make_event("liquidity_sweep", sl_price=88.0),
        ]
        msg = format_confluence_alert(events)
        assert "Confluence: 2 strategies" in msg
        assert "fvg" in msg
        assert "liquidity_sweep" in msg

    def test_confluence_uses_tightest_sl_long(self) -> None:
        # Two longs: sl_price 90 and 85. Tightest = highest = 90.
        events = [
            self._make_event("fvg", sl_price=90.0),
            self._make_event("liquidity_sweep", sl_price=85.0),
        ]
        msg = format_confluence_alert(events)
        assert "90.00" in msg
        assert "85.00" not in msg

    def test_confluence_uses_tightest_sl_short(self) -> None:
        # Two shorts: sl_price 110 and 115. Tightest = lowest = 110.
        events = [
            self._make_event("fvg", direction="short", price=100.0, sl_price=110.0),
            self._make_event("bos", direction="short", price=100.0, sl_price=115.0),
        ]
        msg = format_confluence_alert(events)
        assert "110.00" in msg
        assert "115.00" not in msg

    def test_context_shown_in_confluence_line(self) -> None:
        events = [
            self._make_event(
                "fvg", sl_price=90.0, context="Gap: 17-Nov 10:00 · 10:05 · 10:10"
            ),
            self._make_event("liquidity_sweep", sl_price=88.0),
        ]
        msg = format_confluence_alert(events)
        assert "Gap: 17-Nov 10:00" in msg

    def test_confidence_stars_shown_for_single_signal(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="fvg",
            direction="long",
            reason="fvg_long@100.00-110.00",
            open_time=1700000000000,
            price=1000.0,
            confidence=4,
        )
        msg = format_signal_alert(event)
        assert "★★★★☆" in msg

    def test_no_stars_when_confidence_unset(self) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="fvg",
            direction="long",
            reason="fvg_long@100.00-110.00",
            open_time=1700000000000,
            price=1000.0,
            confidence=0,
        )
        msg = format_signal_alert(event)
        assert "★" not in msg

    def test_confidence_stars_shown_in_confluence_per_strategy(self) -> None:
        events = [
            SignalEvent(
                symbol="BTCUSDT",
                timeframe="15m",
                strategy="fvg",
                direction="long",
                reason="fvg_long@test",
                open_time=1700000000000,
                price=100.0,
                sl_price=90.0,
                confidence=4,
            ),
            SignalEvent(
                symbol="BTCUSDT",
                timeframe="15m",
                strategy="liquidity_sweep",
                direction="long",
                reason="liquidity_sweep_long@test",
                open_time=1700000000000,
                price=100.0,
                sl_price=88.0,
                confidence=4,
            ),
        ]
        msg = format_confluence_alert(events)
        assert msg.count("★★★★☆") == 2

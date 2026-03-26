"""Tests for signal_lib components — in-memory DuckDB, no real network calls."""

from typing import Any
from unittest.mock import patch

import duckdb
import pandas as pd

from analytics.data_store import init_schema
from analytics.signal_lib import (
    parse_timeframe_secs,
    run_scan_cycle,
    scan_symbol,
    secs_until_next_boundary,
)
from signals.alert_formatter import (
    SignalEvent,
    format_confluence_alert,
    format_signal_alert,
)
from signals.cooldown_store import CooldownStore


class TestParseTimeframeSecs:
    def test_minutes(self) -> None:
        assert parse_timeframe_secs("15m") == 900

    def test_hours(self) -> None:
        assert parse_timeframe_secs("4h") == 14400

    def test_one_hour(self) -> None:
        assert parse_timeframe_secs("1h") == 3600

    def test_days(self) -> None:
        assert parse_timeframe_secs("1d") == 86400


class TestSecsUntilNextBoundary:
    def test_wakes_at_next_4h_boundary(self) -> None:
        # now = 14:02:00 UTC → next 4h boundary = 16:00:00 + 10s buffer
        now = 14 * 3600 + 2 * 60  # 50520s since midnight
        with patch("analytics.signal_lib.time.time", return_value=float(now)):
            secs, wake_ts = secs_until_next_boundary(["4h"])
        expected = (16 * 3600 + 10) - now  # 7090s
        assert secs == expected
        assert wake_ts == 16 * 3600 + 10

    def test_picks_earliest_boundary_across_timeframes(self) -> None:
        # now = 14:02:00 → next 1h boundary = 15:00:10, next 4h = 16:00:10
        now = 14 * 3600 + 2 * 60
        with patch("analytics.signal_lib.time.time", return_value=float(now)):
            secs, wake_ts = secs_until_next_boundary(["4h", "1h"])
        expected = (15 * 3600 + 10) - now  # 3490s — the 1h boundary wins
        assert secs == expected
        assert wake_ts == 15 * 3600 + 10

    def test_never_returns_negative(self) -> None:
        # now is exactly on a boundary + buffer — result should be a full interval away
        now = 4 * 3600 + 10  # exactly at 04:00:10
        with patch("analytics.signal_lib.time.time", return_value=float(now)):
            secs, _ = secs_until_next_boundary(["4h"])
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
        assert "MYT" in msg


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
        assert "Strategy: <code>fvg</code>" in msg
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

    def test_confluence_uses_widest_sl_long(self) -> None:
        # Two longs: sl_price 90 and 85. Widest = lowest = 85 (most conservative).
        events = [
            self._make_event("fvg", sl_price=90.0),
            self._make_event("liquidity_sweep", sl_price=85.0),
        ]
        msg = format_confluence_alert(events)
        assert "85.00" in msg
        assert "90.00" not in msg

    def test_confluence_uses_widest_sl_short(self) -> None:
        # Two shorts: sl_price 110 and 115. Widest = highest = 115 (most conservative).
        events = [
            self._make_event("fvg", direction="short", price=100.0, sl_price=110.0),
            self._make_event("bos", direction="short", price=100.0, sl_price=115.0),
        ]
        msg = format_confluence_alert(events)
        assert "115.00" in msg
        assert "110.00" not in msg

    def test_min_sl_pct_enforced_long(self) -> None:
        # sl_price 99.9 is only 0.1% below price 100. min_sl_pct=0.01 → SL = 99.0.
        event = self._make_event("wick_fill", sl_price=99.9)
        msg = format_confluence_alert([event], min_sl_pct=0.01)
        assert "99.00" in msg
        assert "99.90" not in msg

    def test_min_sl_pct_enforced_short(self) -> None:
        # sl_price 100.1 is only 0.1% above price 100. min_sl_pct=0.01 → SL = 101.0.
        event = self._make_event(
            "wick_fill", direction="short", price=100.0, sl_price=100.1
        )
        msg = format_confluence_alert([event], min_sl_pct=0.01)
        assert "101.00" in msg
        assert "100.10" not in msg

    def test_min_sl_pct_not_applied_when_sl_already_wide_enough(self) -> None:
        # sl_price 90 is 10% below price 100. min_sl_pct=0.01 → no override.
        event = self._make_event("fvg", sl_price=90.0)
        msg = format_confluence_alert([event], min_sl_pct=0.01)
        assert "90.00" in msg

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


class TestRunScanCycleSecondaryMap:
    """Tests for secondary_map logic in run_scan_cycle."""

    def _make_empty_df(self) -> pd.DataFrame:
        return pd.DataFrame()

    def test_shared_secondary_fetched_once_for_two_primaries(
        self, tmp_path: Any
    ) -> None:
        """Two primaries sharing the same secondary → get_ohlcv called once for secondary."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))

        with (
            patch(
                "analytics.signal_lib.get_ohlcv", return_value=self._make_empty_df()
            ) as mock_get,
            patch(
                "analytics.signal_lib.get_funding_rates",
                return_value=self._make_empty_df(),
            ),
        ):
            run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT", "ETHUSDT"],
                timeframes=["4h"],
                strategies=["smt_divergence"],
                store=store,
                secondary_map={"BTCUSDT": "SOLUSDT", "ETHUSDT": "SOLUSDT"},
            )

        secondary_calls = [c for c in mock_get.call_args_list if c.args[1] == "SOLUSDT"]
        assert len(secondary_calls) == 1, (
            f"Expected 1 fetch for SOLUSDT/4h, got {len(secondary_calls)}"
        )

    def test_secondary_map_entry_for_absent_symbol_ignored(self, tmp_path: Any) -> None:
        """secondary_map entry for a symbol not in the scan list is silently ignored."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))

        with (
            patch(
                "analytics.signal_lib.get_ohlcv", return_value=self._make_empty_df()
            ) as mock_get,
            patch(
                "analytics.signal_lib.get_funding_rates",
                return_value=self._make_empty_df(),
            ),
        ):
            run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["smt_divergence"],
                store=store,
                # ETHUSDT is in the map but NOT in symbols — its secondary should not be fetched
                secondary_map={"BTCUSDT": "SOLUSDT", "ETHUSDT": "BNBUSDT"},
            )

        solusdt_calls = [c for c in mock_get.call_args_list if c.args[1] == "SOLUSDT"]
        bnbusdt_calls = [c for c in mock_get.call_args_list if c.args[1] == "BNBUSDT"]
        assert len(solusdt_calls) == 1
        assert len(bnbusdt_calls) == 0, (
            "BNBUSDT (secondary for ETHUSDT which is not scanned) should not be fetched"
        )


class TestDayFilter:
    """Tests for the day_filter param in scan_symbol and run_scan_cycle.

    Timestamps used (all UTC):
      Monday    2024-01-01 00:00:00 UTC  → 1704067200000 ms  (weekday 0)
      Wednesday 2024-01-03 00:00:00 UTC  → 1704240000000 ms  (weekday 2)
      Friday    2024-01-05 00:00:00 UTC  → 1704412800000 ms  (weekday 4)
      Saturday  2024-01-06 00:00:00 UTC  → 1704499200000 ms  (weekday 5)
    """

    # Pre-computed UTC timestamps (ms)
    _MONDAY_MS = 1704067200000
    _WEDNESDAY_MS = 1704240000000
    _FRIDAY_MS = 1704412800000
    _SATURDAY_MS = 1704499200000

    def _make_ohlcv(self, open_time_ms: int) -> pd.DataFrame:
        """Minimal OHLCV DataFrame with 4 rows; second-to-last row has the given
        open_time (the latest *closed* candle); the final row is the forming candle."""
        rows = [
            {
                "open_time": open_time_ms - 2000,
                "open": 100.0,
                "high": 105.0,
                "low": 98.0,
                "close": 102.0,
                "volume": 1.0,
            },
            {
                "open_time": open_time_ms - 1000,
                "open": 102.0,
                "high": 106.0,
                "low": 100.0,
                "close": 103.0,
                "volume": 1.0,
            },
            {
                "open_time": open_time_ms,
                "open": 103.0,
                "high": 107.0,
                "low": 101.0,
                "close": 104.0,
                "volume": 1.0,
            },
            {
                "open_time": open_time_ms + 1000,
                "open": 104.0,
                "high": 104.5,
                "low": 103.5,
                "close": 104.2,
                "volume": 0.1,
            },
        ]
        return pd.DataFrame(rows)

    def _make_signals_df(self, open_time_ms: int) -> pd.DataFrame:
        """Minimal signals DataFrame that matches the latest candle."""
        return pd.DataFrame(
            [
                {
                    "open_time": open_time_ms,
                    "direction": "long",
                    "reason": "fvg_long@100.00-102.00",
                    "sl_price": 98.0,
                    "context": "",
                }
            ]
        )

    def test_day_filter_false_passes_monday_signal(self) -> None:
        """With day_filter="off" (default), Monday signals are not suppressed."""
        ohlcv = self._make_ohlcv(self._MONDAY_MS)
        signals_df = self._make_signals_df(self._MONDAY_MS)

        with (
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {
                    "fvg": {
                        "detector": lambda df: signals_df,
                        "confidence": 4,
                    }
                },
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )(),
                },
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                day_filter="off",
            )

        assert len(events) == 1

    def test_day_filter_true_suppresses_monday_signal(self) -> None:
        """With day_filter="tue_thu", a signal on Monday is filtered out."""
        ohlcv = self._make_ohlcv(self._MONDAY_MS)
        signals_df = self._make_signals_df(self._MONDAY_MS)

        with (
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {
                    "fvg": {
                        "detector": lambda df: signals_df,
                        "confidence": 4,
                    }
                },
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )(),
                },
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                day_filter="tue_thu",
            )

        assert len(events) == 0

    def test_day_filter_true_suppresses_friday_signal(self) -> None:
        """With day_filter="tue_thu", a signal on Friday is filtered out."""
        ohlcv = self._make_ohlcv(self._FRIDAY_MS)
        signals_df = self._make_signals_df(self._FRIDAY_MS)

        with (
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {
                    "fvg": {
                        "detector": lambda df: signals_df,
                        "confidence": 4,
                    }
                },
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )(),
                },
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                day_filter="tue_thu",
            )

        assert len(events) == 0

    def test_day_filter_true_passes_wednesday_signal(self) -> None:
        """With day_filter="tue_thu", a signal on Wednesday is NOT suppressed."""
        ohlcv = self._make_ohlcv(self._WEDNESDAY_MS)
        signals_df = self._make_signals_df(self._WEDNESDAY_MS)

        with (
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {
                    "fvg": {
                        "detector": lambda df: signals_df,
                        "confidence": 4,
                    }
                },
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )(),
                },
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                day_filter="tue_thu",
            )

        assert len(events) == 1

    def test_day_filter_weekdays_passes_friday_signal(self) -> None:
        """With day_filter="weekdays", a signal on Friday is NOT suppressed."""
        ohlcv = self._make_ohlcv(self._FRIDAY_MS)
        signals_df = self._make_signals_df(self._FRIDAY_MS)

        with (
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {
                    "fvg": {
                        "detector": lambda df: signals_df,
                        "confidence": 4,
                    }
                },
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )(),
                },
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                day_filter="weekdays",
            )

        assert len(events) == 1

    def test_day_filter_weekdays_suppresses_saturday_signal(self) -> None:
        """With day_filter="weekdays", a signal on Saturday is suppressed."""
        ohlcv = self._make_ohlcv(self._SATURDAY_MS)
        signals_df = self._make_signals_df(self._SATURDAY_MS)

        with (
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {
                    "fvg": {
                        "detector": lambda df: signals_df,
                        "confidence": 4,
                    }
                },
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )(),
                },
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                day_filter="weekdays",
            )

        assert len(events) == 0

    def test_run_scan_cycle_day_filter_propagated(self, tmp_path: Any) -> None:
        """day_filter="tue_thu" passed to run_scan_cycle suppresses Monday signals."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))

        monday_ohlcv = self._make_ohlcv(self._MONDAY_MS)
        monday_signals = self._make_signals_df(self._MONDAY_MS)

        with (
            patch("analytics.signal_lib.get_ohlcv", return_value=monday_ohlcv),
            patch(
                "analytics.signal_lib.get_funding_rates", return_value=pd.DataFrame()
            ),
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {
                    "fvg": {
                        "detector": lambda df: monday_signals,
                        "confidence": 4,
                    }
                },
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )(),
                },
            ),
        ):
            alerts = run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
                day_filter="tue_thu",
            )

        assert alerts == [], (
            "Monday signals should be suppressed with day_filter=tue_thu"
        )

    def test_run_scan_cycle_day_filter_false_default_passes_monday(
        self, tmp_path: Any
    ) -> None:
        """day_filter defaults to "off" — Monday signals reach the alert stage."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))

        monday_ohlcv = self._make_ohlcv(self._MONDAY_MS)
        monday_signals = self._make_signals_df(self._MONDAY_MS)

        with (
            patch("analytics.signal_lib.get_ohlcv", return_value=monday_ohlcv),
            patch(
                "analytics.signal_lib.get_funding_rates", return_value=pd.DataFrame()
            ),
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {
                    "fvg": {
                        "detector": lambda df: monday_signals,
                        "confidence": 4,
                    }
                },
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )(),
                },
            ),
        ):
            alerts = run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
                day_filter="off",
            )

        assert len(alerts) == 1, "Monday signals should pass when day_filter=off"


class TestSMTTrendFilter:
    """Tests for the smt_trend_filter param in scan_symbol.

    Verifies that scan_symbol forwards smt_trend_filter=1/0 to the smt_divergence
    detector as the trend_filter kwarg, and does NOT forward it for other strategies.
    """

    _OPEN_TIME_MS = 1704240000000  # Wednesday 2024-01-03 — passes day_filter

    def _make_ohlcv(self) -> pd.DataFrame:
        rows = [
            {
                "open_time": self._OPEN_TIME_MS - 2000,
                "open": 100.0,
                "high": 105.0,
                "low": 98.0,
                "close": 102.0,
                "volume": 1.0,
            },
            {
                "open_time": self._OPEN_TIME_MS - 1000,
                "open": 102.0,
                "high": 106.0,
                "low": 100.0,
                "close": 103.0,
                "volume": 1.0,
            },
            {
                "open_time": self._OPEN_TIME_MS,
                "open": 103.0,
                "high": 107.0,
                "low": 101.0,
                "close": 104.0,
                "volume": 1.0,
            },
            {
                "open_time": self._OPEN_TIME_MS + 1000,
                "open": 104.0,
                "high": 104.5,
                "low": 103.5,
                "close": 104.2,
                "volume": 0.1,
            },
        ]
        return pd.DataFrame(rows)

    def _make_signals_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "open_time": self._OPEN_TIME_MS,
                    "direction": "short",
                    "reason": "smt_bearish@107.00",
                    "sl_price": 107.0,
                    "context": "",
                }
            ]
        )

    def test_smt_trend_filter_1_forwarded_to_detector(self) -> None:
        """scan_symbol passes trend_filter=1 to smt_divergence detector."""
        ohlcv = self._make_ohlcv()
        signals_df = self._make_signals_df()
        secondary_df = self._make_ohlcv()

        received_kwargs: dict[str, Any] = {}

        def mock_detector(
            primary: pd.DataFrame, secondary: pd.DataFrame, **kwargs: Any
        ) -> pd.DataFrame:
            received_kwargs.update(kwargs)
            return signals_df

        with (
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {
                    "smt_divergence": {
                        "detector": mock_detector,
                        "confidence": 5,
                    }
                },
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "smt_divergence": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": True},
                    )(),
                },
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["smt_divergence"],
                secondary_df=secondary_df,
                smt_trend_filter=1,
            )

        assert received_kwargs.get("trend_filter") == 1
        assert len(events) == 1

    def test_smt_trend_filter_0_forwarded_to_detector(self) -> None:
        """scan_symbol passes trend_filter=0 when smt_trend_filter=0."""
        ohlcv = self._make_ohlcv()
        signals_df = self._make_signals_df()
        secondary_df = self._make_ohlcv()

        received_kwargs: dict[str, Any] = {}

        def mock_detector(
            primary: pd.DataFrame, secondary: pd.DataFrame, **kwargs: Any
        ) -> pd.DataFrame:
            received_kwargs.update(kwargs)
            return signals_df

        with (
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {
                    "smt_divergence": {
                        "detector": mock_detector,
                        "confidence": 5,
                    }
                },
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "smt_divergence": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": True},
                    )(),
                },
            ),
        ):
            scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["smt_divergence"],
                secondary_df=secondary_df,
                smt_trend_filter=0,
            )

        assert received_kwargs.get("trend_filter") == 0

    def test_smt_trend_filter_default_is_1(self) -> None:
        """smt_trend_filter defaults to 1 when not specified."""
        ohlcv = self._make_ohlcv()
        signals_df = self._make_signals_df()
        secondary_df = self._make_ohlcv()

        received_kwargs: dict[str, Any] = {}

        def mock_detector(
            primary: pd.DataFrame, secondary: pd.DataFrame, **kwargs: Any
        ) -> pd.DataFrame:
            received_kwargs.update(kwargs)
            return signals_df

        with (
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {
                    "smt_divergence": {
                        "detector": mock_detector,
                        "confidence": 5,
                    }
                },
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "smt_divergence": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": True},
                    )(),
                },
            ),
        ):
            scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["smt_divergence"],
                secondary_df=secondary_df,
                # smt_trend_filter not specified — should default to 1
            )

        assert received_kwargs.get("trend_filter") == 1

    def test_non_smt_strategy_does_not_receive_trend_filter(self) -> None:
        """trend_filter kwarg is NOT passed to non-smt_divergence detectors."""
        ohlcv = self._make_ohlcv()
        received_kwargs: dict[str, Any] = {}

        def mock_detector(df: pd.DataFrame, **kwargs: Any) -> pd.DataFrame:
            received_kwargs.update(kwargs)
            return self._make_signals_df()

        with (
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {
                    "fvg": {
                        "detector": mock_detector,
                        "confidence": 4,
                    }
                },
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )(),
                },
            ),
        ):
            scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                smt_trend_filter=1,
            )

        assert "trend_filter" not in received_kwargs


class TestStrategyTimeframes:
    """Tests for the strategy_timeframes param in scan_symbol.

    Verifies that strategies are skipped when the current TF is not in their
    allow-list, and still run when the TF is allowed or no restriction exists.
    """

    _OPEN_TIME_MS = 1704240000000  # Wednesday 2024-01-03 — passes day_filter

    def _make_ohlcv(self) -> pd.DataFrame:
        rows = [
            {
                "open_time": self._OPEN_TIME_MS - 2000,
                "open": 100.0,
                "high": 105.0,
                "low": 98.0,
                "close": 102.0,
                "volume": 1.0,
            },
            {
                "open_time": self._OPEN_TIME_MS - 1000,
                "open": 102.0,
                "high": 106.0,
                "low": 100.0,
                "close": 103.0,
                "volume": 1.0,
            },
            {
                "open_time": self._OPEN_TIME_MS,
                "open": 103.0,
                "high": 107.0,
                "low": 101.0,
                "close": 104.0,
                "volume": 1.0,
            },
            {
                "open_time": self._OPEN_TIME_MS + 1000,
                "open": 104.0,
                "high": 104.5,
                "low": 103.5,
                "close": 104.2,
                "volume": 0.1,
            },
        ]
        return pd.DataFrame(rows)

    def _make_signals_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "open_time": self._OPEN_TIME_MS,
                    "direction": "long",
                    "reason": "fvg_long@104.00",
                    "sl_price": 98.0,
                    "context": "",
                }
            ]
        )

    def _mock_registry(self, strategy: str) -> dict[str, Any]:
        signals_df = self._make_signals_df()
        return {
            strategy: {
                "detector": lambda df: signals_df,
                "confidence": 4,
            }
        }

    def _mock_spec_registry(self, strategy: str) -> dict[str, Any]:
        return {
            strategy: type(
                "S", (), {"requires_funding": False, "requires_secondary": False}
            )()
        }

    def test_strategy_allowed_on_matching_tf(self) -> None:
        """strategy_timeframes allows the strategy when TF is in the list."""
        ohlcv = self._make_ohlcv()
        with (
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                self._mock_registry("fvg"),
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                self._mock_spec_registry("fvg"),
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["fvg"],
                strategy_timeframes={"fvg": ["4h", "1d"]},
            )
        assert len(events) == 1

    def test_strategy_skipped_on_disallowed_tf(self) -> None:
        """strategy_timeframes skips the strategy when TF is not in the list."""
        ohlcv = self._make_ohlcv()
        with (
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                self._mock_registry("fvg"),
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                self._mock_spec_registry("fvg"),
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="15m",
                strategies=["fvg"],
                strategy_timeframes={"fvg": ["4h", "1d"]},
            )
        assert len(events) == 0

    def test_strategy_runs_on_all_tfs_when_not_listed(self) -> None:
        """A strategy not in strategy_timeframes runs on all timeframes."""
        ohlcv = self._make_ohlcv()
        with (
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                self._mock_registry("fvg"),
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                self._mock_spec_registry("fvg"),
            ),
        ):
            # fvg is not in strategy_timeframes → should run on any TF
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="15m",
                strategies=["fvg"],
                strategy_timeframes={
                    "trend_day": ["4h", "1d"]
                },  # only trend_day restricted
            )
        assert len(events) == 1

    def test_no_strategy_timeframes_runs_all(self) -> None:
        """When strategy_timeframes is None, no TF restrictions apply."""
        ohlcv = self._make_ohlcv()
        with (
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                self._mock_registry("fvg"),
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                self._mock_spec_registry("fvg"),
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="1m",
                strategies=["fvg"],
                strategy_timeframes=None,
            )
        assert len(events) == 1

    def test_trend_day_skipped_on_15m_via_toml_config(self) -> None:
        """trend_day restricted to 4h/1d via strategy_timeframes — 15m is skipped."""
        ohlcv = self._make_ohlcv()
        with (
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                self._mock_registry("trend_day"),
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                self._mock_spec_registry("trend_day"),
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="15m",
                strategies=["trend_day"],
                strategy_timeframes={"trend_day": ["4h", "1d"]},
            )
        assert len(events) == 0

    def test_trend_day_runs_on_4h_via_toml_config(self) -> None:
        """trend_day restricted to 4h/1d via strategy_timeframes — 4h is allowed."""
        ohlcv = self._make_ohlcv()
        with (
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                self._mock_registry("trend_day"),
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                self._mock_spec_registry("trend_day"),
            ),
        ):
            events = scan_symbol(
                ohlcv_df=ohlcv,
                symbol="BTCUSDT",
                timeframe="4h",
                strategies=["trend_day"],
                strategy_timeframes={"trend_day": ["4h", "1d"]},
            )
        assert len(events) == 1


class TestConflictResolution:
    """Tests for the redesigned conflict suppression in run_scan_cycle.

    R10: When LONG + SHORT fire on same symbol/tf, pick the higher-confidence
    side. On a tie, send both — each with "⚠️ conflict" in reason.
    """

    _OPEN_TIME_MS = 1704240000000  # Wednesday 2024-01-03 — passes day_filter

    def _make_ohlcv(self) -> pd.DataFrame:
        rows = [
            {
                "open_time": self._OPEN_TIME_MS - 2000,
                "open": 100.0,
                "high": 105.0,
                "low": 98.0,
                "close": 102.0,
                "volume": 1.0,
            },
            {
                "open_time": self._OPEN_TIME_MS - 1000,
                "open": 102.0,
                "high": 106.0,
                "low": 100.0,
                "close": 103.0,
                "volume": 1.0,
            },
            {
                "open_time": self._OPEN_TIME_MS,
                "open": 103.0,
                "high": 107.0,
                "low": 101.0,
                "close": 104.0,
                "volume": 1.0,
            },
            {
                "open_time": self._OPEN_TIME_MS + 1000,
                "open": 104.0,
                "high": 104.5,
                "low": 103.5,
                "close": 104.2,
                "volume": 0.1,
            },
        ]
        return pd.DataFrame(rows)

    def _make_signals_df(self, direction: str, reason_prefix: str) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "open_time": self._OPEN_TIME_MS,
                    "direction": direction,
                    "reason": f"{reason_prefix}@104.00",
                    "sl_price": 98.0 if direction == "long" else 110.0,
                    "context": "",
                }
            ]
        )

    def test_higher_confidence_long_wins_over_lower_confidence_short(
        self, tmp_path: Any
    ) -> None:
        """LONG with confidence 4 wins over SHORT with confidence 2 — one alert, LONG."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        ohlcv = self._make_ohlcv()
        long_signals = self._make_signals_df("long", "fvg_long")
        short_signals = self._make_signals_df("short", "bos_short")

        with (
            patch("analytics.signal_lib.get_ohlcv", return_value=ohlcv),
            patch(
                "analytics.signal_lib.get_funding_rates", return_value=pd.DataFrame()
            ),
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {
                    "fvg": {"detector": lambda df: long_signals, "confidence": 4},
                    "bos": {"detector": lambda df: short_signals, "confidence": 2},
                },
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )(),
                    "bos": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )(),
                },
            ),
        ):
            alerts = run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg", "bos"],
                store=store,
            )

        assert len(alerts) == 1
        assert "LONG" in alerts[0]
        assert "SHORT" not in alerts[0]
        assert "⚠️ conflict" in alerts[0]

    def test_higher_confidence_short_wins_over_lower_confidence_long(
        self, tmp_path: Any
    ) -> None:
        """SHORT with confidence 5 wins over LONG with confidence 3 — one alert, SHORT."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        ohlcv = self._make_ohlcv()
        long_signals = self._make_signals_df("long", "fvg_long")
        short_signals = self._make_signals_df("short", "smt_short")

        with (
            patch("analytics.signal_lib.get_ohlcv", return_value=ohlcv),
            patch(
                "analytics.signal_lib.get_funding_rates", return_value=pd.DataFrame()
            ),
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {
                    "fvg": {"detector": lambda df: long_signals, "confidence": 3},
                    "smt_divergence": {
                        "detector": lambda df: short_signals,
                        "confidence": 5,
                    },
                },
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )(),
                    "smt_divergence": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )(),
                },
            ),
        ):
            alerts = run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg", "smt_divergence"],
                store=store,
            )

        assert len(alerts) == 1
        assert "SHORT" in alerts[0]
        assert "LONG" not in alerts[0]
        assert "⚠️ conflict" in alerts[0]

    def test_tied_confidence_sends_both_directions(self, tmp_path: Any) -> None:
        """When confidence is equal, both LONG and SHORT alerts are sent."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        ohlcv = self._make_ohlcv()
        long_signals = self._make_signals_df("long", "fvg_long")
        short_signals = self._make_signals_df("short", "bos_short")

        with (
            patch("analytics.signal_lib.get_ohlcv", return_value=ohlcv),
            patch(
                "analytics.signal_lib.get_funding_rates", return_value=pd.DataFrame()
            ),
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {
                    "fvg": {"detector": lambda df: long_signals, "confidence": 4},
                    "bos": {"detector": lambda df: short_signals, "confidence": 4},
                },
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )(),
                    "bos": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )(),
                },
            ),
        ):
            alerts = run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg", "bos"],
                store=store,
            )

        assert len(alerts) == 2  # one per direction
        directions = {a.split("Direction: ")[1].split()[0] for a in alerts}
        assert "LONG" in directions
        assert "SHORT" in directions
        for alert in alerts:
            assert "⚠️ conflict" in alert

    def test_conflict_tag_appears_in_alert(self, tmp_path: Any) -> None:
        """The conflict tag appears in the alert outside the reason backtick."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        ohlcv = self._make_ohlcv()
        long_signals = self._make_signals_df("long", "fvg_long")
        short_signals = self._make_signals_df("short", "bos_short")

        with (
            patch("analytics.signal_lib.get_ohlcv", return_value=ohlcv),
            patch(
                "analytics.signal_lib.get_funding_rates", return_value=pd.DataFrame()
            ),
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {
                    "fvg": {"detector": lambda df: long_signals, "confidence": 5},
                    "bos": {"detector": lambda df: short_signals, "confidence": 3},
                },
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )(),
                    "bos": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )(),
                },
            ),
        ):
            alerts = run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg", "bos"],
                store=store,
            )

        assert len(alerts) == 1
        # Conflict tag appears outside the code-tagged reason field
        assert "fvg_long@104.00</code>" in alerts[0]
        assert "⚠️ conflict" in alerts[0]

    def test_no_conflict_no_tag(self, tmp_path: Any) -> None:
        """Signals without a conflict must NOT have ⚠️ conflict in the alert."""
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        ohlcv = self._make_ohlcv()
        long_signals = self._make_signals_df("long", "fvg_long")

        with (
            patch("analytics.signal_lib.get_ohlcv", return_value=ohlcv),
            patch(
                "analytics.signal_lib.get_funding_rates", return_value=pd.DataFrame()
            ),
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {
                    "fvg": {"detector": lambda df: long_signals, "confidence": 4},
                },
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )(),
                },
            ),
        ):
            alerts = run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
            )

        assert len(alerts) == 1
        assert "⚠️ conflict" not in alerts[0]


class TestSignalOutcomePersistence:
    """A4 P1 — verify run_scan_cycle writes a row to signal_alert_outcomes for each fired signal."""

    _OPEN_TIME_MS = 1704240000000  # Wednesday 2024-01-03 00:00:00 UTC

    def _make_ohlcv(self) -> pd.DataFrame:
        t = self._OPEN_TIME_MS
        return pd.DataFrame(
            [
                {
                    "open_time": t - 2000,
                    "open": 100.0,
                    "high": 105.0,
                    "low": 98.0,
                    "close": 102.0,
                    "volume": 1.0,
                },
                {
                    "open_time": t - 1000,
                    "open": 102.0,
                    "high": 106.0,
                    "low": 100.0,
                    "close": 103.0,
                    "volume": 1.0,
                },
                {
                    "open_time": t,
                    "open": 103.0,
                    "high": 107.0,
                    "low": 101.0,
                    "close": 104.0,
                    "volume": 1.0,
                },
                {
                    "open_time": t + 1000,
                    "open": 104.0,
                    "high": 104.5,
                    "low": 103.5,
                    "close": 104.0,
                    "volume": 0.1,
                },
            ]
        )

    def _make_signals_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "open_time": self._OPEN_TIME_MS,
                    "direction": "long",
                    "reason": "fvg_long@100.00-102.00",
                    "sl_price": 98.0,
                    "context": "",
                }
            ]
        )

    def test_fired_signal_writes_outcome_row(self, tmp_path: Any) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        signals_df = self._make_signals_df()

        with (
            patch("analytics.signal_lib.get_ohlcv", return_value=self._make_ohlcv()),
            patch(
                "analytics.signal_lib.get_funding_rates", return_value=pd.DataFrame()
            ),
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {"fvg": {"detector": lambda df: signals_df, "confidence": 3}},
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )()
                },
            ),
        ):
            run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
            )

        rows = conn.execute("SELECT * FROM signal_alert_outcomes").fetchall()
        assert len(rows) == 1

    def test_outcome_row_has_correct_fields(self, tmp_path: Any) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        signals_df = self._make_signals_df()

        with (
            patch("analytics.signal_lib.get_ohlcv", return_value=self._make_ohlcv()),
            patch(
                "analytics.signal_lib.get_funding_rates", return_value=pd.DataFrame()
            ),
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {"fvg": {"detector": lambda df: signals_df, "confidence": 3}},
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )()
                },
            ),
        ):
            run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
            )

        row = conn.execute(
            "SELECT signal_id, symbol, tf, strategy, direction, candle_ts_ms, "
            "sl_price, confidence_at_fire, outcome FROM signal_alert_outcomes"
        ).fetchone()
        assert row is not None
        assert row[0] == f"BTCUSDT-4h-fvg-{self._OPEN_TIME_MS}-long"
        assert row[1] == "BTCUSDT"
        assert row[2] == "4h"
        assert row[3] == "fvg"
        assert row[4] == "long"
        assert row[5] == self._OPEN_TIME_MS
        assert row[6] == 98.0
        assert row[7] == 3
        assert row[8] is None  # outcome not yet resolved


class TestBacktestRunPersistence:
    """Verify run_scan_cycle writes to backtest_runs when backtest_cfg.save_results=True."""

    _OPEN_TIME_MS = 1704240000000  # Wednesday 2024-01-03 00:00:00 UTC

    def _make_ohlcv(self) -> pd.DataFrame:
        t = self._OPEN_TIME_MS
        # Signal fires on t (last closed candle); t+1000 is the open/forming candle.
        return pd.DataFrame(
            [
                {
                    "open_time": t - 2000,
                    "open": 100.0,
                    "high": 105.0,
                    "low": 98.0,
                    "close": 102.0,
                    "volume": 1.0,
                },
                {
                    "open_time": t - 1000,
                    "open": 102.0,
                    "high": 106.0,
                    "low": 100.0,
                    "close": 103.0,
                    "volume": 1.0,
                },
                {
                    "open_time": t,
                    "open": 103.0,
                    "high": 107.0,
                    "low": 101.0,
                    "close": 104.0,
                    "volume": 1.0,
                },
                {
                    "open_time": t + 1000,
                    "open": 104.0,
                    "high": 104.5,
                    "low": 103.5,
                    "close": 104.0,
                    "volume": 0.1,
                },
            ]
        )

    def _make_signals_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "open_time": self._OPEN_TIME_MS,
                    "direction": "long",
                    "reason": "test",
                    "sl_price": 98.0,
                    "context": "",
                }
            ]
        )

    def _run(self, tmp_path: Any, save_results: bool) -> duckdb.DuckDBPyConnection:
        from analytics.signal_config import BacktestFilterConfig

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        signals_df = self._make_signals_df()
        bt_cfg = BacktestFilterConfig(mode="soft", days=90, save_results=save_results)

        with (
            patch("analytics.signal_lib.get_ohlcv", return_value=self._make_ohlcv()),
            patch(
                "analytics.signal_lib.get_funding_rates", return_value=pd.DataFrame()
            ),
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {"fvg": {"detector": lambda df: signals_df, "confidence": 3}},
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )()
                },
            ),
        ):
            run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
                backtest_cfg=bt_cfg,
            )
        return conn

    def test_writes_backtest_run_row_when_save_results_true(
        self, tmp_path: Any
    ) -> None:
        conn = self._run(tmp_path, save_results=True)
        count = conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()
        assert count is not None
        assert count[0] == 1

    def test_backtest_run_row_has_correct_strategy(self, tmp_path: Any) -> None:
        conn = self._run(tmp_path, save_results=True)
        row = conn.execute(
            "SELECT symbol, timeframe, strategy FROM backtest_runs"
        ).fetchone()
        assert row is not None
        assert row[0] == "BTCUSDT"
        assert row[1] == "4h"
        assert row[2] == "fvg"

    def test_no_row_written_when_save_results_false(self, tmp_path: Any) -> None:
        conn = self._run(tmp_path, save_results=False)
        count = conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()
        assert count is not None
        assert count[0] == 0

    def test_no_row_written_when_no_backtest_cfg(self, tmp_path: Any) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        store = CooldownStore(str(tmp_path / "state.json"))
        signals_df = self._make_signals_df()

        with (
            patch("analytics.signal_lib.get_ohlcv", return_value=self._make_ohlcv()),
            patch(
                "analytics.signal_lib.get_funding_rates", return_value=pd.DataFrame()
            ),
            patch(
                "analytics.signal_lib.SIGNAL_REGISTRY",
                {"fvg": {"detector": lambda df: signals_df, "confidence": 3}},
            ),
            patch(
                "analytics.signal_lib.STRATEGY_REGISTRY",
                {
                    "fvg": type(
                        "S",
                        (),
                        {"requires_funding": False, "requires_secondary": False},
                    )()
                },
            ),
        ):
            run_scan_cycle(
                conn=conn,
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                strategies=["fvg"],
                store=store,
                backtest_cfg=None,  # no backtest cfg → no persistence
            )
        count = conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()
        assert count is not None
        assert count[0] == 0

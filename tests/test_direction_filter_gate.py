"""Unit tests for the T2c direction filter gate (`_apply_direction_filter_gate`).

The gate is pure — it reads `StrategyOverride.suppress_long` / `.suppress_short`
from a `strategy_params` dict, so no DB / OHLCV setup is needed.
"""

from analytics.signal.gates import _apply_direction_filter_gate
from analytics.signal.types import SignalEvent
from analytics.signal_config import BiasConfig, StrategyOverride


def _evt(strategy: str, direction: str = "long") -> SignalEvent:
    return SignalEvent(
        symbol="BTCUSDT",
        timeframe="1h",
        strategy=strategy,
        direction=direction,
        reason="test",
        open_time=1_700_000_000_000,
        price=100.0,
    )


def _bias(*, enabled: bool = True, mode: str = "hard") -> BiasConfig:
    return BiasConfig(
        direction_filter_enabled=enabled,
        direction_filter_mode=mode,
    )


class TestDirectionFilterGate:
    def test_disabled_is_noop(self) -> None:
        # bos long is suppressed in TOML, but gate disabled → allow.
        params = {"bos": StrategyOverride(suppress_long=True)}
        events = [_evt("bos", "long")]
        out = _apply_direction_filter_gate(
            events, _bias(enabled=False), params, "BTCUSDT", "1h"
        )
        assert len(out) == 1

    def test_no_strategy_params_is_noop(self) -> None:
        events = [_evt("bos", "long")]
        out = _apply_direction_filter_gate(events, _bias(), None, "BTCUSDT", "1h")
        assert len(out) == 1

    def test_empty_events_is_noop(self) -> None:
        params = {"bos": StrategyOverride(suppress_long=True)}
        out = _apply_direction_filter_gate([], _bias(), params, "BTCUSDT", "1h")
        assert out == []

    def test_hard_mode_drops_suppressed_long(self) -> None:
        params = {"bos": StrategyOverride(suppress_long=True)}
        events = [_evt("bos", "long"), _evt("bos", "short")]
        out = _apply_direction_filter_gate(
            events, _bias(mode="hard"), params, "BTCUSDT", "1h"
        )
        assert len(out) == 1
        assert out[0].direction == "short"

    def test_hard_mode_drops_suppressed_short(self) -> None:
        params = {"bos": StrategyOverride(suppress_short=True)}
        events = [_evt("bos", "long"), _evt("bos", "short")]
        out = _apply_direction_filter_gate(
            events, _bias(mode="hard"), params, "BTCUSDT", "1h"
        )
        assert len(out) == 1
        assert out[0].direction == "long"

    def test_hard_mode_drops_both_directions_when_both_flagged(self) -> None:
        params = {"bos": StrategyOverride(suppress_long=True, suppress_short=True)}
        events = [_evt("bos", "long"), _evt("bos", "short")]
        out = _apply_direction_filter_gate(
            events, _bias(mode="hard"), params, "BTCUSDT", "1h"
        )
        assert out == []

    def test_soft_mode_keeps_all(self) -> None:
        params = {"bos": StrategyOverride(suppress_long=True)}
        events = [_evt("bos", "long"), _evt("bos", "short")]
        out = _apply_direction_filter_gate(
            events, _bias(mode="soft"), params, "BTCUSDT", "1h"
        )
        assert len(out) == 2

    def test_unflagged_strategy_falls_open(self) -> None:
        # ema has an override row but no suppress flags set.
        params = {"ema": StrategyOverride()}
        events = [_evt("ema", "long"), _evt("ema", "short")]
        out = _apply_direction_filter_gate(
            events, _bias(mode="hard"), params, "BTCUSDT", "1h"
        )
        assert len(out) == 2

    def test_unknown_strategy_falls_open(self) -> None:
        # Strategy not in params dict at all → defensive pass-through.
        params = {"bos": StrategyOverride(suppress_long=True)}
        events = [_evt("ema", "long"), _evt("ema", "short")]
        out = _apply_direction_filter_gate(
            events, _bias(mode="hard"), params, "BTCUSDT", "1h"
        )
        assert len(out) == 2

    def test_only_matched_direction_dropped(self) -> None:
        # Multi-strategy mix: bos long suppressed, ema unrestricted.
        params = {"bos": StrategyOverride(suppress_long=True)}
        events = [
            _evt("bos", "long"),
            _evt("bos", "short"),
            _evt("ema", "long"),
            _evt("ema", "short"),
        ]
        out = _apply_direction_filter_gate(
            events, _bias(mode="hard"), params, "BTCUSDT", "1h"
        )
        # bos long dropped; other 3 kept.
        assert len(out) == 3
        assert not any(e.strategy == "bos" and e.direction == "long" for e in out)

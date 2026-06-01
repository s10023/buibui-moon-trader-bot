"""Unit tests for the F8 HTF EMA directional gate (`_apply_htf_ema_gate`).

The gate logic is pure — it consumes a pre-computed slope cache, so these tests
do not need any DB or OHLCV setup. The slope helper is covered separately in
`tests/test_strategies_shared.py::TestComputeHtfEmaSlope`.
"""

from analytics.signal.gates import _apply_htf_ema_gate
from analytics.signal.types import SignalEvent
from analytics.signal_config import BiasConfig, HtfEmaAnchor


def _evt(strategy: str, direction: str) -> SignalEvent:
    return SignalEvent(
        symbol="BTCUSDT",
        timeframe="1h",
        strategy=strategy,
        direction=direction,
        reason="test",
        open_time=1_700_000_000_000,
        price=100.0,
    )


def _bias(
    *,
    enabled: bool = True,
    mode: str = "hard",
    deadband: float = 0.003,
    overrides: dict[str, HtfEmaAnchor] | None = None,
    default_suppress_directions: tuple[str, ...] = ("long", "short"),
) -> BiasConfig:
    return BiasConfig(
        htf_ema_enabled=enabled,
        htf_ema_mode=mode,
        htf_ema_default_tf="4h",
        htf_ema_default_period=50,
        htf_ema_default_slope_lookback=10,
        htf_ema_deadband_pct=deadband,
        htf_ema_per_strategy=overrides or {},
        htf_ema_default_suppress_directions=default_suppress_directions,
    )


class TestHtfEmaGate:
    def test_disabled_is_noop(self) -> None:
        # Cache opposes long, but gate is disabled → both directions pass.
        cache = {("BTCUSDT", "4h", 50, 10): -0.05}
        events = [_evt("bos", "long"), _evt("bos", "short")]
        out = _apply_htf_ema_gate(events, _bias(enabled=False), cache, "BTCUSDT", "1h")
        assert len(out) == 2

    def test_hard_mode_drops_opposing_long(self) -> None:
        # slope = +5% → up-trend → SHORT opposes
        cache = {("BTCUSDT", "4h", 50, 10): 0.05}
        events = [_evt("bos", "long"), _evt("bos", "short")]
        out = _apply_htf_ema_gate(events, _bias(mode="hard"), cache, "BTCUSDT", "1h")
        assert [e.direction for e in out] == ["long"]

    def test_hard_mode_drops_opposing_short(self) -> None:
        # slope = -5% → down-trend → LONG opposes
        cache = {("BTCUSDT", "4h", 50, 10): -0.05}
        events = [_evt("bos", "long"), _evt("bos", "short")]
        out = _apply_htf_ema_gate(events, _bias(mode="hard"), cache, "BTCUSDT", "1h")
        assert [e.direction for e in out] == ["short"]

    def test_soft_mode_keeps_all(self) -> None:
        cache = {("BTCUSDT", "4h", 50, 10): 0.05}
        events = [_evt("bos", "long"), _evt("bos", "short")]
        out = _apply_htf_ema_gate(events, _bias(mode="soft"), cache, "BTCUSDT", "1h")
        assert len(out) == 2  # soft = log-only

    def test_deadband_allows_both_directions(self) -> None:
        # |slope| = 0.001 < deadband 0.003 → HTF flat → both pass
        cache = {("BTCUSDT", "4h", 50, 10): 0.001}
        events = [_evt("bos", "long"), _evt("bos", "short")]
        out = _apply_htf_ema_gate(events, _bias(mode="hard"), cache, "BTCUSDT", "1h")
        assert len(out) == 2

    def test_warmup_or_missing_data_allows_both(self) -> None:
        # slope=None → insufficient data / not in cache
        cache: dict[tuple[str, str, int, int], float | None] = {
            ("BTCUSDT", "4h", 50, 10): None
        }
        events = [_evt("bos", "long"), _evt("bos", "short")]
        out = _apply_htf_ema_gate(events, _bias(mode="hard"), cache, "BTCUSDT", "1h")
        assert len(out) == 2

    def test_per_strategy_override_uses_different_anchor(self) -> None:
        # Default anchor (4h) has up-slope → SHORT would be suppressed.
        # Override for ema → 1d EMA-50 with down-slope → ema SHORT passes,
        # ema LONG is suppressed; non-overridden strategies follow default.
        overrides = {
            "ema": HtfEmaAnchor(tf="1d", period=50, slope_lookback=10),
        }
        cache = {
            ("BTCUSDT", "4h", 50, 10): 0.05,  # up-trend on 4h
            ("BTCUSDT", "1d", 50, 10): -0.05,  # down-trend on 1d
        }
        events = [
            _evt("ema", "long"),  # opposes 1d down → drop
            _evt("ema", "short"),  # aligned with 1d down → keep
            _evt("bos", "long"),  # aligned with 4h up → keep
            _evt("bos", "short"),  # opposes 4h up → drop
        ]
        out = _apply_htf_ema_gate(
            events,
            _bias(mode="hard", overrides=overrides),
            cache,
            "BTCUSDT",
            "1h",
        )
        assert [(e.strategy, e.direction) for e in out] == [
            ("ema", "short"),
            ("bos", "long"),
        ]

    def test_aligned_signal_passes(self) -> None:
        # slope down, direction short → aligned → pass
        cache = {("BTCUSDT", "4h", 50, 10): -0.02}
        events = [_evt("bos", "short")]
        out = _apply_htf_ema_gate(events, _bias(mode="hard"), cache, "BTCUSDT", "1h")
        assert len(out) == 1

    def test_empty_event_list_returns_empty(self) -> None:
        out = _apply_htf_ema_gate([], _bias(mode="hard"), {}, "BTCUSDT", "1h")
        assert out == []

    def test_long_only_scope_keeps_counter_trend_short(self) -> None:
        # slope up → SHORT opposes, but scope = ["long"] → short is NOT suppressed.
        cache = {("BTCUSDT", "4h", 50, 10): 0.05}
        events = [_evt("bos", "long"), _evt("bos", "short")]
        out = _apply_htf_ema_gate(
            events,
            _bias(mode="hard", default_suppress_directions=("long",)),
            cache,
            "BTCUSDT",
            "1h",
        )
        assert {e.direction for e in out} == {"long", "short"}

    def test_long_only_scope_still_drops_counter_trend_long(self) -> None:
        # slope down → LONG opposes; scope ["long"] still drops it.
        cache = {("BTCUSDT", "4h", 50, 10): -0.05}
        events = [_evt("bos", "long"), _evt("bos", "short")]
        out = _apply_htf_ema_gate(
            events,
            _bias(mode="hard", default_suppress_directions=("long",)),
            cache,
            "BTCUSDT",
            "1h",
        )
        assert [e.direction for e in out] == ["short"]

    def test_empty_scope_exempts_strategy_via_override(self) -> None:
        # cvd_divergence override with suppress_directions=() → never suppressed.
        overrides = {
            "cvd_divergence": HtfEmaAnchor(
                tf="4h", period=50, slope_lookback=10, suppress_directions=()
            )
        }
        cache = {("BTCUSDT", "4h", 50, 10): 0.05}
        events = [_evt("cvd_divergence", "long"), _evt("cvd_divergence", "short")]
        out = _apply_htf_ema_gate(
            events, _bias(mode="hard", overrides=overrides), cache, "BTCUSDT", "1h"
        )
        assert len(out) == 2

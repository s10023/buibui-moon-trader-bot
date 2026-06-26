"""Tests for the faithful per-strategy structural entry-simulation harness."""

from __future__ import annotations

from typing import Any

import pandas as pd

import analytics.structural_entry_sim as ses
from analytics.structural_entry_sim import (
    REALIZED_TABLE_COLUMNS,
    _merge_touches_to_trades,
    build_realized_table,
    build_touch_signals,
    evaluate_build,
    resolve_touch_trades,
    touch_sl_price,
)
from analytics.structural_touch import Touch, Zone


def _bars(
    closes: list[float], *, start_ms: int = 0, step_ms: int = 86_400_000
) -> pd.DataFrame:
    """Minimal OHLCV frame; high/low default to a tight band around close."""
    n = len(closes)
    return pd.DataFrame(
        {
            "open_time": [start_ms + i * step_ms for i in range(n)],
            "open": closes,
            "high": [c * 1.001 for c in closes],
            "low": [c * 0.999 for c in closes],
            "close": closes,
            "volume": [1000.0] * n,
        }
    )


class TestTouchSlPrice:
    def test_structural_long_uses_zone_low(self) -> None:
        zone = Zone("fvg", "long", zone_low=90.0, zone_high=100.0, start_ms=0)
        sl = touch_sl_price(zone, entry_ref=98.0, atr=10.0, sl_model="structural")
        assert sl == 90.0  # demand zone → stop below the far (low) edge

    def test_structural_short_uses_zone_high(self) -> None:
        zone = Zone("fvg", "short", zone_low=90.0, zone_high=100.0, start_ms=0)
        sl = touch_sl_price(zone, entry_ref=92.0, atr=10.0, sl_model="structural")
        assert sl == 100.0  # supply zone → stop above the far (high) edge

    def test_atr_floor_widens_tight_structural_stop(self) -> None:
        # entry 99 right on the band; structural stop (zone_low 98) is only 1.0
        # away but 0.5*ATR(10) = 5.0 → floor widens the stop to 99 - 5 = 94.
        zone = Zone("fvg", "long", zone_low=98.0, zone_high=100.0, start_ms=0)
        sl = touch_sl_price(zone, entry_ref=99.0, atr=10.0, sl_model="atr_floor")
        assert sl == 94.0

    def test_atr_floor_keeps_wide_structural_stop(self) -> None:
        # structural stop already wider than the floor → unchanged.
        zone = Zone("fvg", "long", zone_low=90.0, zone_high=100.0, start_ms=0)
        sl = touch_sl_price(zone, entry_ref=99.0, atr=4.0, sl_model="atr_floor")
        assert sl == 90.0  # floor 0.5*4=2 → 97; structural 90 is wider → keep 90

    def test_fixed_atr_ignores_structural_edge(self) -> None:
        zone = Zone("fvg", "short", zone_low=90.0, zone_high=100.0, start_ms=0)
        sl = touch_sl_price(
            zone, entry_ref=95.0, atr=10.0, sl_model="fixed_atr", atr_mult=1.0
        )
        assert sl == 105.0  # short → entry_ref + 1.0*ATR, edge ignored


class TestBuildTouchSignals:
    def test_one_row_per_touch_with_structural_far_edge(self) -> None:
        bars = _bars([100.0, 95.0, 99.0, 94.0, 99.0, 94.0])
        zone = Zone("fvg", "long", zone_low=92.0, zone_high=96.0, start_ms=-1)
        touches = [
            Touch(touch_index=1, bar_idx=3, ts_ms=int(bars["open_time"].iloc[3])),
            Touch(touch_index=2, bar_idx=5, ts_ms=int(bars["open_time"].iloc[5])),
        ]
        sig = build_touch_signals(zone, touches, bars, sl_model="structural")

        assert list(sig["touch_index"]) == [1, 2]
        assert list(sig["direction"]) == ["long", "long"]
        assert list(sig["sl_price"]) == [92.0, 92.0]  # far edge for a long zone
        assert list(sig["open_time"]) == [
            int(bars["open_time"].iloc[3]),
            int(bars["open_time"].iloc[5]),
        ]
        assert sig["zone_id"].nunique() == 1  # all touches share one zone id

    def test_empty_touches_returns_empty_frame(self) -> None:
        bars = _bars([100.0, 95.0, 99.0])
        zone = Zone("fvg", "long", zone_low=92.0, zone_high=96.0, start_ms=-1)
        sig = build_touch_signals(zone, [], bars, sl_model="structural")
        assert sig.empty
        assert list(sig.columns) == [
            "open_time",
            "direction",
            "sl_price",
            "touch_index",
            "zone_id",
        ]


def _win_loss_bars() -> pd.DataFrame:
    """8 daily bars: a long signal at bar 1 wins (TP), one at bar 4 loses (SL)."""
    rows = [
        # open_time(day), open, high, low, close
        (0, 100, 101, 99, 100),  # 0 filler
        (1, 100, 101, 99, 100),  # 1 signal A candle (entry next bar)
        (2, 100, 101, 99, 100),  # 2 entry A, no hit
        (3, 120, 121, 119, 120),  # 3 TP A (high 121 >= 120)
        (4, 100, 101, 99, 100),  # 4 signal B candle
        (5, 100, 101, 99, 100),  # 5 entry B, no hit
        (6, 90, 102, 85, 90),  # 6 SL B (low 85 <= 90)
        (7, 100, 101, 99, 100),  # 7 filler
    ]
    day = 86_400_000
    return pd.DataFrame(
        {
            "open_time": [r[0] * day for r in rows],
            "open": [float(r[1]) for r in rows],
            "high": [float(r[2]) for r in rows],
            "low": [float(r[3]) for r in rows],
            "close": [float(r[1]) for r in rows],
            "volume": [1000.0] * len(rows),
        }
    )


def _two_signals() -> pd.DataFrame:
    day = 86_400_000
    return pd.DataFrame(
        {
            "open_time": [1 * day, 4 * day],
            "direction": ["long", "long"],
            "sl_price": [90.0, 90.0],
            "touch_index": [1, 2],
            "zone_id": ["z", "z"],
        }
    )


class TestResolveTouchTrades:
    def test_win_and_loss_gross_r(self) -> None:
        trades = resolve_touch_trades(
            _win_loss_bars(), _two_signals(), symbol="BTCUSDT", tf="1d", tp_r=2.0
        )
        by_time = {
            int(t): g
            for t, g in zip(trades["open_time"], trades["pnl_r_gross"], strict=True)
        }
        day = 86_400_000
        assert by_time[1 * day] == 2.0  # TP at +2R
        assert by_time[4 * day] == -1.0  # SL at -1R

    def test_costs_reduce_net_below_gross(self) -> None:
        trades = resolve_touch_trades(
            _win_loss_bars(),
            _two_signals(),
            symbol="BTCUSDT",
            tf="1d",
            tp_r=2.0,
            fee_pct=0.001,
        )
        # fee drag = 2 * 0.001 * 100 / 10 = 0.02 R on every trade
        assert (trades["pnl_r"] < trades["pnl_r_gross"]).all()
        win = trades[trades["open_time"] == 86_400_000].iloc[0]
        assert abs(win["pnl_r"] - 1.98) < 1e-9

    def test_zero_cost_net_equals_gross(self) -> None:
        trades = resolve_touch_trades(
            _win_loss_bars(), _two_signals(), symbol="BTCUSDT", tf="1d", tp_r=2.0
        )
        assert (trades["pnl_r"] == trades["pnl_r_gross"]).all()


class TestMergeTouchesToTrades:
    def test_each_touch_gets_its_realized_r(self) -> None:
        touch_signals = pd.DataFrame(
            {
                "open_time": [10, 20],
                "direction": ["long", "long"],
                "sl_price": [90.0, 90.0],
                "touch_index": [1, 2],
                "zone_id": ["z", "z"],
            }
        )
        trades = pd.DataFrame(
            {
                "open_time": [10, 20],
                "direction": ["long", "long"],
                "sl_price": [90.0, 90.0],
                "pnl_r": [2.0, -1.0],
                "pnl_r_gross": [2.0, -1.0],
            }
        )
        merged = _merge_touches_to_trades(touch_signals, trades)
        by_idx = {
            int(ti): float(r)
            for ti, r in zip(merged["touch_index"], merged["pnl_r"], strict=True)
        }
        assert by_idx == {1: 2.0, 2: -1.0}
        assert list(merged["ts_ms"]) == [10, 20]

    def test_unresolved_touch_dropped(self) -> None:
        # touch at open_time 30 never closed → no trade row → dropped.
        touch_signals = pd.DataFrame(
            {
                "open_time": [10, 30],
                "direction": ["long", "long"],
                "sl_price": [90.0, 90.0],
                "touch_index": [1, 2],
                "zone_id": ["z", "z"],
            }
        )
        trades = pd.DataFrame(
            {
                "open_time": [10],
                "direction": ["long"],
                "sl_price": [90.0],
                "pnl_r": [2.0],
                "pnl_r_gross": [2.0],
            }
        )
        merged = _merge_touches_to_trades(touch_signals, trades)
        assert list(merged["touch_index"]) == [1]


def _staircase_then_drop() -> pd.DataFrame:
    """Reused from test_structural_touch — reliably forms a real fvg zone."""
    highs = [105.0 + 10 * k for k in range(10)]
    lows = [100.0 + 10 * k for k in range(10)]
    highs.append(90.0)
    lows.append(10.0)
    n = len(highs)
    return pd.DataFrame(
        {
            "open_time": [1_000 + i for i in range(n)],
            "open": lows,
            "high": highs,
            "low": lows,
            "close": highs,
            "volume": [1000.0] * n,
        }
    )


class TestSimulateCell:
    def test_injected_zone_touches_yield_exact_realized_r(self) -> None:
        bars = _win_loss_bars()
        day = 86_400_000
        zone = Zone("fvg", "long", zone_low=90.0, zone_high=96.0, start_ms=-1)
        touches = [
            Touch(touch_index=1, bar_idx=1, ts_ms=1 * day),
            Touch(touch_index=2, bar_idx=4, ts_ms=4 * day),
        ]
        out = ses.simulate_cell(
            bars,
            "fvg",
            symbol="BTCUSDT",
            tf="1d",
            tp_r=2.0,
            sl_model="structural",
            zone_touches=[(zone, touches)],
        )
        assert list(out.columns) == REALIZED_TABLE_COLUMNS
        by_idx = {
            int(ti): float(r)
            for ti, r in zip(out["touch_index"], out["pnl_r"], strict=True)
        }
        assert by_idx == {1: 2.0, 2: -1.0}
        assert set(out["zone_type"]) == {"fvg"}
        assert set(out["tp_r"]) == {2.0}
        assert set(out["sl_model"]) == {"structural"}


class TestBuildRealizedTable:
    def test_real_fvg_extraction_runs_and_keeps_schema(self) -> None:
        bars_by = {("BTCUSDT", "1d"): _staircase_then_drop()}
        table = build_realized_table(
            bars_by, ["fvg"], tp_r_grid=[2.0], sl_models=["structural"]
        )
        assert list(table.columns) == REALIZED_TABLE_COLUMNS

    def test_grid_expands_over_tp_r_and_sl_model(self, monkeypatch: Any) -> None:
        bars = _win_loss_bars()
        day = 86_400_000
        zone = Zone("fvg", "long", zone_low=90.0, zone_high=96.0, start_ms=-1)
        touches = [
            Touch(touch_index=1, bar_idx=1, ts_ms=1 * day),
            Touch(touch_index=2, bar_idx=4, ts_ms=4 * day),
        ]
        monkeypatch.setattr(
            ses, "extract_zone_touches", lambda *a, **k: [(zone, touches)]
        )
        table = build_realized_table(
            {("BTCUSDT", "1d"): bars},
            ["fvg"],
            tp_r_grid=[1.0, 2.0],
            sl_models=["structural", "atr_floor"],
        )
        combos = set(zip(table["tp_r"], table["sl_model"], strict=True))
        assert combos == {
            (1.0, "structural"),
            (2.0, "structural"),
            (1.0, "atr_floor"),
            (2.0, "atr_floor"),
        }
        assert table["pnl_r"].notna().all()


def _cell_table(
    rows_r: list[float],
    *,
    touch_index: int = 1,
    zone_type: str = "fvg",
    direction: str = "long",
    tp_r: float = 2.0,
    sl_model: str = "atr_floor",
    tf: str = "1d",
) -> pd.DataFrame:
    n = len(rows_r)
    return pd.DataFrame(
        {
            "symbol": ["BTCUSDT"] * n,
            "tf": [tf] * n,
            "zone_type": [zone_type] * n,
            "direction": [direction] * n,
            "zone_id": [f"z{i}" for i in range(n)],
            "touch_index": [touch_index] * n,
            "tp_r": [tp_r] * n,
            "sl_model": [sl_model] * n,
            "pnl_r": rows_r,
            "pnl_r_gross": rows_r,
            "ts_ms": list(range(n)),
        }
    )


_HEADLINE: dict[str, Any] = {
    "headline_tp_r": 2.0,
    "headline_sl_model": "atr_floor",
    "headline_tf": "1d",
}


class TestEvaluateBuild:
    def test_insufficient_below_min_n(self) -> None:
        table = _cell_table([1.0, 1.0, 1.0])  # n=3 < min_n
        verdicts = evaluate_build(table, **_HEADLINE, min_n=30, n_boot=2000)
        assert len(verdicts) == 1
        assert verdicts[0].decision == "INSUFFICIENT"
        assert verdicts[0].n_first == 3

    def test_build_when_first_touch_reliably_positive(self) -> None:
        rows = [1.0 + 0.1 * (-1) ** i for i in range(60)]  # mean 1.0, sd ~0.1
        table = _cell_table(rows)
        v = evaluate_build(table, **_HEADLINE, min_n=30, bar=0.0, n_boot=2000)[0]
        assert v.decision == "BUILD"
        assert v.boot_lo is not None and v.boot_lo > 0.0
        assert v.n_first == 60

    def test_no_edge_when_ci_spans_zero(self) -> None:
        rows = [0.6 * (-1) ** i for i in range(60)]  # mean ~0, CI spans 0
        table = _cell_table(rows)
        v = evaluate_build(table, **_HEADLINE, min_n=30, bar=0.0, n_boot=2000)[0]
        assert v.decision == "NO-EDGE"

    def test_only_headline_config_rows_gate_the_cell(self) -> None:
        # strong positive at the headline config; a junk off-config trial present.
        good = _cell_table([1.0 + 0.1 * (-1) ** i for i in range(60)])
        junk = _cell_table(
            [-2.0] * 60, tp_r=1.0, sl_model="structural"
        )  # different (tp_r, sl_model) → not the headline
        table = pd.concat([good, junk], ignore_index=True)
        v = evaluate_build(table, **_HEADLINE, min_n=30, bar=0.0, n_boot=2000)[0]
        assert v.first_avg_r is not None and v.first_avg_r > 0.9
        assert v.decision == "BUILD"

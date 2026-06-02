"""Tests for the outcome-ledger SL/TP fallback.

The live outcome-ledger writer must persist a non-NULL sl_price/tp_price for
*every* fired event, falling back to the same pct-based SL the alert formatter
already uses when an event carries no valid structural SL. See
docs/superpowers/specs/2026-06-01-outcome-ledger-sl-tp-fallback-design.md.
"""

import math
from typing import Any
from unittest.mock import patch

import duckdb
import pandas as pd

from analytics.signal.scanner import _resolve_outcome_sl_tp, run_scan_cycle
from analytics.signal.types import SignalEvent
from analytics.store import init_schema
from signals.cooldown_store import CooldownStore

_HOUR = 3_600_000


def _ohlcv_df(open_time_ms: int) -> pd.DataFrame:
    """Minimal 3-row OHLCV frame; second-to-last row is the latest closed candle."""
    rows = [
        {
            "open_time": open_time_ms - 1000,
            "open": 100.0,
            "high": 105.0,
            "low": 98.0,
            "close": 102.0,
            "volume": 1.0,
        },
        {
            "open_time": open_time_ms,
            "open": 102.0,
            "high": 106.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1.0,
        },
        {
            "open_time": open_time_ms + 1000,
            "open": 100.0,
            "high": 100.5,
            "low": 99.5,
            "close": 100.2,
            "volume": 0.1,
        },
    ]
    return pd.DataFrame(rows)


def _run_cycle_with_event(event: SignalEvent, tmp_path: Any) -> dict[str, Any]:
    """Drive run_scan_cycle with scan_symbol stubbed to emit `event`, then return
    the persisted signal_alert_outcomes row as a dict."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    store = CooldownStore(str(tmp_path / "state.json"))
    df = _ohlcv_df(event.open_time)

    with (
        patch("analytics.signal.scanner.get_ohlcv", return_value=df),
        patch(
            "analytics.signal.scanner.get_funding_rates", return_value=pd.DataFrame()
        ),
        patch("analytics.signal.scanner.scan_symbol", return_value=[event]),
    ):
        run_scan_cycle(
            conn=conn,
            symbols=[event.symbol],
            timeframes=[event.timeframe],
            strategies=[event.strategy],
            store=store,
            tp_r=2.0,
            sl_pct=0.02,
            min_sl_pct=0.0,
        )

    cols = ["sl_price", "tp_price", "rr_ratio", "entry_price"]
    row = conn.execute(
        f"SELECT {', '.join(cols)} FROM signal_alert_outcomes "
        "WHERE strategy = ? AND direction = ?",
        [event.strategy, event.direction],
    ).fetchone()
    assert row is not None, "no outcome row persisted"
    return dict(zip(cols, row, strict=True))


class TestResolveOutcomeSlTp:
    """Pure per-event SL/TP resolver mirroring the alert formatter's fallback."""

    def test_long_uses_structural_sl_when_valid(self) -> None:
        sl, tp = _resolve_outcome_sl_tp(
            direction="long",
            entry=100.0,
            struct_sl=95.0,
            struct_tp=0.0,
            eff_sl_pct=0.02,
            min_sl_pct=0.0,
            tp_r=2.0,
        )
        # structural sl_dist = 100 - 95 = 5 → sl = 95, tp = 100 + 5*2 = 110
        assert math.isclose(sl, 95.0)
        assert math.isclose(tp, 110.0)

    def test_long_falls_back_to_pct_when_no_structural(self) -> None:
        sl, tp = _resolve_outcome_sl_tp(
            direction="long",
            entry=100.0,
            struct_sl=0.0,
            struct_tp=0.0,
            eff_sl_pct=0.02,
            min_sl_pct=0.0,
            tp_r=2.0,
        )
        # no structural → fallback sl_dist = 100*0.02 = 2 → sl = 98, tp = 100 + 2*2 = 104
        assert math.isclose(sl, 98.0)
        assert math.isclose(tp, 104.0)

    def test_short_falls_back_to_pct_when_no_structural(self) -> None:
        sl, tp = _resolve_outcome_sl_tp(
            direction="short",
            entry=100.0,
            struct_sl=0.0,
            struct_tp=0.0,
            eff_sl_pct=0.02,
            min_sl_pct=0.0,
            tp_r=2.0,
        )
        # fallback sl_dist = 2 → sl = 102, tp = 100 - 2*2 = 96
        assert math.isclose(sl, 102.0)
        assert math.isclose(tp, 96.0)

    def test_short_uses_structural_sl_when_valid(self) -> None:
        sl, tp = _resolve_outcome_sl_tp(
            direction="short",
            entry=100.0,
            struct_sl=104.0,
            struct_tp=0.0,
            eff_sl_pct=0.02,
            min_sl_pct=0.0,
            tp_r=2.0,
        )
        # structural sl_dist = 104 - 100 = 4 → sl = 104, tp = 100 - 4*2 = 92
        assert math.isclose(sl, 104.0)
        assert math.isclose(tp, 92.0)

    def test_min_sl_floor_widens_tiny_structural(self) -> None:
        sl, tp = _resolve_outcome_sl_tp(
            direction="long",
            entry=100.0,
            struct_sl=99.9,  # sl_dist 0.1 < floor
            struct_tp=0.0,
            eff_sl_pct=0.02,
            min_sl_pct=0.01,  # floor = 1.0
            tp_r=2.0,
        )
        # floored sl_dist = max(0.1, 1.0) = 1.0 → sl = 99, tp = 100 + 1*2 = 102
        assert math.isclose(sl, 99.0)
        assert math.isclose(tp, 102.0)

    def test_structural_tp_preferred_over_sl_dist(self) -> None:
        sl, tp = _resolve_outcome_sl_tp(
            direction="long",
            entry=100.0,
            struct_sl=95.0,
            struct_tp=120.0,  # valid structural TP wins over 100+5*2=110
            eff_sl_pct=0.02,
            min_sl_pct=0.0,
            tp_r=2.0,
        )
        assert math.isclose(sl, 95.0)
        assert math.isclose(tp, 120.0)

    def test_short_structural_tp_preferred(self) -> None:
        sl, tp = _resolve_outcome_sl_tp(
            direction="short",
            entry=100.0,
            struct_sl=104.0,
            struct_tp=80.0,  # valid (0 < 80 < 100) wins over 100-4*2=92
            eff_sl_pct=0.02,
            min_sl_pct=0.0,
            tp_r=2.0,
        )
        assert math.isclose(sl, 104.0)
        assert math.isclose(tp, 80.0)


class TestWriterNeverNull:
    """The outcome-ledger writer persists non-NULL SL/TP for every fired event."""

    def test_outcome_row_never_null_for_no_structural_long(self, tmp_path: Any) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="1h",
            strategy="bos",
            direction="long",
            reason="test",
            open_time=_HOUR,
            price=100.0,
            sl_price=0.0,  # no structural SL — previously wrote NULL
        )
        row = _run_cycle_with_event(event, tmp_path)
        assert row["sl_price"] is not None
        assert row["tp_price"] is not None
        assert row["rr_ratio"] is not None
        # pct fallback: sl_dist = 100*0.02 = 2 → sl 98, tp 100 + 2*2 = 104
        assert math.isclose(row["sl_price"], 98.0)
        assert math.isclose(row["tp_price"], 104.0)
        assert math.isclose(row["rr_ratio"], 2.0)

    def test_outcome_row_never_null_for_no_structural_short(
        self, tmp_path: Any
    ) -> None:
        event = SignalEvent(
            symbol="BTCUSDT",
            timeframe="1h",
            strategy="bos",
            direction="short",
            reason="test",
            open_time=_HOUR,
            price=100.0,
            sl_price=0.0,
        )
        row = _run_cycle_with_event(event, tmp_path)
        assert row["sl_price"] is not None
        assert row["tp_price"] is not None
        assert row["rr_ratio"] is not None
        # sl_dist = 2 → sl 102, tp 100 - 2*2 = 96
        assert math.isclose(row["sl_price"], 102.0)
        assert math.isclose(row["tp_price"], 96.0)
        assert math.isclose(row["rr_ratio"], 2.0)

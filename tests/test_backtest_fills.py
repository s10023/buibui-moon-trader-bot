"""A bar that opens beyond a level fills THERE, not at the level (wifey #242 / ST68).

The engine booked `exit_price = sl_price` / `tp_price` exactly, whatever the bar
did. A bar that *opens* through the level never filled at that level: a
stop-market and a resting limit both fill at the open. The rule is symmetric —
the favourable mirror (a target gapped through) is the larger of the two effects.
"""

from __future__ import annotations

import pandas as pd
import pytest

from analytics.backtest.fills import gap_fill_price, level_is_on_the_expected_side
from analytics.backtest_lib import run_backtest
from analytics.signal.outcome_backfill import _scan_forward
from tests.conftest import _candle, _make_ohlcv

_BASE_TIME = 1_700_000_000_000


def _make_signals(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["open_time", "direction", "reason"])


class TestGapFillPrice:
    """`gap_fill_price` prices the fill, not the level."""

    def test_long_stop_gapped_through_fills_at_the_open(self) -> None:
        # Long from 100, stop at 95. The bar opens at 92 — the stop was already
        # blown when the bar printed, so a stop-market fills at 92, not 95.
        assert (
            gap_fill_price(
                entry=100.0, level=95.0, bar_open=92.0, crossed_when="at_or_below"
            )
            == 92.0
        )

    def test_long_stop_merely_wicked_through_fills_at_the_level(self) -> None:
        # Same stop, but the bar opens at 98 and only later trades down to it.
        # The resting stop triggers AT the level, so the level is the fill.
        assert (
            gap_fill_price(
                entry=100.0, level=95.0, bar_open=98.0, crossed_when="at_or_below"
            )
            == 95.0
        )

    def test_long_target_gapped_through_fills_at_the_open(self) -> None:
        # The favourable mirror: long from 100, target 110, bar opens at 113.
        # A resting limit at 110 fills at 113 — better than booked.
        assert (
            gap_fill_price(
                entry=100.0, level=110.0, bar_open=113.0, crossed_when="at_or_above"
            )
            == 113.0
        )

    def test_short_stop_gapped_through_fills_at_the_open(self) -> None:
        # Short from 100, stop at 105, bar opens at 108.
        assert (
            gap_fill_price(
                entry=100.0, level=105.0, bar_open=108.0, crossed_when="at_or_above"
            )
            == 108.0
        )

    def test_short_target_gapped_through_fills_at_the_open(self) -> None:
        # Short from 100, target 90, bar opens at 87.
        assert (
            gap_fill_price(
                entry=100.0, level=90.0, bar_open=87.0, crossed_when="at_or_below"
            )
            == 87.0
        )

    def test_open_exactly_at_the_level_fills_at_the_level(self) -> None:
        # Boundary: opening exactly at the level is not a gap. Both readings
        # agree on the number, so this pins that they keep agreeing.
        assert (
            gap_fill_price(
                entry=100.0, level=95.0, bar_open=95.0, crossed_when="at_or_below"
            )
            == 95.0
        )

    def test_a_malformed_level_is_never_priced_as_a_gap(self) -> None:
        # A long whose "stop" sits ABOVE entry is a malformed row. Every bar
        # would look gapped-through, so the guard refuses the override and
        # books the level unchanged rather than inventing a fill.
        assert (
            gap_fill_price(
                entry=100.0, level=105.0, bar_open=92.0, crossed_when="at_or_below"
            )
            == 105.0
        )


class TestLevelIsOnTheExpectedSide:
    """The guard that stops a malformed row being priced as a gap."""

    @pytest.mark.parametrize(
        ("entry", "level", "crossed_when"),
        [
            (100.0, 95.0, "at_or_below"),  # long stop below entry
            (100.0, 110.0, "at_or_above"),  # long target above entry
            (100.0, 105.0, "at_or_above"),  # short stop above entry
            (100.0, 90.0, "at_or_below"),  # short target below entry
        ],
    )
    def test_well_formed_levels_pass(
        self, entry: float, level: float, crossed_when: str
    ) -> None:
        assert level_is_on_the_expected_side(
            entry=entry,
            level=level,
            crossed_when=crossed_when,  # type: ignore[arg-type]
        )

    @pytest.mark.parametrize(
        ("entry", "level", "crossed_when"),
        [
            (100.0, 105.0, "at_or_below"),  # "stop" above entry on a long
            (100.0, 90.0, "at_or_above"),  # "target" below entry on a long
        ],
    )
    def test_malformed_levels_are_rejected(
        self, entry: float, level: float, crossed_when: str
    ) -> None:
        assert not level_is_on_the_expected_side(
            entry=entry,
            level=level,
            crossed_when=crossed_when,  # type: ignore[arg-type]
        )


class TestEngineBooksTheGapFill:
    """`run_backtest` books the price the bar filled at, not the level.

    Defaults: sl_pct 2% and tp_r 2.0, so a long entered at 100 carries
    sl=98 and tp=104; a short carries sl=102 and tp=96.
    """

    def test_long_stop_gapped_through_books_the_open(self) -> None:
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),  # signal candle
                _candle(_BASE_TIME + 1, 100, 103, 99, 101),  # entry at open=100, safe
                _candle(_BASE_TIME + 2, 94, 96, 92, 93),  # opens at 94, BELOW sl=98
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "long", "reason": "test"}]
        )
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg")

        trade = result.trades[0]
        assert trade.outcome == "loss"
        # The stop was already blown when the bar printed: a stop-market fills
        # at 94, so the loss is worse than the declared −1R.
        assert trade.exit_price == 94.0

    def test_long_target_gapped_through_books_the_open(self) -> None:
        # The favourable mirror — wifey measured this as the LARGER of the two.
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),
                _candle(_BASE_TIME + 1, 100, 103, 99, 101),
                _candle(_BASE_TIME + 2, 107, 109, 106, 108),  # opens ABOVE tp=104
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "long", "reason": "test"}]
        )
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg")

        trade = result.trades[0]
        assert trade.outcome == "win"
        assert trade.exit_price == 107.0

    def test_short_stop_gapped_through_books_the_open(self) -> None:
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 98),
                _candle(_BASE_TIME + 1, 100, 101, 99, 100),  # entry at open=100, safe
                _candle(_BASE_TIME + 2, 105, 107, 104, 106),  # opens ABOVE sl=102
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "short", "reason": "test"}]
        )
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg")

        trade = result.trades[0]
        assert trade.outcome == "loss"
        assert trade.exit_price == 105.0

    def test_a_bar_that_merely_trades_to_the_level_still_books_the_level(self) -> None:
        # The no-change control: without it, "book the open always" would pass
        # every test above and silently rewrite the ordinary path.
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),
                _candle(_BASE_TIME + 1, 100, 103, 99, 101),
                _candle(_BASE_TIME + 2, 101, 102, 97, 99),  # opens 101, wicks to 97
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "long", "reason": "test"}]
        )
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg")

        trade = result.trades[0]
        assert trade.outcome == "loss"
        assert trade.exit_price == 98.0


class TestLiveResolverBooksTheGapFill:
    """The live resolver must price the same rule, or the two books drift.

    `_scan_forward` returned a hardcoded −1.0 on a stop — the DECLARED risk —
    which is `AGENTS.md`'s *"raw stays exactly −1.0 … neither half expresses
    gap risk"* stated as code. A stop gapped through is worse than −1R.
    """

    @staticmethod
    def _bars(rows: list[tuple[int, float, float, float]]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"open_time": ts, "open": o, "high": h, "low": lo}
                for ts, o, h, lo in rows
            ]
        )

    def test_long_stop_gapped_through_is_worse_than_minus_one_r(self) -> None:
        # entry 100, sl 98 → risk 2. The exit bar opens at 94, so the fill is
        # 6 below entry: −3R, not the declared −1R.
        bars = self._bars(
            [
                (1_000, 100.0, 105.0, 95.0),  # signal candle
                (2_000, 100.0, 103.0, 99.0),  # entry bar, safe
                (3_000, 94.0, 96.0, 92.0),  # gaps below sl=98
            ]
        )
        outcome, outcome_r, exit_ts = _scan_forward(
            bars, 1_000, "long", 100.0, 98.0, 104.0, 2.0, 10
        )
        assert outcome == "loss"
        assert exit_ts == 3_000
        assert outcome_r == pytest.approx(-3.0)

    def test_long_target_gapped_through_is_better_than_the_booked_target(self) -> None:
        bars = self._bars(
            [
                (1_000, 100.0, 105.0, 95.0),
                (2_000, 100.0, 103.0, 99.0),
                (3_000, 107.0, 109.0, 106.0),  # gaps above tp=104
            ]
        )
        outcome, outcome_r, exit_ts = _scan_forward(
            bars, 1_000, "long", 100.0, 98.0, 104.0, 2.0, 10
        )
        assert outcome == "win"
        # Fill at 107 → (107−100)/2 = +3.5R, against the booked +2R.
        assert outcome_r == pytest.approx(3.5)

    def test_an_ordinary_stop_still_resolves_at_exactly_minus_one_r(self) -> None:
        # The no-change control: the ordinary path must stay byte-identical,
        # or every historical row is restated for nothing.
        bars = self._bars(
            [
                (1_000, 100.0, 105.0, 95.0),
                (2_000, 100.0, 103.0, 99.0),
                (3_000, 101.0, 102.0, 97.0),  # opens 101, wicks to 97
            ]
        )
        outcome, outcome_r, _ = _scan_forward(
            bars, 1_000, "long", 100.0, 98.0, 104.0, 2.0, 10
        )
        assert outcome == "loss"
        assert outcome_r == pytest.approx(-1.0)

"""Tests for analytics.cme_gap_lib."""

from __future__ import annotations

import pandas as pd
import pytest

from analytics.cme_gap_lib import CMEGap, cme_gap_alert_warning, get_recent_cme_gap

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Reference "now": Wednesday 2024-10-08 12:00:00 UTC (Unix: 1728388800)
# Most recent Friday 21:00 UTC before this = 2024-10-04 21:00:00 UTC (1728075600)
# CME open = +49h = 2024-10-06 22:00:00 UTC (1728252000)
_NOW_SEC = 1728388800.0
_FRI_CLOSE_SEC = 1728075600.0  # Fri 2024-10-04 21:00 UTC
_MON_OPEN_SEC = 1728252000.0  # Sun 2024-10-06 22:00 UTC


def _ms(sec: float) -> int:
    return int(sec * 1000)


def _make_candle(open_time_sec: float, o: float, h: float, lo: float, c: float) -> dict:
    return {
        "open_time": _ms(open_time_sec),
        "open": o,
        "high": h,
        "low": lo,
        "close": c,
    }


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# get_recent_cme_gap
# ---------------------------------------------------------------------------


class TestGetRecentCMEGap:
    def test_gap_up(self) -> None:
        """Mon open > Fri close → bullish gap, gap_up=True."""
        fri_close = 95_000.0
        mon_open = 96_000.0
        rows = [
            _make_candle(_FRI_CLOSE_SEC - 3600, 94_000, 95_500, 93_500, fri_close),
            _make_candle(_MON_OPEN_SEC, mon_open, 96_500, 95_500, 96_200),
        ]
        gap = get_recent_cme_gap(_df(rows), _now_sec=_NOW_SEC)
        assert gap is not None
        assert gap.gap_low == pytest.approx(fri_close)
        assert gap.gap_high == pytest.approx(mon_open)
        assert gap.gap_up is True
        assert gap.filled is False

    def test_gap_down(self) -> None:
        """Mon open < Fri close → bearish gap, gap_up=False."""
        fri_close = 95_000.0
        mon_open = 94_000.0
        rows = [
            _make_candle(_FRI_CLOSE_SEC - 3600, 95_500, 96_000, 94_500, fri_close),
            _make_candle(_MON_OPEN_SEC, mon_open, 94_500, 93_500, 94_200),
        ]
        gap = get_recent_cme_gap(_df(rows), _now_sec=_NOW_SEC)
        assert gap is not None
        assert gap.gap_low == pytest.approx(mon_open)
        assert gap.gap_high == pytest.approx(fri_close)
        assert gap.gap_up is False
        assert gap.filled is False

    def test_gap_up_filled(self) -> None:
        """Bullish gap is filled when a subsequent low dips to/below fri_close."""
        fri_close = 95_000.0
        mon_open = 96_000.0
        rows = [
            _make_candle(_FRI_CLOSE_SEC - 3600, 94_000, 95_500, 93_500, fri_close),
            _make_candle(
                _MON_OPEN_SEC, mon_open, 96_500, 94_800, 96_200
            ),  # low 94_800 < gap_low 95_000
        ]
        gap = get_recent_cme_gap(_df(rows), _now_sec=_NOW_SEC)
        assert gap is not None
        assert gap.filled is True

    def test_gap_down_filled(self) -> None:
        """Bearish gap is filled when a subsequent high reaches/exceeds fri_close."""
        fri_close = 95_000.0
        mon_open = 94_000.0
        rows = [
            _make_candle(_FRI_CLOSE_SEC - 3600, 95_500, 96_000, 94_500, fri_close),
            _make_candle(
                _MON_OPEN_SEC, mon_open, 95_200, 93_500, 94_800
            ),  # high 95_200 > gap_high 95_000
        ]
        gap = get_recent_cme_gap(_df(rows), _now_sec=_NOW_SEC)
        assert gap is not None
        assert gap.filled is True

    def test_empty_df_returns_none(self) -> None:
        gap = get_recent_cme_gap(pd.DataFrame(), _now_sec=_NOW_SEC)
        assert gap is None

    def test_no_friday_candle_returns_none(self) -> None:
        """Only candles after the CME open — no pre-close candle available."""
        rows = [_make_candle(_MON_OPEN_SEC + 3600, 95_000, 96_000, 94_500, 95_500)]
        gap = get_recent_cme_gap(_df(rows), _now_sec=_NOW_SEC)
        assert gap is None

    def test_inside_closure_window_returns_none(self) -> None:
        """No Monday candle yet (currently inside the CME closure window)."""
        rows = [_make_candle(_FRI_CLOSE_SEC - 3600, 94_000, 95_500, 93_500, 95_000)]
        gap = get_recent_cme_gap(_df(rows), _now_sec=_NOW_SEC)
        assert gap is None

    def test_trivially_small_gap_returns_none(self) -> None:
        """Gap smaller than 0.05% of price is ignored."""
        fri_close = 95_000.0
        mon_open = 95_010.0  # 0.011% gap — below 0.05% threshold
        rows = [
            _make_candle(_FRI_CLOSE_SEC - 3600, 94_800, 95_500, 94_500, fri_close),
            _make_candle(_MON_OPEN_SEC, mon_open, 95_500, 94_800, 95_300),
        ]
        gap = get_recent_cme_gap(_df(rows), _now_sec=_NOW_SEC)
        assert gap is None


# ---------------------------------------------------------------------------
# cme_gap_alert_warning
# ---------------------------------------------------------------------------


class TestCMEGapAlertWarning:
    def _gap(self, low: float, high: float, gap_up: bool, filled: bool) -> CMEGap:
        return CMEGap(gap_low=low, gap_high=high, gap_up=gap_up, filled=filled)

    def test_none_gap_returns_none(self) -> None:
        assert cme_gap_alert_warning(None, "long", 95_000, 98_000) is None

    def test_filled_gap_returns_none(self) -> None:
        gap = self._gap(93_000, 93_500, gap_up=True, filled=True)
        assert cme_gap_alert_warning(gap, "long", 95_000, 98_000) is None

    # LONG scenarios
    def test_long_gap_below_warns(self) -> None:
        """Unfilled gap entirely below entry → warn for LONG."""
        gap = self._gap(93_000, 93_500, gap_up=True, filled=False)
        result = cme_gap_alert_warning(gap, "long", 95_000, 98_000)
        assert result is not None
        assert "CME gap" in result
        assert "unfilled below" in result

    def test_long_gap_above_no_warn(self) -> None:
        """Gap above entry is favourable for LONG — no warning."""
        gap = self._gap(96_000, 96_500, gap_up=True, filled=False)
        result = cme_gap_alert_warning(gap, "long", 95_000, 98_000)
        assert result is None

    def test_long_gap_partially_overlapping_entry_no_warn(self) -> None:
        """Gap straddles the entry price — high >= entry, not strictly below."""
        gap = self._gap(94_500, 95_500, gap_up=True, filled=False)
        result = cme_gap_alert_warning(gap, "long", 95_000, 98_000)
        assert result is None  # high (95_500) >= entry (95_000) → not below

    # SHORT scenarios
    def test_short_gap_in_tp_path_warns(self) -> None:
        """Gap between entry and TP for SHORT → warn."""
        gap = self._gap(93_000, 93_500, gap_up=False, filled=False)
        # entry=95_000, tp=92_000 → gap 93_000–93_500 is between them
        result = cme_gap_alert_warning(gap, "short", 95_000, 92_000)
        assert result is not None
        assert "CME gap" in result
        assert "TP path" in result

    def test_short_gap_below_tp_no_warn(self) -> None:
        """Gap is beyond (below) the TP — favourable territory, no warning."""
        gap = self._gap(90_000, 90_500, gap_up=False, filled=False)
        result = cme_gap_alert_warning(gap, "short", 95_000, 92_000)
        assert result is None  # gap_high (90_500) < tp (92_000)

    def test_short_gap_above_entry_no_warn(self) -> None:
        """Gap is above the entry for SHORT — not in the downside path."""
        gap = self._gap(96_000, 96_500, gap_up=True, filled=False)
        result = cme_gap_alert_warning(gap, "short", 95_000, 92_000)
        assert result is None  # gap_low (96_000) >= entry (95_000)

    def test_short_zero_tp_no_warn(self) -> None:
        """tp_price=0 means unknown — skip the gap-in-path check."""
        gap = self._gap(93_000, 93_500, gap_up=False, filled=False)
        result = cme_gap_alert_warning(gap, "short", 95_000, 0.0)
        assert result is None

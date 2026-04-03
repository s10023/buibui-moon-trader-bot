"""Tests for _get_session_label in alert_formatter."""

from datetime import UTC, datetime

from signals.alert_formatter import (
    SignalEvent,
    StatsContext,
    _format_stats_line,
    _get_session_label,
    format_signal_alert,
)

_UTC = UTC


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2024, 1, 15, hour, minute, tzinfo=_UTC)


def _utc_summer(hour: int, minute: int = 0) -> datetime:
    """2024-07-15 is in EDT (UTC-4) — tests DST handling."""
    return datetime(2024, 7, 15, hour, minute, tzinfo=_UTC)


class TestGetSessionLabel:
    # --- Asia Kill Zone: 8 PM – 11 PM ET ---
    # Winter (EST, UTC-5): 20:00 ET = 01:00 UTC next day; use Jan 14 UTC to land on Jan 15 ET
    # For simplicity: test in UTC hours that correspond to ET 8 PM–11 PM in winter
    # 8 PM ET (EST) = 01:00 UTC  |  11 PM ET (EST) = 04:00 UTC (exclusive end)

    def test_asia_start(self) -> None:
        # 01:00 UTC = 8 PM ET (EST) — Asia KZ start
        assert _get_session_label(_utc(1, 0)) == "Asia"

    def test_asia_mid(self) -> None:
        # 02:30 UTC = 9:30 PM ET (EST)
        assert _get_session_label(_utc(2, 30)) == "Asia"

    def test_asia_end(self) -> None:
        # 03:59 UTC = 10:59 PM ET (EST) — last minute of Asia KZ
        assert _get_session_label(_utc(3, 59)) == "Asia"

    # --- London Kill Zone: 2 AM – 5 AM ET ---
    # Winter (EST, UTC-5): 2 AM ET = 07:00 UTC  |  5 AM ET = 10:00 UTC (exclusive end)

    def test_london_start(self) -> None:
        # 07:00 UTC = 2 AM ET (EST) — London KZ start
        assert _get_session_label(_utc(7, 0)) == "London"

    def test_london_mid(self) -> None:
        # 08:30 UTC = 3:30 AM ET (EST) — around London open
        assert _get_session_label(_utc(8, 30)) == "London"

    def test_london_end(self) -> None:
        # 09:59 UTC = 4:59 AM ET (EST) — last minute of London KZ
        assert _get_session_label(_utc(9, 59)) == "London"

    # --- NY Kill Zone: 7 AM – 10 AM ET ---
    # Winter (EST, UTC-5): 7 AM ET = 12:00 UTC  |  10 AM ET = 15:00 UTC (exclusive end)

    def test_ny_start(self) -> None:
        # 12:00 UTC = 7 AM ET (EST) — NY KZ start
        assert _get_session_label(_utc(12, 0)) == "NY"

    def test_ny_mid(self) -> None:
        # 14:30 UTC = 9:30 AM ET (EST) — NY open itself
        assert _get_session_label(_utc(14, 30)) == "NY"

    def test_ny_end(self) -> None:
        # 14:59 UTC = 9:59 AM ET (EST) — last minute of NY KZ
        assert _get_session_label(_utc(14, 59)) == "NY"

    # --- Outside windows ---

    def test_outside_between_asia_and_london(self) -> None:
        # 05:00 UTC = midnight ET (EST) — between Asia KZ end and London KZ start
        assert _get_session_label(_utc(5, 0)) == ""

    def test_outside_between_london_and_ny(self) -> None:
        # 10:00 UTC = 5 AM ET (EST) — between London KZ end and NY KZ start
        assert _get_session_label(_utc(10, 0)) == ""

    def test_outside_after_ny(self) -> None:
        # 15:00 UTC = 10 AM ET (EST) — just after NY KZ ends
        assert _get_session_label(_utc(15, 0)) == ""

    def test_outside_before_asia(self) -> None:
        # 00:30 UTC = 7:30 PM ET (EST) — before Asia KZ starts at 8 PM
        assert _get_session_label(_utc(0, 30)) == ""

    # --- DST: Summer 2024 (EDT, UTC-4) ---
    # Asia KZ:   8 PM EDT = 00:00 UTC  |  11 PM EDT = 03:00 UTC (exclusive)
    # London KZ: 2 AM EDT = 06:00 UTC  |   5 AM EDT = 09:00 UTC (exclusive)
    # NY KZ:     7 AM EDT = 11:00 UTC  |  10 AM EDT = 14:00 UTC (exclusive)

    def test_asia_summer_dst(self) -> None:
        # 00:30 UTC on 2024-07-15 = 8:30 PM EDT — inside Asia KZ
        assert _get_session_label(_utc_summer(0, 30)) == "Asia"

    def test_london_summer_dst(self) -> None:
        # 06:30 UTC on 2024-07-15 = 2:30 AM EDT — inside London KZ
        assert _get_session_label(_utc_summer(6, 30)) == "London"

    def test_ny_summer_dst(self) -> None:
        # 13:30 UTC on 2024-07-15 = 9:30 AM EDT — NY open, inside NY KZ
        assert _get_session_label(_utc_summer(13, 30)) == "NY"

    def test_outside_summer_that_was_ny_in_winter(self) -> None:
        # 14:30 UTC on 2024-07-15 = 10:30 AM EDT — outside NY KZ in summer
        # (same UTC time IS inside NY KZ in winter: 14:30 UTC = 9:30 AM EST)
        assert _get_session_label(_utc_summer(14, 30)) == ""


class TestFormatSignalAlertSessionTag:
    def _make_event(self, open_time_ms: int) -> SignalEvent:
        return SignalEvent(
            symbol="BTCUSDT",
            timeframe="1h",
            strategy="fvg",
            direction="long",
            reason="fvg_long@43200.00-43350.00",
            open_time=open_time_ms,
            price=43000.0,
            sl_price=42000.0,
        )

    def test_session_tag_in_message_london(self) -> None:
        # 2024-01-15 08:00 UTC = 3 AM ET (EST) — London Kill Zone
        dt = datetime(2024, 1, 15, 8, 0, tzinfo=_UTC)
        ts_ms = int(dt.timestamp() * 1000)
        msg = format_signal_alert(self._make_event(ts_ms))
        assert "🇬🇧 London Kill Zone" in msg

    def test_session_tag_in_message_ny(self) -> None:
        # 2024-01-15 14:30 UTC = 9:30 AM ET (EST) — NY Kill Zone (NY open)
        dt = datetime(2024, 1, 15, 14, 30, tzinfo=_UTC)
        ts_ms = int(dt.timestamp() * 1000)
        msg = format_signal_alert(self._make_event(ts_ms))
        assert "🗽 NY Kill Zone" in msg

    def test_no_session_tag_outside_windows(self) -> None:
        # 2024-01-15 11:00 UTC = 6 AM ET (EST) — between London KZ and NY KZ
        dt = datetime(2024, 1, 15, 11, 0, tzinfo=_UTC)
        ts_ms = int(dt.timestamp() * 1000)
        msg = format_signal_alert(self._make_event(ts_ms))
        assert "Kill Zone" not in msg


class TestStatsContextFormat:
    """Tests for _format_stats_line with new plain-English 2-line format."""

    def _ctx(
        self,
        dow: str = "Monday",
        p1_low: float = 0.69,
        bull: float = 0.67,
        adr: float = 0.043,
        consumed: float | None = 0.82,
        peak_hi_dow: int | None = 23,
        peak_lo_dow: int | None = 8,
        wk_low: float | None = 0.78,
        wk_high: float | None = 0.65,
        wk_low_cond: float | None = None,
        wk_high_cond: float | None = None,
        wk_bucket: str | None = None,
    ) -> StatsContext:
        return StatsContext(
            today_dow=dow,
            p1_low_pct_today=p1_low,
            adr_14=adr,
            adr_consumed_pct=consumed,
            peak_high_hour_myt=14,
            peak_low_hour_myt=8,
            bull_pct_today=bull,
            avg_return_today=0.01,
            peak_high_hour_dow=peak_hi_dow,
            peak_low_hour_dow=peak_lo_dow,
            wk_low_still_ahead_pct=wk_low,
            wk_high_still_ahead_pct=wk_high,
            wk_low_still_ahead_conditioned_pct=wk_low_cond,
            wk_high_still_ahead_conditioned_pct=wk_high_cond,
            wk_move_bucket=wk_bucket,
        )

    def test_long_line1_contains_bull_p1_adr(self) -> None:
        line = _format_stats_line(self._ctx(), "long")
        assert "Mon closes bullish 67%" in line
        # p1_low=0.69 → still_ahead for long = 1-0.69 = 31%
        assert "Low still ahead 31% of Mondays" in line
        assert (
            "4.3%" in line
        )  # ADR value present (bar separates "ADR" prefix from value)
        assert "82%" in line

    def test_long_line2_peak_and_weekly(self) -> None:
        line = _format_stats_line(self._ctx(), "long")
        assert "TP window: high ~23:00 MYT on Mondays" in line
        assert "Weekly low: 78% still ahead" in line

    def test_short_line1_high_first(self) -> None:
        line = _format_stats_line(self._ctx(), "short")
        # p1_low=0.69 → still_ahead for short = p1_low = 69%
        assert "High still ahead 69% of Mondays" in line

    def test_short_line2_low_peak_and_weekly_high(self) -> None:
        line = _format_stats_line(self._ctx(), "short")
        assert "TP window: low ~08:00 MYT on Mondays" in line
        assert "Weekly high: 65% still ahead" in line

    def test_no_line2_when_optional_fields_none(self) -> None:
        ctx = self._ctx(peak_hi_dow=None, peak_lo_dow=None, wk_low=None, wk_high=None)
        line = _format_stats_line(ctx, "long")
        assert "🎯" not in line

    def test_adr_consumed_none(self) -> None:
        ctx = self._ctx(consumed=None)
        line = _format_stats_line(ctx, "long")
        assert "used" not in line

    def test_two_lines_separated_by_newline(self) -> None:
        line = _format_stats_line(self._ctx(), "long")
        lines = line.split("\n")
        assert len(lines) == 2
        assert lines[0].startswith("📐")
        assert lines[1].startswith("🎯")

    def test_long_uses_conditioned_pct_over_unconditional(self) -> None:
        ctx = self._ctx(wk_low=0.78, wk_low_cond=0.62, wk_bucket="medium")
        line = _format_stats_line(ctx, "long")
        assert "Weekly low: 62% still ahead (medium move)" in line
        assert "78%" not in line

    def test_short_uses_conditioned_pct_over_unconditional(self) -> None:
        ctx = self._ctx(wk_high=0.65, wk_high_cond=0.44, wk_bucket="large")
        line = _format_stats_line(ctx, "short")
        assert "Weekly high: 44% still ahead (large move)" in line
        assert "65%" not in line

    def test_falls_back_to_unconditional_when_conditioned_none(self) -> None:
        # conditioned fields absent — should fall back to plain unconditional
        ctx = self._ctx(wk_low=0.78, wk_low_cond=None, wk_bucket=None)
        line = _format_stats_line(ctx, "long")
        assert "Weekly low: 78% still ahead" in line
        assert "move)" not in line

    def test_conditioned_pct_requires_bucket_to_activate(self) -> None:
        # conditioned pct present but bucket missing → fall back to unconditional
        ctx = self._ctx(wk_low=0.78, wk_low_cond=0.62, wk_bucket=None)
        line = _format_stats_line(ctx, "long")
        assert "Weekly low: 78% still ahead" in line
        assert "62%" not in line


class TestLowVolumeWarning:
    _TS_MS = int(datetime(2024, 1, 15, 11, 0, tzinfo=_UTC).timestamp() * 1000)

    def _make_event(self, low_volume: bool) -> SignalEvent:
        return SignalEvent(
            symbol="BTCUSDT",
            timeframe="1h",
            strategy="engulfing",
            direction="long",
            reason="bullish_engulfing@43000.00",
            open_time=self._TS_MS,
            price=43000.0,
            sl_price=42000.0,
            low_volume=low_volume,
        )

    def test_low_volume_true_shows_warning(self) -> None:
        msg = format_signal_alert(self._make_event(low_volume=True))
        assert "⚠️ Low volume — weaker conviction" in msg

    def test_low_volume_false_no_warning(self) -> None:
        msg = format_signal_alert(self._make_event(low_volume=False))
        assert "⚠️ Low volume — weaker conviction" not in msg

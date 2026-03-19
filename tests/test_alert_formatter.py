"""Tests for _get_session_label in alert_formatter."""

from datetime import UTC, datetime

from signals.alert_formatter import (
    SignalEvent,
    _get_session_label,
    format_signal_alert,
)

_UTC = UTC


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2024, 1, 15, hour, minute, tzinfo=_UTC)


class TestGetSessionLabel:
    def test_asia_start(self) -> None:
        assert _get_session_label(_utc(20, 0)) == "Asia"

    def test_asia_mid(self) -> None:
        assert _get_session_label(_utc(22, 30)) == "Asia"

    def test_asia_end(self) -> None:
        assert _get_session_label(_utc(23, 59)) == "Asia"

    def test_london_start(self) -> None:
        assert _get_session_label(_utc(2, 0)) == "London"

    def test_london_mid(self) -> None:
        assert _get_session_label(_utc(3, 30)) == "London"

    def test_london_end(self) -> None:
        assert _get_session_label(_utc(4, 59)) == "London"

    def test_ny_start(self) -> None:
        assert _get_session_label(_utc(9, 30)) == "NY"

    def test_ny_mid(self) -> None:
        assert _get_session_label(_utc(10, 45)) == "NY"

    def test_ny_end(self) -> None:
        assert _get_session_label(_utc(11, 59)) == "NY"

    def test_outside_before_ny(self) -> None:
        assert _get_session_label(_utc(9, 29)) == ""

    def test_outside_after_ny(self) -> None:
        assert _get_session_label(_utc(12, 0)) == ""

    def test_outside_after_london(self) -> None:
        assert _get_session_label(_utc(5, 0)) == ""

    def test_outside_before_london(self) -> None:
        assert _get_session_label(_utc(1, 59)) == ""

    def test_outside_midnight_to_2am(self) -> None:
        assert _get_session_label(_utc(0, 30)) == ""


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
        # 2024-01-15 03:00 UTC — London session (11:00 MYT)
        dt = datetime(2024, 1, 15, 3, 0, tzinfo=_UTC)
        ts_ms = int(dt.timestamp() * 1000)
        msg = format_signal_alert(self._make_event(ts_ms))
        assert "🇬🇧 London Kill Zone" in msg

    def test_session_tag_in_message_ny(self) -> None:
        # 2024-01-15 10:00 UTC — NY session (18:00 MYT)
        dt = datetime(2024, 1, 15, 10, 0, tzinfo=_UTC)
        ts_ms = int(dt.timestamp() * 1000)
        msg = format_signal_alert(self._make_event(ts_ms))
        assert "🗽 NY Kill Zone" in msg

    def test_no_session_tag_outside_windows(self) -> None:
        # 2024-01-15 07:00 UTC — outside all sessions (15:00 MYT)
        dt = datetime(2024, 1, 15, 7, 0, tzinfo=_UTC)
        ts_ms = int(dt.timestamp() * 1000)
        msg = format_signal_alert(self._make_event(ts_ms))
        assert "Kill Zone" not in msg

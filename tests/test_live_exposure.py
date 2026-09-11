"""Live alert frequency per swept cell — ST134 section 5 item 2."""

import duckdb
import pytest

from analytics.live_exposure import alerts_per_week

_MS_PER_DAY = 86_400_000
_WEEK = 7 * _MS_PER_DAY


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE signal_alert_outcomes (
            signal_id TEXT PRIMARY KEY,
            symbol TEXT, tf TEXT, strategy TEXT, direction TEXT,
            fired_at_ms BIGINT
        )
    """)
    return conn


def _fire(conn: duckdb.DuckDBPyConnection, sid: str, ts: int, **kw: str) -> None:
    row = {"symbol": "BTCUSDT", "tf": "15m", "strategy": "bos", "direction": "long"}
    row.update(kw)
    conn.execute(
        "INSERT INTO signal_alert_outcomes VALUES (?, ?, ?, ?, ?, ?)",
        [sid, row["symbol"], row["tf"], row["strategy"], row["direction"], ts],
    )


class TestAlertsPerWeek:
    def test_counts_only_the_named_cell(self) -> None:
        conn = _conn()
        _fire(conn, "a", 0)
        _fire(conn, "b", _MS_PER_DAY)
        _fire(conn, "c", 2 * _MS_PER_DAY, strategy="fvg")
        _fire(conn, "d", 3 * _MS_PER_DAY, tf="1h")
        _fire(conn, "e", 4 * _MS_PER_DAY, symbol="ETHUSDT")
        exposure = alerts_per_week(
            conn,
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=0,
            now_ms=_WEEK,
        )
        assert exposure.rate == pytest.approx(2.0)

    def test_scales_to_a_weekly_rate(self) -> None:
        conn = _conn()
        for i in range(8):
            _fire(conn, f"s{i}", i * _MS_PER_DAY)
        exposure = alerts_per_week(
            conn,
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=0,
            now_ms=4 * _WEEK,
        )
        assert exposure.rate == pytest.approx(2.0)

    def test_excludes_fires_outside_the_window(self) -> None:
        conn = _conn()
        _fire(conn, "old", -_WEEK)
        _fire(conn, "in", _MS_PER_DAY)
        exposure = alerts_per_week(
            conn,
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=0,
            now_ms=_WEEK,
        )
        # "old" is excluded from the COUNT by the window filter, but it still
        # sets the LEDGER's earliest row at -1 week, which predates since_ms
        # (0) -- so there is nothing to clamp and the full 7-day window
        # stands: 1 counted alert / 1 week = 1.0/wk.
        assert exposure.rate == pytest.approx(1.0)
        assert exposure.window_start_ms == 0
        assert exposure.window_days == pytest.approx(7.0)
        assert exposure.first_fired_ms == _MS_PER_DAY

    def test_no_fires_is_zero_not_none(self) -> None:
        exposure = alerts_per_week(
            _conn(),
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=0,
            now_ms=_WEEK,
        )
        assert exposure.rate == 0.0
        assert exposure.first_fired_ms is None

    def test_zero_length_window_is_zero_not_a_division_error(self) -> None:
        conn = _conn()
        _fire(conn, "a", 0)
        exposure = alerts_per_week(
            conn,
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=100,
            now_ms=100,
        )
        assert exposure.rate == 0.0

    def test_reads_tf_not_timeframe(self) -> None:
        """Mutation guard: backtest_runs uses `timeframe`, this ledger uses `tf`.

        A query copied from the backtest side returns nothing and reads as a cell
        that never fires — silent, and in the direction that hides exposure.
        """
        conn = _conn()
        _fire(conn, "a", 0, tf="4h")
        assert alerts_per_week(
            conn,
            symbol="BTCUSDT",
            timeframe="4h",
            strategy="bos",
            since_ms=0,
            now_ms=_WEEK,
        ).rate == pytest.approx(1.0)


class TestAlertsPerWeekWindowRider:
    """ST134 Task 10 rider: the denominator must be visible, not guessed at.

    `since_ms` is the sweep's own `--since` (a backtest start date), not when
    the live daemon started recording ANYTHING. Dividing by the full
    `since_ms -> now_ms` span when the ledger's history starts later prices
    years with no possible ledger row into the denominator and
    systematically UNDERSTATES the rate.

    ⚠ The clamp targets the LEDGER's shared start, never this cell's own
    first alert — a per-cell clamp is unconditional (a cell's own first
    alert is always `>= since_ms` by construction of the query that finds
    it) and inflates the rate of the sparsest cells hardest. See
    `test_a_cell_that_fired_once_recently_does_not_report_an_absurd_rate`,
    which reproduces the exact defect this class replaced and its fix.
    """

    def test_window_clamps_to_the_ledgers_shared_start_not_this_cells_own(
        self,
    ) -> None:
        """The ledger as a WHOLE starts recording at day 10 (established by a
        DIFFERENT cell's earliest row); this cell's own first alert at day 17
        must not itself set the boundary."""
        conn = _conn()
        _fire(conn, "ledger-start", 10 * _MS_PER_DAY, strategy="fvg")
        _fire(conn, "a", 17 * _MS_PER_DAY)
        exposure = alerts_per_week(
            conn,
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=0,
            now_ms=24 * _MS_PER_DAY,
        )
        # Window is [10d, 24d) = 14 days = 2 weeks, from the LEDGER's shared
        # start -- NOT [17d, 24d) = 1 week, which is what a per-cell clamp on
        # this cell's own first alert would have produced.
        assert exposure.window_start_ms == 10 * _MS_PER_DAY
        assert exposure.window_days == pytest.approx(14.0)
        assert exposure.rate == pytest.approx(0.5)  # 1 alert / 2 weeks
        assert exposure.first_fired_ms == 17 * _MS_PER_DAY

    def test_a_cell_that_fired_once_recently_does_not_report_an_absurd_rate(
        self,
    ) -> None:
        """Reproduces the review's own numbers for the defect this class
        replaces: an alert one day before `now_ms`, over a 1095-day window
        whose LEDGER started at day 0 (established by a different cell). The
        (incoherent) per-cell clamp this file shipped first read 7.00/wk;
        the true rate is ~0.0064/wk, and the fix must read that instead."""
        conn = _conn()
        window_days = 1095
        now_ms = window_days * _MS_PER_DAY
        _fire(conn, "establishes-ledger-start", 0, strategy="fvg")
        _fire(conn, "this-cell", now_ms - _MS_PER_DAY)
        exposure = alerts_per_week(
            conn,
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=0,
            now_ms=now_ms,
        )
        assert exposure.rate == pytest.approx(7 / window_days, abs=1e-4)
        assert exposure.rate < 0.01, "must not read anywhere near the old 7.00/wk"
        assert exposure.window_start_ms == 0
        assert exposure.first_fired_ms == now_ms - _MS_PER_DAY

    def test_a_cell_that_fired_ten_minutes_ago_does_not_report_an_absurd_rate(
        self,
    ) -> None:
        """The review's second cited number: a lone alert 10 minutes before
        `now_ms` read 1008/wk under the incoherent per-cell clamp. With the
        ledger-wide clamp the true (tiny) rate over the full window stands."""
        conn = _conn()
        window_days = 1095
        now_ms = window_days * _MS_PER_DAY
        ten_minutes_ms = 10 * 60_000
        _fire(conn, "establishes-ledger-start", 0, strategy="fvg")
        _fire(conn, "this-cell", now_ms - ten_minutes_ms)
        exposure = alerts_per_week(
            conn,
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=0,
            now_ms=now_ms,
        )
        assert exposure.rate != pytest.approx(1008.0)
        assert exposure.rate == pytest.approx(7 / window_days, abs=1e-4)

    def test_no_alerts_anywhere_in_the_ledger_keeps_the_original_window(self) -> None:
        """The unchanged case: an empty ledger has no shared start to clamp
        to, so the window stays the caller's own `since_ms -> now_ms` span
        and the rate stays 0.0."""
        exposure = alerts_per_week(
            _conn(),
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=0,
            now_ms=_WEEK,
        )
        assert exposure.rate == 0.0
        assert exposure.window_start_ms == 0
        assert exposure.window_days == pytest.approx(7.0)
        assert exposure.first_fired_ms is None

    def test_this_cell_silent_while_the_ledger_is_not_still_reads_zero(self) -> None:
        """A cell with genuinely no alerts must read rate=0.0 even though the
        ledger (via another cell) narrows its window -- "leaving genuine
        per-cell silence in the denominator where it belongs" per the
        review, rather than the clamp manufacturing a nonzero rate."""
        conn = _conn()
        _fire(conn, "other-cell", 5 * _MS_PER_DAY, strategy="fvg")
        exposure = alerts_per_week(
            conn,
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=0,
            now_ms=20 * _MS_PER_DAY,
        )
        assert exposure.rate == 0.0
        assert exposure.window_start_ms == 5 * _MS_PER_DAY
        assert exposure.first_fired_ms is None

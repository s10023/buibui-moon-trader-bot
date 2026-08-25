"""Behavioural tests for `tools/ohlcv_freshness.py` — the per-series staleness guard.

The defect this guards, measured 2026-08-23 (ST61a): the routine universe sync
ran `--timeframes 1d` and nothing else, so **22 of 25 universe symbols sat
frozen on 1h (17.9 days), 4h (61.2 days) and 1w (76.2 days)** while every
existing check stayed green. All 22 were `TRADING` — this was not delisting.

Two properties make the bug invisible to a naive check, and both are pinned
below:

- **Staleness only means anything in BAR units.** A 1w series three days old is
  healthy; a 1h series three days old is 72 bars behind. One wall-clock
  threshold cannot serve both, so a check written in hours silently picks a
  timeframe to be wrong about.
- **The newest stored bar is normally IN PROGRESS.** `data_sync.sync` re-fetches
  from `latest` inclusive precisely so a mid-formation candle is overwritten
  with its final values, so a healthy series always trails by under one bar.
  A guard that treats "not yet closed" as "stale" reds every series forever.

The specificity controls are as load-bearing as the teeth here: this repo has
already shipped a guard that could not tell clean from blind
(`tests/test_lookahead.py`'s injected causal detector exists for the same
reason), so every "flags it" test below has a "does not flag it" twin.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from analytics.store import init_schema, upsert_ohlcv
from tools.ohlcv_freshness import (
    BAR_MS,
    COVERAGE_SQL,
    SCHEDULED_GAP_MS,
    SIGNAL_WATCH_GAP_MS,
    UNIVERSE_SYNC_GAP_MS,
    Series,
    series_from_rows,
    stale_series,
    tolerance_bars_for,
)

# A fixed anchor so no test reads the wall clock. 2026-08-23 04:48:00 UTC — the
# moment the ST61a measurement was taken.
REPO_ROOT = Path(__file__).resolve().parent.parent

NOW_MS = 1_787_460_480_000


def _series(symbol: str, timeframe: str, bars_behind: float) -> Series:
    """A series whose newest bar opened `bars_behind` bars before NOW_MS."""
    return Series(
        symbol=symbol,
        timeframe=timeframe,
        newest_open_time=NOW_MS - int(bars_behind * BAR_MS[timeframe]),
    )


def test_flags_a_series_frozen_beyond_tolerance() -> None:
    """Teeth: the real ST61a shape — 1w frozen 10.9 bars behind."""
    rows = [_series("BTCUSDT", "1w", 10.89)]

    stale = stale_series(rows, now_ms=NOW_MS, tolerance_bars=2.0)

    assert [s.symbol for s in stale] == ["BTCUSDT"]
    assert stale[0].timeframe == "1w"
    age_bars = stale[0].age_bars
    assert age_bars is not None
    assert round(age_bars, 1) == 10.9


def test_does_not_flag_a_series_within_tolerance() -> None:
    """Specificity: a series one bar behind is healthy, not stale."""
    rows = [_series("BTCUSDT", "1w", 0.9)]

    assert stale_series(rows, now_ms=NOW_MS, tolerance_bars=2.0) == []


def test_the_in_progress_bar_is_not_stale() -> None:
    """`sync` stores the forming bar on purpose, so age < 1 bar is the healthy case.

    Without this, every series reds continuously the moment it is guarded.
    """
    rows = [
        _series(sym, tf, 0.99) for sym, tf in (("BTCUSDT", "1d"), ("ETHUSDT", "1h"))
    ]

    assert stale_series(rows, now_ms=NOW_MS, tolerance_bars=2.0) == []


def test_tolerance_is_measured_in_bars_not_wall_clock() -> None:
    """THE reason this module exists: 3 days is fine on 1w and 72 bars behind on 1h."""
    three_days_ms = 3 * 24 * 60 * 60 * 1000
    rows = [
        Series("BTCUSDT", "1w", NOW_MS - three_days_ms),
        Series("BTCUSDT", "1h", NOW_MS - three_days_ms),
    ]

    stale = stale_series(rows, now_ms=NOW_MS, tolerance_bars=2.0)

    assert [s.timeframe for s in stale] == ["1h"]


def test_a_symbol_that_is_not_active_is_never_flagged() -> None:
    """A delisted/settling perp stops producing bars legitimately.

    TONUSDT was `SETTLING` at the ST61a measurement; without this the guard
    would red on it every day forever and train the operator to ignore the line.
    """
    rows = [_series("TONUSDT", "1h", 500.0)]

    stale = stale_series(
        rows, now_ms=NOW_MS, tolerance_bars=2.0, ignore_symbols=frozenset({"TONUSDT"})
    )

    assert stale == []


def test_an_unknown_timeframe_is_reported_rather_than_silently_skipped() -> None:
    """A timeframe with no bar length is a coverage hole, not a clean series.

    Returning `[]` for it would make "we do not know how to check this" look
    exactly like "this is fresh" — the SKIP-is-not-a-PASS failure this repo has
    already shipped once in `sanity_checks.py`.
    """
    rows = [Series("BTCUSDT", "3d", NOW_MS - 999_999_999)]

    stale = stale_series(rows, now_ms=NOW_MS, tolerance_bars=2.0)

    assert [s.timeframe for s in stale] == ["3d"]
    assert stale[0].age_bars is None


# --- The tolerance must follow the SCHEDULER, not the bar length -----------------
#
# ST90, measured 2026-08-25. The flat 2-bar tolerance is right for a series
# something refreshes every 15 minutes and wrong for one refreshed three times a
# day. `buibui-xsmom-daily.timer` is the ONLY thing that schedules a universe
# sync (00:20 / 02:20 / 06:20 UTC), so it leaves an 18-hour overnight hole — and
# 18 hours is 18 bars on 1h. Measured at 08:26 UTC that day: 22 of 25 universe
# symbols sat at 2.45 bars on 1h, over the flat tolerance, purely because the
# 06:20 sync was two hours old. The line was therefore red ~19 hours of every 24
# and green only just after a sync.
#
# A permanently-red tier-2 line is worse than no line: it teaches its reader to
# skip it, which is the same failure mode `test_a_symbol_that_is_not_active_is_
# never_flagged` above exists to prevent, arriving by a different route.
#
# The majors keep no tightness here because they never needed this check for it:
# a majors freeze means the 15-minute signal-watch timer is dead, and tier 1's
# own `signal-watch` line watches exactly that. This check's unique contribution
# is the universe path.


def test_normal_operation_between_scheduled_syncs_is_not_stale() -> None:
    """Specificity, and THE ST90 defect: the measured 2026-08-25 08:26 UTC shape.

    1h at 2.45 bars and 4h at 1.11 bars is a universe two hours past its 06:20
    sync — the healthy state for a series refreshed three times a day. Under the
    flat 2-bar tolerance the 1h row here reds, which is the bug.
    """
    rows = [_series("DOGEUSDT", "1h", 2.45), _series("DOGEUSDT", "4h", 1.11)]

    assert stale_series(rows, now_ms=NOW_MS) == []


def test_the_worst_point_of_the_refresh_cycle_is_not_stale() -> None:
    """The overnight hole itself must be healthy, or the line reds every night.

    18h after the 06:20 sync the newest 1h bar is 19 bars old (18 elapsed plus
    the forming one). That is the loosest a correctly-refreshed series ever gets.
    """
    rows = [_series("DOGEUSDT", "1h", 19.0), _series("DOGEUSDT", "4h", 5.5)]

    assert stale_series(rows, now_ms=NOW_MS) == []


def test_the_st61a_freeze_is_still_flagged_under_the_wider_default() -> None:
    """TEETH — the whole point of the module must survive the widening.

    Widening a tolerance is the one change that can silently turn a guard mute,
    so the original defect is re-asserted against the NEW default rather than
    against the flat 2.0 the other teeth tests pass explicitly.
    """
    rows = [
        _series("ADAUSDT", "1h", 17.9 * 24),  # 17.9 days = 429.6 bars
        _series("ADAUSDT", "4h", 61.2 * 6),  # 61.2 days = 367.2 bars
        _series("ADAUSDT", "1w", 76.2 / 7),  # 76.2 days = 10.9 bars
    ]

    stale = stale_series(rows, now_ms=NOW_MS)

    assert sorted(s.timeframe for s in stale) == ["1h", "1w", "4h"]


def test_tolerance_follows_the_scheduler_not_the_bar_length() -> None:
    """A 1d series three bars behind is stale; a 1h series three bars behind is not.

    Under a flat tolerance these two read identically. They are opposite states:
    three 1h bars is 90 minutes inside one refresh cycle, three 1d bars is three
    missed cycles.
    """
    rows = [_series("DOGEUSDT", "1h", 3.0), _series("DOGEUSDT", "1d", 3.0)]

    stale = stale_series(rows, now_ms=NOW_MS)

    assert [s.timeframe for s in stale] == ["1d"]


def test_the_scheduled_gap_boundary_is_where_it_is_claimed_to_be() -> None:
    """Pin both sides of the 1h boundary, so a later edit cannot drift it silently."""
    assert stale_series([_series("A", "1h", 19.9)], now_ms=NOW_MS) == []
    assert [
        s.timeframe for s in stale_series([_series("A", "1h", 20.1)], now_ms=NOW_MS)
    ] == ["1h"]


def test_an_explicit_tolerance_still_overrides_the_schedule() -> None:
    """The primitive is preserved: a caller asking for 2.0 bars gets 2.0 bars.

    Every teeth test above passes `tolerance_bars=2.0` explicitly and must keep
    measuring what it says it measures.
    """
    rows = [_series("DOGEUSDT", "1h", 2.45)]

    assert [
        s.timeframe for s in stale_series(rows, now_ms=NOW_MS, tolerance_bars=2.0)
    ] == ["1h"]


def test_the_derived_tolerances_are_the_ones_documented() -> None:
    """The numbers AGENTS.md and the daily check quote, derived rather than typed.

    `2 + gap/bar`: the flat 2 absorbs the forming bar plus slack, the second term
    is one full refresh cycle. 1h lands at 20 bars because the overnight hole is
    18 hours long — that arithmetic is the entire claim, so it is asserted here
    rather than left to a reader to redo.
    """
    assert tolerance_bars_for("1h") == 20.0
    assert tolerance_bars_for("4h") == 6.5
    assert tolerance_bars_for("1d") == 2.75
    assert tolerance_bars_for("15m") == 3.0
    assert tolerance_bars_for("3d") is None


def test_every_known_timeframe_declares_a_scheduled_gap() -> None:
    """SKIP-is-not-a-PASS: a new timeframe must not fall back to a silent default.

    `BAR_MS` and `SCHEDULED_GAP_MS` answer two halves of one question. If they
    drift apart, a series gets a bar length and no cadence — and the safe-looking
    fallback (the flat 2.0) is exactly the permanently-red state ST90 removed.
    """
    assert set(SCHEDULED_GAP_MS) == set(BAR_MS)


class TestScheduledGapMatchesTheTimer:
    """`UNIVERSE_SYNC_GAP_MS` is DERIVED from a file, so a gate has to say so.

    ST90 widened the tolerance using a number read out of
    `deploy/systemd/user/buibui-xsmom-daily.timer`. That makes the constant a
    second definition of the sync schedule — and this repo has now shipped four
    defects in one week whose whole shape was "two definitions of one thing, both
    internally consistent, no gate can see the disagreement" (ST28's spec-vs-driver
    threshold, `analytics.md`'s serial-correlation claim, ST89's two surface lists,
    and ST90 itself). Adding a fifth while fixing the fourth is the trap.

    So the timer file is parsed and the worst gap recomputed here. Change the
    schedule without changing the constant and this fails, which is the only
    reason the constant is allowed to exist as a literal.
    """

    @staticmethod
    def _on_calendar_utc_seconds() -> list[int]:
        """Seconds-past-midnight for each `OnCalendar=... HH:MM:SS UTC` line."""
        timer = REPO_ROOT / "deploy" / "systemd" / "user" / "buibui-xsmom-daily.timer"
        times: list[int] = []
        for raw in timer.read_text().splitlines():
            line = raw.strip()
            if not line.startswith("OnCalendar="):
                continue
            # `OnCalendar=*-*-* 06:20:00 UTC` -> 06:20:00
            parts = line.split()
            assert parts[-1] == "UTC", f"non-UTC schedule line, unhandled: {line}"
            hh, mm, ss = (int(x) for x in parts[-2].split(":"))
            times.append(hh * 3600 + mm * 60 + ss)
        return sorted(times)

    def test_the_timer_declares_the_schedule_the_constant_assumes(self) -> None:
        assert self._on_calendar_utc_seconds() == [
            0 * 3600 + 20 * 60,
            2 * 3600 + 20 * 60,
            6 * 3600 + 20 * 60,
        ]

    def test_the_worst_gap_in_the_timer_is_the_constant(self) -> None:
        """The wrap-around gap is the one that matters and the easy one to miss.

        06:20 -> 00:20 is 18h; every in-day gap is 2h or 4h. A max over adjacent
        pairs that forgets to wrap returns 4h and silently under-sizes the
        tolerance by a factor of four and a half.
        """
        times = self._on_calendar_utc_seconds()
        day = 24 * 3600
        gaps = [(times[(i + 1) % len(times)] - t) % day for i, t in enumerate(times)]

        assert max(gaps) * 1000 == UNIVERSE_SYNC_GAP_MS

    def test_the_signal_watch_gap_is_one_bar_of_its_tightest_timeframe(self) -> None:
        """The 15-minute timer refreshes 15m/1h/4h, so its gap is one 15m bar."""
        assert BAR_MS["15m"] == SIGNAL_WATCH_GAP_MS


class TestCoverageSql:
    """The SQL half — which relation it reads is a decision, not a detail."""

    @staticmethod
    def _conn() -> duckdb.DuckDBPyConnection:
        c = duckdb.connect(":memory:")
        init_schema(c)
        return c

    @staticmethod
    def _bar(open_time: int) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "symbol": "BTCUSDT",
                    "timeframe": "1h",
                    "open_time": open_time,
                    "open": 30000.0,
                    "high": 31000.0,
                    "low": 29500.0,
                    "close": 30500.0,
                    "volume": 100.0,
                    "taker_buy_volume": 55.0,
                }
            ]
        )

    def test_reads_the_view_so_a_shadowed_venue_cannot_mask_staleness(self) -> None:
        """A fresh OKX tail must NOT make a frozen Binance series look current.

        This is the OPPOSITE choice to `FABRICATED_CVD_SQL`, which scans
        `ohlcv_all` on purpose because it guards history across every venue.
        This check asks a different question — "is what consumers READ fresh?" —
        and consumers read the view.
        """
        conn = self._conn()
        frozen, fresh = 1_700_000_000_000, 1_787_000_000_000
        upsert_ohlcv(conn, self._bar(frozen), venue="binance")
        upsert_ohlcv(conn, self._bar(fresh), venue="okx")

        rows = conn.execute(COVERAGE_SQL).fetchall()

        assert series_from_rows(rows) == [Series("BTCUSDT", "1h", frozen)]

    def test_series_from_rows_maps_the_columns_in_order(self) -> None:
        """An adapter that transposes two columns fails silently forever."""
        assert series_from_rows([("ETHUSDT", "4h", 42)]) == [
            Series("ETHUSDT", "4h", 42)
        ]


class TestUniverseSyncCoverage:
    """The FIX itself: the routine sync must reach every timeframe it holds.

    ST61a's root cause was not a broken sync — it was a sync pointed at one
    timeframe while the universe held four. Pinned here because the next person
    to touch this line has no way to see that 1h/4h/1w depend on it.

    ST61b widened this from the Makefile to EVERY caller. Pinning the Makefile
    alone reproduced the original bug one layer up: ST61a fixed the target a
    human runs, `deploy/run-xsmom.sh` kept `--timeframes 1d`, and that script is
    the only universe sync anything SCHEDULES — so this test passed while the
    1h/4h/1w gaps re-opened at the rate they had closed. Coverage is the UNION
    of the callers, so the test has to be too.
    """

    # Every executable surface that runs the routine universe sync. A third
    # caller must be added here deliberately — `test_no_universe_sync_caller_is
    # _unlisted` fails until it is, which is what stops the next one landing
    # 1d-only and unwatched.
    CALLERS = ("Makefile", "deploy/run-xsmom.sh")

    UNIVERSE_TIMEFRAMES = ("1h", "4h", "1d", "1w")

    @staticmethod
    def _sync_invocation(path: str) -> str:
        """The universe-sync command, line continuations folded into one line."""
        text = (REPO_ROOT / path).read_text().replace("\\\n", " ")
        line = next(ln for ln in text.splitlines() if "analytics sync --universe" in ln)
        return line

    def test_universe_sync_covers_every_timeframe_the_universe_holds(self) -> None:
        makefile = (REPO_ROOT / "Makefile").read_text()
        recipe = makefile.split("buibui-universe-sync:")[1].split("\n.PHONY")[0]

        assert "--universe" in recipe
        for timeframe in self.UNIVERSE_TIMEFRAMES:
            assert timeframe in recipe, f"universe sync no longer covers {timeframe}"

    def test_every_universe_sync_caller_covers_every_timeframe(self) -> None:
        """The SCHEDULED caller is the one that matters, and it is not the Makefile.

        `deploy/run-xsmom.sh` is what `buibui-xsmom-daily.timer` runs, on the
        laptop and on the VPS. Nothing schedules the Make target at all.
        """
        for caller in self.CALLERS:
            invocation = self._sync_invocation(caller)
            for timeframe in self.UNIVERSE_TIMEFRAMES:
                assert timeframe in invocation, (
                    f"{caller} syncs the universe without {timeframe} — "
                    f"the 1h/4h/1w gaps re-open through this caller"
                )

    def test_no_universe_sync_caller_is_unlisted(self) -> None:
        """A new caller must join CALLERS, not sync the universe unwatched.

        Scoped to the executable surfaces on purpose: prose in `docs/` and
        `.claude/` names the command constantly and none of it runs.
        """
        surfaces = [REPO_ROOT / "Makefile", REPO_ROOT / "docker-compose.yml"]
        surfaces += sorted(REPO_ROOT.glob("deploy/**/*.sh"))
        surfaces += sorted(REPO_ROOT.glob("deploy/**/*.service"))
        surfaces += sorted(REPO_ROOT.glob(".github/workflows/*.yaml"))
        surfaces += sorted(REPO_ROOT.glob(".github/workflows/*.yml"))

        found = {
            str(path.relative_to(REPO_ROOT))
            for path in surfaces
            if path.is_file() and "analytics sync --universe" in path.read_text()
        }

        assert found == set(self.CALLERS), (
            f"universe-sync callers changed: {sorted(found)} != "
            f"{sorted(self.CALLERS)} — add it to CALLERS so its timeframes "
            f"are pinned too"
        )

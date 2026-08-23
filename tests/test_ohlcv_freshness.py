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
    Series,
    series_from_rows,
    stale_series,
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

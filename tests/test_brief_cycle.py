"""Tests for analytics/brief/cycle.py (ST54 — the bundle-level bear score).

The score is a NUMBER, never a gate: n_eff is ~3 distinct bear markets, so
nothing here may size, gate or suppress anything. These tests pin the shape
and the no-look-ahead property, not a trading claim.

⚠ The four WEEKLY MAs are resampled from 1d on purpose — `analytics.db`'s
`1w` bars have been stale since 2026-06-08 on all 25 symbols (ST53).
"""

from __future__ import annotations

import pandas as pd
import pytest

from analytics.brief.cycle import (
    _CYCLE_MAS,
    _closed_weekly_closes,
    _ma_frame,
    _pick_trigger,
    build_cycle_state,
)
from analytics.brief.render import _cycle_line
from analytics.brief.types import CycleState

DAY_MS = 86_400_000
# 2024-01-01 00:00 UTC — a Monday (matches tests/_brief_fixtures.py).
START_MS = 1_704_067_200_000


def _daily(
    n_days: int, close: float = 100.0, final_close: float | None = None
) -> pd.DataFrame:
    """A flat daily series, optionally with a different close on the last bar.

    Flat is deliberate: it puts every one of the six MAs at the same level, so
    a single final close decides all six comparisons at once and the fixture
    cannot drift into a partial score by accident.
    """
    rows: list[dict[str, object]] = []
    for i in range(n_days):
        c = close if (final_close is None or i < n_days - 1) else final_close
        rows.append(
            {
                "open_time": START_MS + i * DAY_MS,
                "open": c,
                "high": c * 1.001,
                "low": c * 0.999,
                "close": c,
                "volume": 1000.0,
            }
        )
    return pd.DataFrame(rows)


def _as_of(df: pd.DataFrame) -> int:
    """One day past the last bar — the bar is closed, the next is forming."""
    return int(df["open_time"].iloc[-1]) + DAY_MS


class TestPickTrigger:
    """Trigger = the NEAREST MA below price, per ST53's worked example."""

    def test_picks_the_nearest_ma_below_price_not_the_lowest(self) -> None:
        # ST53's shape: BTC 69,600 sits above three MAs; the one whose break
        # moves the score by exactly one is the HIGHEST of them, 68,767.
        mas = {
            "21W EMA": 68_767.0,
            "20W SMA": 61_000.0,
            "200D SMA": 55_000.0,
            "50W SMA": 71_000.0,
            "50W EMA": 72_000.0,
            "200D EMA": 73_000.0,
        }
        picked = _pick_trigger(69_600.0, mas)
        assert picked is not None
        name, price, dist_pct = picked
        assert name == "21W EMA"
        assert price == 68_767.0
        # Signed, negative because the level sits below price.
        assert dist_pct == pytest.approx(-1.196, abs=0.01)

    def test_none_when_price_is_below_every_ma(self) -> None:
        """Score 6 — no MA sits below price, so there is nothing to cross."""
        mas = dict.fromkeys(_CYCLE_MAS, 100.0)
        assert _pick_trigger(50.0, mas) is None

    def test_none_when_price_is_above_every_ma(self) -> None:
        """Score 0 — ST54 pins this end as None too, matching score 6."""
        mas = dict.fromkeys(_CYCLE_MAS, 100.0)
        assert _pick_trigger(200.0, mas) is None


class TestScoreAtEachEnd:
    def test_price_above_every_ma_scores_zero(self) -> None:
        df = _daily(500, close=100.0, final_close=200.0)
        state, notes = build_cycle_state(df, _as_of(df))
        assert state is not None, notes
        assert state.score == 0
        assert state.total == len(_CYCLE_MAS) == 6
        assert state.below == ()
        assert state.trigger_name is None

    def test_price_below_every_ma_scores_six(self) -> None:
        df = _daily(500, close=100.0, final_close=50.0)
        state, notes = build_cycle_state(df, _as_of(df))
        assert state is not None, notes
        assert state.score == 6
        assert set(state.below) == set(_CYCLE_MAS)
        assert state.trigger_name is None


class TestNoLookAhead:
    """The in-progress week must never reach a weekly MA.

    Three tests, and all three are load-bearing: the invariant (teeth), a
    positive control proving the stimulus propagates at all, and a mutation
    proving the guard fails when the closed-week shift is dropped. Without the
    second, "unchanged" cannot be told from "inert"; without the third, the
    guard cannot be told from a guard that never looks.
    """

    @staticmethod
    def _split() -> tuple[pd.DataFrame, pd.DataFrame]:
        """(through last Sunday, same + 3 days of a violent in-progress week)."""
        # 497 days from a Monday start lands the last bar on a Sunday, so the
        # frame ends exactly on a week boundary.
        base = _daily(497, close=100.0)
        assert (
            pd.Timestamp(int(base["open_time"].iloc[-1]), unit="ms", tz="UTC").dayofweek
            == 6
        ), "fixture must end on a Sunday"
        extra = pd.DataFrame(
            [
                {
                    "open_time": int(base["open_time"].iloc[-1]) + k * DAY_MS,
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 1000.0,
                }
                for k in range(1, 4)
            ]
        )
        return base, pd.concat([base, extra], ignore_index=True)

    def test_in_progress_week_never_enters_a_weekly_ma(self) -> None:
        base, with_partial = self._split()
        closed_before = _closed_weekly_closes(base, _as_of(base))
        closed_after = _closed_weekly_closes(with_partial, _as_of(with_partial))
        # The three crash days belong to an unfinished week and must be absent.
        assert list(closed_after.values) == list(closed_before.values)
        assert 10.0 not in set(closed_after.values)

    def test_positive_control_the_crash_does_reach_the_daily_side(self) -> None:
        """Pairs the invariant above — proves the stimulus was not inert."""
        base, with_partial = self._split()
        before, _ = build_cycle_state(base, _as_of(base))
        after, _ = build_cycle_state(with_partial, _as_of(with_partial))
        assert before is not None and after is not None
        assert before.close == 100.0
        assert after.close == 10.0, "the crash never propagated; the test is vacuous"
        assert after.score > before.score

    def test_guard_catches_a_resampler_that_keeps_the_open_week(self) -> None:
        """Mutation / specificity control — the injected leak MUST be caught.

        A guard with only the teeth test above cannot distinguish "clean" from
        "blind". This is the same two-control shape as tests/test_lookahead.py.
        """
        _, with_partial = self._split()
        as_of = _as_of(with_partial)

        leaky = _leaky_weekly_closes(with_partial, as_of)
        clean = _closed_weekly_closes(with_partial, as_of)

        assert 10.0 in set(leaky.values), "the injected leak did not leak"
        assert 10.0 not in set(clean.values)
        assert len(leaky) == len(clean) + 1


class TestWeeklyAverageTakesEffectOnlyAfterItsWeekCloses:
    """The second half of the no-look-ahead property, and it needs its own test.

    Dropping the in-progress week (above) fixes TODAY's reading. It does not fix
    the HISTORICAL series behind `days_at_score`: without the effective-date
    shift, a Wednesday reads a weekly average containing Wednesday's own close,
    so a run length is counted off levels nobody could have seen. Measured: with
    only the tests above, removing that shift left all 8 of them passing.
    """

    @staticmethod
    def _spiked() -> pd.DataFrame:
        """Flat 100 for 71 weeks, except week 60 which trades at 200."""
        df = _daily(500, close=100.0)
        week60 = (df.index >= 60 * 7) & (df.index < 61 * 7)
        df.loc[week60, ["open", "high", "low", "close"]] = 200.0
        return df

    def test_spike_week_is_absent_until_the_following_monday(self) -> None:
        df = self._spiked()
        frame, _ = _ma_frame(df, _as_of(df))
        index = pd.to_datetime(df["open_time"], unit="ms", utc=True)

        sunday_of_spike_week = index.iloc[60 * 7 + 6]
        monday_after = index.iloc[61 * 7]
        assert sunday_of_spike_week.dayofweek == 6
        assert monday_after.dayofweek == 0

        # Weeks 40..59 are all 100, so the average has not moved yet.
        assert frame.loc[sunday_of_spike_week, "20W SMA"] == pytest.approx(100.0)
        # Positive control: weeks 41..60 = (19 * 100 + 200) / 20. If this does
        # not move, the fixture never perturbed anything and the line above is
        # vacuous rather than reassuring.
        assert frame.loc[monday_after, "20W SMA"] == pytest.approx(105.0)


def _leaky_weekly_closes(daily: pd.DataFrame, as_of_ms: int) -> pd.Series:
    """Deliberately broken control: the closed-week shift removed.

    Injected here rather than imported so nothing in production can call it.
    """
    ts = pd.to_datetime(daily["open_time"], unit="ms", utc=True)
    return (
        daily.assign(_ts=ts)
        .set_index("_ts")["close"]
        .resample("W-MON", label="left", closed="left")
        .last()
        .dropna()
    )


class TestRender:
    """Rendering is a separate failure surface — the gates are blind to it.

    Three prior instances of a green suite over a broken render are on file, so
    the line gets its own assertions rather than riding on the builder's.
    """

    @staticmethod
    def _state(**over: object) -> CycleState:
        base: dict[str, object] = {
            "score": 3,
            "total": 6,
            "below": ("50W SMA", "50W EMA", "200D EMA"),
            "close": 69_600.0,
            "trigger_name": "21W EMA",
            "trigger_price": 68_767.0,
            "trigger_dist_pct": -1.1968,
            "days_at_score": 9,
        }
        base.update(over)
        return CycleState(**base)  # type: ignore[arg-type]

    def test_absent_cycle_renders_nothing(self) -> None:
        assert _cycle_line(None) is None

    def test_line_carries_score_trigger_and_the_not_a_gate_marker(self) -> None:
        line = _cycle_line(self._state())
        assert line is not None
        assert "bear score 3/6" in line
        assert "50W SMA, 50W EMA, 200D EMA" in line
        assert "21W EMA" in line and "-1.20%" in line
        assert "9d at this score" in line
        # ST54's framing requirement: n_eff is ~3, so the number must never read
        # as a signal. If this assertion is ever deleted, read ST53 first.
        assert "display only, never a gate" in line

    def test_score_six_renders_without_a_trigger(self) -> None:
        line = _cycle_line(
            self._state(
                score=6,
                below=tuple(_CYCLE_MAS),
                trigger_name=None,
                trigger_price=None,
                trigger_dist_pct=None,
            )
        )
        assert line is not None
        assert "bear score 6/6" in line
        assert "next" not in line

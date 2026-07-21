"""Tests for analytics/brief/weekly.py (conn-free adapter)."""

from analytics.brief.weekly import build_weekly_state
from analytics.stats.weekly_cone import (
    CurrentWeekPath,
    WeeklyConeBundle,
    WeeklyConeCombo,
)


def _combo(direction: str, n: int) -> WeeklyConeCombo:
    return WeeklyConeCombo(
        direction=direction,
        n=n,
        bands=[[-1.0, -0.5, 0.0, 0.5, 1.0]] * 168,
        low_in_by=[i / 168 for i in range(1, 169)],
        high_in_by=[i / 168 for i in range(1, 169)],
        mae_p=[-1.0, -0.4, -0.1],
        mfe_p=[0.1, 0.4, 1.0],
        high_piv=[0.5, 0.9],
        low_piv=[0.5, 0.9],
    )


def _bundle() -> WeeklyConeBundle:
    return WeeklyConeBundle(
        combos={
            "all": _combo("all", 344),
            "bull": _combo("bull", 172),
            "bear": _combo("bear", 172),
        },
        total_weeks=344,
    )


def test_none_when_no_current_path() -> None:
    """No forming-week path -> no state, one note."""
    state, notes = build_weekly_state(cone=_bundle(), current=None)
    assert state is None
    assert len(notes) == 1


def test_none_when_population_empty() -> None:
    """Empty cone -> no state, one note."""
    empty = WeeklyConeBundle(combos={}, total_weeks=0)
    current = CurrentWeekPath(
        points=[0.2], elapsed_h=1, awr14_current=0.02, week_open=100.0
    )
    state, notes = build_weekly_state(cone=empty, current=current)
    assert state is None
    assert len(notes) == 1


def test_direction_from_current_path() -> None:
    """A positive last point reads as a bull path so far."""
    current = CurrentWeekPath(
        points=[0.0] * 62 + [0.4], elapsed_h=62, awr14_current=0.02, week_open=100.0
    )
    state, notes = build_weekly_state(cone=_bundle(), current=current)
    assert state is not None
    assert state.path_direction == "bull"
    assert state.elapsed_h == 62
    assert state.total_bars == 168
    assert state.n_conditional == 172
    assert state.n_unconditional == 344
    assert notes == []


def test_percentile_within_bands() -> None:
    """Percentile rank is reported for both the conditional and all pools."""
    current = CurrentWeekPath(
        points=[0.0] * 62 + [0.6], elapsed_h=62, awr14_current=0.02, week_open=100.0
    )
    state, _ = build_weekly_state(cone=_bundle(), current=current)
    assert state is not None
    # Saturates at the ladder ends — only p10…p90 is resolvable from 5 bands.
    assert 10 <= state.pct_conditional <= 90
    assert 10 <= state.pct_unconditional <= 90


def test_percentile_saturates_at_tails() -> None:
    """A value far above p90 reports 90, not 100 — no invented precision."""
    current = CurrentWeekPath(
        points=[0.0] * 62 + [99.0], elapsed_h=62, awr14_current=0.02, week_open=100.0
    )
    state, _ = build_weekly_state(cone=_bundle(), current=current)
    assert state is not None
    assert state.pct_unconditional == 90.0

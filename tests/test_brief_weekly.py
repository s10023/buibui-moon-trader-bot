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


def test_direction_flat_on_exact_zero() -> None:
    """norm_now == 0.0 exactly reads as 'flat' — no bull/bear cohort exists

    for it in cone.combos (only all/bull/bear), so the conditional pool
    falls back to the unconditional one (n_conditional == n_unconditional).
    The renderer, not this adapter, is responsible for never presenting that
    fallback as a real "closed flat" cohort (see render.py::_weekly_lines).
    """
    current = CurrentWeekPath(
        points=[0.0] * 62, elapsed_h=62, awr14_current=0.02, week_open=100.0
    )
    state, notes = build_weekly_state(cone=_bundle(), current=current)
    assert state is not None
    assert notes == []
    assert state.path_direction == "flat"
    assert state.n_conditional == state.n_unconditional == 344
    assert state.pct_conditional == state.pct_unconditional
    assert state.conditional_is_fallback is True


def test_conditional_is_fallback_false_when_cohort_populated() -> None:
    """A real bull/bear cohort with weeks in it is NOT a fallback."""
    current = CurrentWeekPath(
        points=[0.0] * 62 + [0.4], elapsed_h=62, awr14_current=0.02, week_open=100.0
    )
    state, _ = build_weekly_state(cone=_bundle(), current=current)
    assert state is not None
    assert state.path_direction == "bull"
    assert state.conditional_is_fallback is False


def test_conditional_is_fallback_true_when_cohort_empty() -> None:
    """C1: the bear combo key EXISTS in cone.combos but has zero weeks
    (n=0, bands=[]) — a non-"flat" direction can also hit the fallback, so
    the flag must be set from bands emptiness, not inferred from the
    direction string being "flat"."""
    bundle = WeeklyConeBundle(
        combos={
            "all": _combo("all", 344),
            "bull": _combo("bull", 344),
            "bear": WeeklyConeCombo(
                direction="bear",
                n=0,
                bands=[],
                low_in_by=[],
                high_in_by=[],
                mae_p=[],
                mfe_p=[],
                high_piv=[],
                low_piv=[],
            ),
        },
        total_weeks=344,
    )
    current = CurrentWeekPath(
        points=[0.0] * 62 + [-0.4], elapsed_h=62, awr14_current=0.02, week_open=100.0
    )
    state, notes = build_weekly_state(cone=bundle, current=current)
    assert state is not None
    assert notes == []
    assert state.path_direction == "bear"
    assert state.conditional_is_fallback is True
    # Unlike the "flat" case (where cond_combo IS all_combo by identity, so
    # n_conditional mirrors n_unconditional), here cond_combo is the
    # genuinely-empty "bear" combo object — n_conditional is its own n=0.
    # The renderer never reads n_conditional/pct_conditional when
    # conditional_is_fallback is True (see render.py::_weekly_lines), so
    # this doesn't leak into the rendered text either way.
    assert state.n_conditional == 0
    assert state.n_unconditional == 344
    assert state.pct_conditional == state.pct_unconditional

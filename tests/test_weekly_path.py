"""H10 partial-path predictiveness — pure-library tests (no DB)."""

from datetime import date, timedelta

import numpy as np
import pytest

from analytics import weekly_path as wp

WEEK_BARS = 168


def _week(i: int) -> date:
    """Monday i weeks after 2020-01-06 (a Monday)."""
    return date(2020, 1, 6) + timedelta(weeks=i)


def _linear_path(start: float, end: float) -> tuple[float, ...]:
    """A 168-point path rising linearly from `start` to `end`."""
    return tuple(np.linspace(start, end, WEEK_BARS))


def test_signal_sign_reads_the_bar_before_hour() -> None:
    """Hour h means index h-1 — the close of the h-th completed bar."""
    path = [0.0] * WEEK_BARS
    path[23] = -5.0  # index 23 == hour 24
    path[24] = +5.0
    assert wp.signal_sign(path, 24) == -1.0
    assert wp.signal_sign(path, 25) == +1.0


def test_signal_sign_flat_week_is_zero() -> None:
    assert wp.signal_sign([0.0] * WEEK_BARS, 24) == 0.0


def test_remaining_return_spans_hour_to_close() -> None:
    path = [0.0] * WEEK_BARS
    path[23] = 2.0
    path[167] = 5.0
    assert wp.remaining_return(path, 24) == pytest.approx(3.0)


def test_remaining_return_at_last_hour_is_zero() -> None:
    path = list(_linear_path(0.0, 4.0))
    assert wp.remaining_return(path, 168) == pytest.approx(0.0)


def _population(
    paths_by_week: dict[int, list[tuple[str, tuple[float, ...]]]],
) -> list[wp.SymbolWeek]:
    out: list[wp.SymbolWeek] = []
    for i, entries in sorted(paths_by_week.items()):
        for symbol, path in entries:
            out.append(wp.SymbolWeek(symbol=symbol, week=_week(i), norm_path=path))
    return out


def test_pure_drift_does_not_read_as_skill() -> None:
    """THE DRIFT GUARD (spec §2.1).

    Every week rises identically. sign(path) is always +1 and the remaining
    return is always positive, so a NON-demeaned statistic would read strongly
    positive. The causal demean must collapse it toward zero.
    """
    pop = _population({i: [("BTCUSDT", _linear_path(0.0, 1.0))] for i in range(200)})
    obs = wp.build_observations(pop, 24, wp.PathConfig())
    values = [o.value for o in obs]
    assert values, "population must produce observations"
    assert abs(float(np.mean(values))) < 0.01


def test_driftless_random_walk_reads_near_zero_at_every_hour() -> None:
    """THE TAUTOLOGY GUARD (spec §2).

    A random walk carries no path information. It must read ~0 at EVERY hour,
    including 120 — where a terminal-direction-vs-base-rate test would have
    reported strong predictiveness from arithmetic alone.
    """
    rng = np.random.default_rng(0)
    pop: list[wp.SymbolWeek] = []
    for i in range(600):
        # Step sigma is deliberately small: the remaining return's sampling
        # error must sit well under the 0.05 bar, or this test flakes on a
        # correct implementation rather than catching a broken one.
        steps = rng.normal(0.0, 0.02, WEEK_BARS)
        pop.append(wp.SymbolWeek("BTCUSDT", _week(i), tuple(np.cumsum(steps))))
    for hour in wp.GATED_HOURS:
        obs = wp.build_observations(pop, hour, wp.PathConfig())
        mean_v = float(np.mean([o.value for o in obs]))
        assert abs(mean_v) < 0.05, f"hour {hour} drifted to {mean_v}"


def test_constructed_continuation_reads_positive() -> None:
    """A genuine continuation effect must survive the demean."""
    rng = np.random.default_rng(1)
    pop: list[wp.SymbolWeek] = []
    for i in range(300):
        direction = 1.0 if rng.random() < 0.5 else -1.0
        # Same sign in both halves => the partial path predicts the remainder.
        first = np.linspace(0.0, direction * 1.0, 24)
        rest = np.linspace(direction * 1.0, direction * 3.0, WEEK_BARS - 24)
        pop.append(
            wp.SymbolWeek("BTCUSDT", _week(i), tuple(np.concatenate([first, rest])))
        )
    obs = wp.build_observations(pop, 24, wp.PathConfig())
    assert float(np.mean([o.value for o in obs])) > 0.5


def test_expanding_mean_is_causal() -> None:
    """CAUSALITY GUARD: perturbing week k must not move any earlier observation."""
    base = [
        wp.SymbolWeek("BTCUSDT", _week(i), _linear_path(0.0, 1.0)) for i in range(120)
    ]
    bumped = list(base)
    bumped[100] = wp.SymbolWeek("BTCUSDT", _week(100), _linear_path(0.0, 50.0))

    cfg = wp.PathConfig()
    before = {o.week: o.value for o in wp.build_observations(base, 24, cfg)}
    after = {o.week: o.value for o in wp.build_observations(bumped, 24, cfg)}

    for i in range(100):
        w = _week(i)
        if w in before:
            assert before[w] == pytest.approx(after[w]), f"week {i} moved"


def test_baseline_excludes_the_week_it_prices() -> None:
    """SELF-INCLUSION GUARD — the case the perturbation test structurally cannot see.

    52 flat prior weeks (remaining 0.0), then one extreme week (remaining 10.0).
    The extreme week is the FIRST week to clear the min_prior_obs warm-up, so its
    baseline must be the mean of the 52 priors (0.0), making its value exactly
    10.0. If the baseline were advanced BEFORE the emit block, the week would
    price against a mean that includes itself: 10 - 10/53 = 9.811..., and this
    assertion fails.
    """
    cfg = wp.PathConfig()

    def _path(remaining: float) -> tuple[float, ...]:
        path = [0.0] * WEEK_BARS
        for j in range(23, WEEK_BARS):
            path[j] = 1.0
        path[WEEK_BARS - 1] = 1.0 + remaining
        return tuple(path)

    pop = [wp.SymbolWeek("A", _week(i), _path(0.0)) for i in range(52)]
    pop.append(wp.SymbolWeek("A", _week(52), _path(10.0)))

    obs = wp.build_observations(pop, 24, cfg)
    assert len(obs) == 1
    assert obs[0].week == _week(52)
    assert obs[0].value == pytest.approx(10.0)


def test_cross_section_is_averaged_not_counted() -> None:
    """25 identical symbols must give the same observation as 1 (spec §5.1)."""
    cfg = wp.PathConfig()
    one = [
        wp.SymbolWeek("A", _week(i), _linear_path(0.0, float(i % 3) - 1.0))
        for i in range(200)
    ]
    many: list[wp.SymbolWeek] = []
    for i in range(200):
        for s in range(25):
            many.append(
                wp.SymbolWeek(f"S{s}", _week(i), _linear_path(0.0, float(i % 3) - 1.0))
            )

    # Compared over the weeks COMMON to both runs, not position-by-position:
    # min_prior_obs counts symbol-weeks, so the 25-symbol population finishes
    # its baseline warm-up ~25x sooner and legitimately emits more weeks.
    obs_one = {o.week: o for o in wp.build_observations(one, 24, cfg)}
    obs_many = {o.week: o for o in wp.build_observations(many, 24, cfg)}
    common = set(obs_one) & set(obs_many)
    assert common, "the two runs must overlap on some weeks"
    for w in common:
        # If the collapse summed instead of averaging, these would differ 25x.
        assert obs_one[w].value == pytest.approx(obs_many[w].value)
        assert obs_one[w].n_symbols == 1
        assert obs_many[w].n_symbols == 25


def test_flat_weeks_are_dropped() -> None:
    cfg = wp.PathConfig()
    pop = [wp.SymbolWeek("A", _week(i), tuple([0.0] * WEEK_BARS)) for i in range(120)]
    assert wp.build_observations(pop, 24, cfg) == []


def test_mean_abs_signal_measures_the_signal_not_the_remainder() -> None:
    """It feeds the magnitude terciles, so it must be |signal|, not |remainder|."""
    cfg = wp.PathConfig()
    pop: list[wp.SymbolWeek] = []
    for i in range(120):
        path = [0.0] * WEEK_BARS
        path[23] = 2.0  # signal magnitude 2.0 at hour 24
        path[167] = 9.0  # remainder 7.0 — must NOT be what is recorded
        pop.append(wp.SymbolWeek("A", _week(i), tuple(path)))
    obs = wp.build_observations(pop, 24, cfg)
    assert obs
    assert obs[0].mean_abs_signal == pytest.approx(2.0)


def test_malformed_paths_are_excluded() -> None:
    """Population rules (spec §4): a path that is not 168 points cannot enter.

    The cone's own rules already exclude short weeks, zero-open weeks, and weeks
    without 14 priors upstream; this is the library's own defensive guard for a
    caller that hands it something malformed.
    """
    cfg = wp.PathConfig()
    good = [wp.SymbolWeek("A", _week(i), _linear_path(0.0, 1.0)) for i in range(120)]
    bad = [wp.SymbolWeek("B", _week(i), tuple([1.0] * 167)) for i in range(120)]
    obs = wp.build_observations(good + bad, 24, cfg)
    assert obs
    assert all(o.n_symbols == 1 for o in obs), "the 167-bar symbol must not contribute"

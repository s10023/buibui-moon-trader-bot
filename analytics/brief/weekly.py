"""Forming-week state for the brief panel (M5 adapter).

Conn-free: consumes a precomputed WeeklyConeBundle and CurrentWeekPath (the
bundle owns the DB calls). Returns (state, notes) with UNPREFIXED notes; a
failure degrades to a note and a None state, never an exception.

The cone is conditional on OUTCOME. `path_direction` describes what the week
has done so far — it is NOT a claim about how the week will close.
"""

from __future__ import annotations

from analytics.brief.types import WeeklyState
from analytics.stats.weekly_cone import CurrentWeekPath, WeeklyConeBundle


def _percentile_of(bands_at_hour: list[float], value: float) -> float:
    """Approximate percentile of `value` against the p10/25/50/75/90 ladder.

    Linear interpolation between adjacent band edges, SATURATING at the ends:
    the cone bundle carries only five percentiles, so nothing outside
    [p10, p90] is resolvable and the return value is clamped to [10, 90].
    A returned 10.0 means "at or below p10", 90.0 means "at or above p90" —
    the renderer must not present those as exact ranks.
    """
    ladder = [10.0, 25.0, 50.0, 75.0, 90.0]
    if value <= bands_at_hour[0]:
        return ladder[0]
    if value >= bands_at_hour[-1]:
        return ladder[-1]
    for i in range(len(ladder) - 1):
        lo, hi = bands_at_hour[i], bands_at_hour[i + 1]
        if lo <= value <= hi:
            if hi == lo:
                return ladder[i]
            frac = (value - lo) / (hi - lo)
            return ladder[i] + frac * (ladder[i + 1] - ladder[i])
    return ladder[-1]


def build_weekly_state(
    *,
    cone: WeeklyConeBundle,
    current: CurrentWeekPath | None,
) -> tuple[WeeklyState | None, list[str]]:
    """(state, notes). None state whenever the week or the cone is unusable."""
    if current is None or not current.points:
        return None, ["weekly cone: no forming-week path (short history?)"]
    if cone.total_weeks == 0 or "all" not in cone.combos:
        return None, ["weekly cone: no complete weeks in population"]

    # `points[-1]` is the last CLOSED hour — hour `elapsed_h` — because
    # compute_current_week_path admits only bars closed at its anchor, so
    # len(points) == elapsed_h. It is ranked below (via `idx`) against that
    # same hour's band, so the value and its band name the same moment and
    # the head line's "h{elapsed_h}" label is exact.
    #
    # Until 2026-08-05 `points` carried the still-forming bar instead. That
    # put this one step AHEAD of its band, and — because the DB overwrites
    # the open bar's OHLC on every sync — made it drift under a fixed
    # `--as-of` anchor (measured: norm_now 0.1696 -> 0.1485, high_hour
    # 61 -> 51, at one anchor over 17 min).
    norm_now = current.points[-1]
    if norm_now > 0:
        direction = "bull"
    elif norm_now < 0:
        direction = "bear"
    else:
        direction = "flat"

    all_combo = cone.combos["all"]
    # "flat" has no cohort in cone.combos (only all/bull/bear) — this falls
    # back to the unconditional pool, so pct_cond/n_conditional below mirror
    # pct_uncond/n_unconditional exactly. A same-direction combo CAN also
    # exist but be empty (n=0, bands=[]) when the population has zero weeks
    # of that outcome. `conditional_is_fallback` below captures BOTH cases so
    # the renderer (render.py::_weekly_lines) can omit the "of weeks that
    # closed X" clause without inferring it from `path_direction == "flat"`
    # — presenting the unconditional population as a conditional cohort
    # would be a false label (see 2026-07-20 task-4 review, B2; C1 2026-07-21).
    cond_combo = cone.combos.get(direction, all_combo)
    conditional_is_fallback = cond_combo is all_combo or not cond_combo.bands
    if not all_combo.bands:
        return None, ["weekly cone: population has no bands"]

    # bands[elapsed_h - 1] is hour `elapsed_h`'s band — the same hour that
    # points[-1] now reports (see the norm_now comment above).
    idx = min(max(current.elapsed_h - 1, 0), len(all_combo.bands) - 1)
    pct_uncond = _percentile_of(all_combo.bands[idx], norm_now)
    pct_cond = (
        _percentile_of(cond_combo.bands[idx], norm_now)
        if cond_combo.bands
        else pct_uncond
    )

    # current.points is guaranteed non-empty by the guard at the top of this
    # function, so these can never fall back to None.
    # I4: these are CLOSE-based (current.points holds hourly closes only —
    # CurrentWeekPath carries no intrabar high/low). low_in_by_now below
    # compares against a historical distribution built from intrabar
    # lows/highs (WeeklyConeCombo.low_in_by) — the renderer must label
    # low_hour/high_hour as "close" to avoid implying the same definition.
    low_hour = current.points.index(min(current.points)) + 1
    high_hour = current.points.index(max(current.points)) + 1
    low_in_by_now = (
        cond_combo.low_in_by[idx] if cond_combo.low_in_by else all_combo.low_in_by[idx]
    )

    return (
        WeeklyState(
            path_direction=direction,
            elapsed_h=current.elapsed_h,
            total_bars=len(all_combo.bands),
            norm_now=norm_now,
            pct_conditional=pct_cond,
            pct_unconditional=pct_uncond,
            n_conditional=cond_combo.n,
            n_unconditional=all_combo.n,
            low_hour=low_hour,
            high_hour=high_hour,
            low_in_by_now=low_in_by_now,
            conditional_is_fallback=conditional_is_fallback,
        ),
        [],
    )

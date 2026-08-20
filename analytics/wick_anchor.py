"""ST56 — does ``wick_fill``'s own wick anchor beat the geometry that discards it?

Pre-registration and power pricing:
``docs/audits/2026-08-20-st56-wick-fill-anchor-power.md``. One construction, one
trial. Nothing here searches a parameter.

The detector already emits the wick's extreme as ``sl_price`` (``zone_bot`` for a
long, ``zone_top`` for a short), so the anchor is computed in production and then
thrown away by one of two mechanisms — the side-of-entry fallback, and the
``min_sl_pct`` floor that clamps a wick lying too close to entry. This module
measures the arm that keeps it, paired against the arm that does not, on the
identical fire set.

Production geometry is **called, never restated**: ``classify_production_geometry``
delegates to ``analytics.signal.scanner._resolve_outcome_sl_tp`` and labels which
of the three candidate prices came back. A second copy of the preference order is
the ``passes_gate`` drift trap, where two independent spellings agreed on every
input until they did not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from analytics.signal.scanner import _resolve_outcome_sl_tp

DIRECTIONS = ("long", "short")

#: Geometry labels. ``floor`` is the mechanism ST56 never named.
GEOMETRY_STRUCTURAL = "structural"
GEOMETRY_FALLBACK = "fallback"
GEOMETRY_FLOOR = "floor"


@dataclass(frozen=True)
class ExitResult:
    """Outcome of one simulated trade.

    ``r`` is ``None`` when neither level was touched before the data ran out —
    unresolved, which is dropped-and-counted rather than marked to market, so a
    truncated tail cannot masquerade as a small loss.
    """

    r: float | None
    bars_held: int
    ambiguous: bool
    r_optimistic: float | None = None

    def __post_init__(self) -> None:
        if self.r_optimistic is None and self.r is not None:
            object.__setattr__(self, "r_optimistic", self.r)


def _check_direction(direction: str) -> None:
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}, got {direction!r}")


def wick_anchor_stop(direction: str, entry: float, wick: float) -> float | None:
    """The wick's own extreme as a stop, or ``None`` when it is unusable.

    No floor and no fallback: that omission *is* the construction. A wick on the
    wrong side of entry — or exactly at it — is dropped and counted, never
    silently defaulted, because defaulting is what the production path does and
    would collapse the two arms back together.
    """
    _check_direction(direction)
    if not math.isfinite(entry) or not math.isfinite(wick):
        return None
    if direction == "long":
        return wick if 0.0 < wick < entry else None
    return wick if wick > entry else None


def classify_production_geometry(
    direction: str,
    entry: float,
    struct_sl: float,
    sl_pct: float,
    min_sl_pct: float,
) -> str:
    """Label which of production's three stop paths actually bound.

    Delegates the arithmetic to the production resolver and infers the label from
    which candidate price it returned, so this function cannot drift away from
    the behaviour it describes.
    """
    _check_direction(direction)
    sl_price, _tp = _resolve_outcome_sl_tp(
        direction=direction,
        entry=entry,
        struct_sl=struct_sl,
        struct_tp=0.0,
        eff_sl_pct=sl_pct,
        min_sl_pct=min_sl_pct,
        tp_r=1.0,
    )
    if math.isclose(sl_price, struct_sl, rel_tol=1e-12, abs_tol=1e-12):
        return GEOMETRY_STRUCTURAL
    sl_dist = abs(entry - sl_price)
    if math.isclose(sl_dist, entry * min_sl_pct, rel_tol=1e-12, abs_tol=1e-12):
        return GEOMETRY_FLOOR
    return GEOMETRY_FALLBACK


def entry_index_for_fire(fire_idx: int, n_bars: int) -> int | None:
    """The first bar whose open may legally be used as an entry.

    The fire is confirmed by the close of ``fire_idx``, so the next bar's open is
    the earliest price a trader could actually pay. Returning ``fire_idx`` itself
    would be same-bar entry — the confirmation-causality shape that flipped all
    six structural-touch cells and withdrew that BUILD.
    """
    entry_idx = fire_idx + 1
    return entry_idx if entry_idx < n_bars else None


def simulate_exit(
    highs: npt.NDArray[np.float64],
    lows: npt.NDArray[np.float64],
    entry_idx: int,
    direction: str,
    *,
    sl: float,
    tp: float,
    tp_r: float,
) -> ExitResult:
    """Walk forward from ``entry_idx`` until a level is touched.

    A bar touching both levels is resolved as the LOSS and flagged ``ambiguous``:
    intrabar order is unknowable from OHLC, so the optimistic reading would
    manufacture edge exactly where the data is silent.
    """
    _check_direction(direction)
    n = len(highs)
    for k in range(entry_idx, n):
        high = float(highs[k])
        low = float(lows[k])
        if direction == "long":
            hit_sl = low <= sl
            hit_tp = high >= tp
        else:
            hit_sl = high >= sl
            hit_tp = low <= tp
        if hit_sl and hit_tp:
            # Pre-registered resolution is the LOSS. ``r_optimistic`` carries the
            # other reading so the tie-break's contribution can be bounded
            # without a second run — a sensitivity, never the verdict.
            return ExitResult(
                r=-1.0,
                bars_held=k - entry_idx,
                ambiguous=True,
                r_optimistic=tp_r,
            )
        if hit_sl:
            return ExitResult(r=-1.0, bars_held=k - entry_idx, ambiguous=False)
        if hit_tp:
            return ExitResult(r=tp_r, bars_held=k - entry_idx, ambiguous=False)
    return ExitResult(r=None, bars_held=max(n - entry_idx, 0), ambiguous=False)

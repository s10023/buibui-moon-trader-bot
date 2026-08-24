"""Fill-price rules shared by the backtest engine and the live outcome resolver.

A bar that *opens* beyond a level never filled at that level. Both a
stop-market and a resting limit fill at the open in that case, so booking
the level exactly is an optimistic bound on the stop side and a pessimistic
one on the target side — the error does not cancel, and the favourable
mirror is the larger of the two.

This lives OUTSIDE `engine.py` on purpose: the live outcome resolver
(`analytics/signal/outcome_backfill.py`) imports it without pulling in the
engine, so the two books cannot drift on the one rule they must share.
"""

from __future__ import annotations

from typing import Literal

# How the trigger tests the level: a long's stop and a short's target are
# crossed by trading down to them; a long's target and a short's stop by
# trading up to them. Naming the COMPARISON rather than the leg keeps the
# rule direction-agnostic — there is no long/short branch below.
CrossedWhen = Literal["at_or_below", "at_or_above"]


def level_is_on_the_expected_side(
    *, entry: float, level: float, crossed_when: CrossedWhen
) -> bool:
    """Is this level well-formed for the trigger that reads it?

    A long whose "stop" sits above entry is a malformed row: every bar would
    look gapped-through and the override would invent a fill. Guarding here
    means a bad row books the level unchanged instead.
    """
    if crossed_when == "at_or_below":
        return level <= entry
    return level >= entry


def gap_fill_price(
    *, entry: float, level: float, bar_open: float, crossed_when: CrossedWhen
) -> float:
    """The price this level actually filled at on a bar that triggered it.

    If the open already satisfies the trigger, the level was gapped through
    and the open is the fill; otherwise the bar traded to the level and the
    level is the fill. Opening exactly at the level is not a gap — both
    readings give the same number.
    """
    if not level_is_on_the_expected_side(
        entry=entry, level=level, crossed_when=crossed_when
    ):
        return level
    if crossed_when == "at_or_below":
        return min(bar_open, level)
    return max(bar_open, level)

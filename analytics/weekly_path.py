"""H10 — partial-path predictiveness (pure library).

Spec: docs/superpowers/specs/2026-07-23-h10-partial-path-predictiveness-design.md

Does the week's normalized path observable at hour `h` predict the return from
`h` to the week's close, beyond drift?

Two contaminants are neutralized by construction and each is unit-tested:

1. Arithmetic tautology — `terminal = path_at_h + remaining`, so the partial
   path correlates with the terminal value at sqrt(h/168) under a pure random
   walk. We test the REMAINING return, whose null is exactly zero.
2. Drift — `sign(path)` is +1 almost always in an up-drifting market, so a raw
   signed remaining return reads as skill. We sign the CAUSALLY DEMEANED
   remaining return (expanding mean over weeks strictly earlier).

Pure: no DB, no I/O, no network. The driver is tools/weekly_path_audit.py.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

WEEK_BARS = 168
GATED_HOURS = (24, 48, 72, 96, 120)  # end of Mon/Tue/Wed/Thu/Fri, UTC


@dataclass(frozen=True)
class PathConfig:
    """Pre-committed audit parameters (spec §6). Never tuned against a result."""

    hours: tuple[int, ...] = GATED_HOURS
    bar: float = 0.05  # AWR per week; ~2.5x round-trip cost
    alpha: float = 0.05
    min_n: int = 52  # weeks — one year of observations
    min_prior_obs: int = 52  # symbol-weeks required before the expanding mean is usable
    n_boot: int = 10_000
    seed: int | None = 12345


@dataclass(frozen=True)
class SymbolWeek:
    """One completed symbol-week: the AWR14-normalized 168-point close path."""

    symbol: str
    week: date
    norm_path: tuple[float, ...]


@dataclass(frozen=True)
class WeekObservation:
    """One calendar week, cross-section already collapsed (spec §5.1)."""

    week: date
    value: float  # mean of v across symbols live that week
    n_symbols: int
    mean_abs_signal: float  # mean |path[h-1]| — feeds the magnitude terciles


def _index_for(hour: int) -> int:
    """Hour `h` is index `h-1` — the close of the h-th completed bar."""
    if not 1 <= hour <= WEEK_BARS:
        raise ValueError(f"hour must be in 1..{WEEK_BARS}, got {hour}")
    return hour - 1


def signal_sign(norm_path: Sequence[float], hour: int) -> float:
    """sign(path at `hour`); 0.0 when exactly flat, which drops the week."""
    value = norm_path[_index_for(hour)]
    if value > 0.0:
        return 1.0
    if value < 0.0:
        return -1.0
    return 0.0


def remaining_return(norm_path: Sequence[float], hour: int) -> float:
    """Return from `hour` to the week's close, in AWR units."""
    return float(norm_path[WEEK_BARS - 1] - norm_path[_index_for(hour)])

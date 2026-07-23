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

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np

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


def build_observations(
    weeks: Sequence[SymbolWeek],
    hour: int,
    cfg: PathConfig,
) -> list[WeekObservation]:
    """One observation per calendar week: mean over symbols of the signed,
    causally demeaned remaining return.

    The expanding baseline `mu_t` is the mean raw remaining return over ALL
    symbol-weeks strictly EARLIER than week `t` — pooled across symbols, which
    keeps it stable while staying causal. A week is emitted only once at least
    `cfg.min_prior_obs` prior symbol-weeks exist, so the earliest weeks do not
    ride a one-sample baseline.

    Weeks whose signal is exactly flat contribute nothing (their sign is
    undefined); a calendar week with no contributing symbol is omitted.
    """
    # (sign, raw remaining return, |signal| magnitude) per contributing symbol.
    by_week: dict[date, list[tuple[float, float, float]]] = defaultdict(list)
    for sw in weeks:
        if len(sw.norm_path) != WEEK_BARS:
            continue
        sign = signal_sign(sw.norm_path, hour)
        if sign == 0.0:
            continue
        by_week[sw.week].append(
            (
                sign,
                remaining_return(sw.norm_path, hour),
                abs(sw.norm_path[_index_for(hour)]),
            )
        )

    out: list[WeekObservation] = []
    prior_sum = 0.0
    prior_count = 0
    for week in sorted(by_week):
        entries = by_week[week]
        if prior_count >= cfg.min_prior_obs:
            mu = prior_sum / prior_count
            values = [sign * (rem - mu) for sign, rem, _ in entries]
            magnitudes = [mag for _, _, mag in entries]
            out.append(
                WeekObservation(
                    week=week,
                    value=float(np.mean(values)),
                    n_symbols=len(entries),
                    mean_abs_signal=float(np.mean(magnitudes)),
                )
            )
        # Advance the baseline only AFTER emitting — week t must never price
        # against a mean that includes itself. This ordering IS the causality
        # guarantee; test_baseline_excludes_the_week_it_prices is the guard that
        # actually detects a move above the emit block (the broader
        # test_expanding_mean_is_causal does NOT — self-inclusion cancels out of
        # its base-vs-perturbed comparison).
        for _, rem, _ in entries:
            prior_sum += rem
            prior_count += 1
    return out

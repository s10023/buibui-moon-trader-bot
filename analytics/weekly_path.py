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
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from analytics.audit_guard import (
    DECISION_DISABLE,
    DECISION_ENABLE,
    AuditCell,
    evaluate_audit_cells,
)
from analytics.research_guards import (
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
)

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


# Module-level singleton so `evaluate_hours` can default to it without calling a
# constructor in an argument default (ruff B008). PathConfig is frozen, so one
# shared instance is safe.
DEFAULT_CONFIG = PathConfig()


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


VERDICT_PREDICTIVE = "PREDICTIVE"
VERDICT_REVERTING = "REVERTING"
VERDICT_NO_EDGE = "NO-EDGE"
VERDICT_INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True)
class HourVerdict:
    hour: int
    verdict: str
    n_weeks: int
    mean_v: float | None
    ci_lo: float | None
    ci_hi: float | None
    adj_pvalue: float | None
    early_mean: float | None
    late_mean: float | None
    reasons: list[str] = field(default_factory=list)


def _halves(values: list[float]) -> tuple[float | None, float | None]:
    """Means of the early and late halves, split at the median week."""
    if len(values) < 4:
        return None, None
    mid = len(values) // 2
    return float(np.mean(values[:mid])), float(np.mean(values[mid:]))


def evaluate_hours(
    weeks: Sequence[SymbolWeek],
    cfg: PathConfig = DEFAULT_CONFIG,
) -> list[HourVerdict]:
    """Pre-committed verdict per gated hour, sharing one Holm family (spec §6).

    IMPORTANT — audit_guard's decisions are INVERTED relative to the sign of the
    mean, because it was written for gate auditing where `supp_r` is a
    suppressed slice (analytics/audit_guard.py:196-207):

        DISABLE  <=> mean reliably POSITIVE  => PREDICTIVE here
        ENABLE   <=> mean reliably NEGATIVE  => REVERTING here

    Mapping ENABLE -> PREDICTIVE would invert every verdict.
    """
    series: dict[int, list[float]] = {}
    weeks_by_hour: dict[int, list[date]] = {}
    for hour in cfg.hours:
        obs = build_observations(weeks, hour, cfg)
        series[hour] = [o.value for o in obs]
        weeks_by_hour[hour] = [o.week for o in obs]

    # The cross-section is already collapsed to ONE observation per calendar
    # week (spec §5.1), so the week key yields singleton clusters and a design
    # effect of exactly 1.0 -- the same self-enforcing shape as
    # ``state_audit``. The calendar week, not the UTC day, is this module's
    # dependence unit, which is why it does not call ``utc_day_keys``.
    cells = [
        AuditCell(label=f"h{h}", supp_r=series[h], cluster_key=weeks_by_hour[h])
        for h in cfg.hours
    ]
    results = evaluate_audit_cells(
        cells,
        bar=cfg.bar,
        alpha=cfg.alpha,
        min_n=cfg.min_n,
        n_boot=cfg.n_boot,
        seed=cfg.seed,
        enable_concentrate=False,
    )

    out: list[HourVerdict] = []
    for hour, cell in zip(cfg.hours, results, strict=True):
        values = series[hour]
        n = len(values)
        early, late = _halves(values)
        reasons = list(cell.reasons)

        # NO-EDGE is a positive claim that no effect worth acting on exists,
        # so it needs `powered_null` (audit_guard: CI strictly inside ±bar).
        # It keyed off `n >= cfg.min_n` until 2026-08-13 — a sample-size
        # floor, which cannot tell a ruled-out effect from an unresolved one
        # and so made NO-EDGE always reachable. Corrected across H8/H9/H14
        # in the same pass.
        if cell.decision == DECISION_DISABLE:
            verdict = VERDICT_PREDICTIVE
        elif cell.decision == DECISION_ENABLE:
            verdict = VERDICT_REVERTING
        elif cell.powered_null:
            verdict = VERDICT_NO_EDGE
        else:
            verdict = VERDICT_INSUFFICIENT

        # The early/late split is a verdict CONDITION, not a footnote (spec §6.1).
        if verdict in (VERDICT_PREDICTIVE, VERDICT_REVERTING):
            headline = cell.supp_avg or 0.0
            if (
                early is None
                or late is None
                or np.sign(early) != np.sign(headline)
                or np.sign(late) != np.sign(headline)
            ):
                verdict = VERDICT_INSUFFICIENT
                reasons.append(
                    f"time-split sign disagreement (early {early}, late {late}, "
                    f"headline {headline})"
                )

        out.append(
            HourVerdict(
                hour=hour,
                verdict=verdict,
                n_weeks=n,
                mean_v=cell.supp_avg,
                ci_lo=cell.ci_lo,
                ci_hi=cell.ci_hi,
                adj_pvalue=cell.adj_pvalue,
                early_mean=early,
                late_mean=late,
                reasons=reasons,
            )
        )
    return out


@dataclass(frozen=True)
class MagnitudeRow:
    hour: int
    tercile: int  # 1 = smallest |signal|, 3 = largest
    n_weeks: int
    mean_v: float


@dataclass(frozen=True)
class CurvePoint:
    hour: int
    n_weeks: int
    mean_v: float


@dataclass(frozen=True)
class FamilyStamps:
    n_trials: int
    best_hour: int | None
    best_sharpe: float | None
    dsr: float | None
    pbo: float | None
    min_trl: float | None


def magnitude_breakdown(
    weeks: Sequence[SymbolWeek],
    hour: int,
    cfg: PathConfig,
) -> list[MagnitudeRow]:
    """Mean v within terciles of |signal| (spec §7 — reported, never gating).

    Terciles are RANK-based (a stable sort of the qualifying symbol-weeks by
    |path[h-1]|, split into three near-equal-count groups), not value-threshold
    cuts on the quantiles of the pooled distribution. A value-threshold cut
    degenerates to one all-or-nothing bucket whenever the magnitude
    distribution has ties at the cut points (e.g. a population where |signal|
    is a constant by construction) — rank-based splitting still yields three
    non-empty groups whenever there are enough qualifying weeks. Each tercile
    is then collapsed per calendar week exactly as the headline is.

    Rank-based splitting forces equal-sized terciles, which means that under
    heavy ties identical |signal| values can land in DIFFERENT terciles
    (whichever side of the equal-count boundary the stable sort places them
    on). This is immaterial here: the input is continuous AWR-normalized
    price data, where exact ties in |signal| are effectively impossible. It
    is still the preferred behaviour over value-threshold cuts, which
    collapse to a single populated bucket when magnitudes are tied, rather
    than merely reassigning a handful of boundary rows.
    """
    idx = _index_for(hour)
    candidates = [
        sw
        for sw in weeks
        if len(sw.norm_path) == WEEK_BARS and signal_sign(sw.norm_path, hour) != 0.0
    ]
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda sw: abs(sw.norm_path[idx]))
    groups = np.array_split(np.arange(len(ordered)), 3)

    rows: list[MagnitudeRow] = []
    for tercile, group in zip((1, 2, 3), groups, strict=True):
        subset = [ordered[i] for i in group]
        obs = build_observations(subset, hour, cfg)
        values = [o.value for o in obs]
        rows.append(
            MagnitudeRow(
                hour=hour,
                tercile=tercile,
                n_weeks=len(values),
                mean_v=float(np.mean(values)) if values else 0.0,
            )
        )
    return rows


def hour_curve(weeks: Sequence[SymbolWeek], cfg: PathConfig) -> list[CurvePoint]:
    """mean(v) at every hour 1..168 — a descriptive shape, never gating.

    Read the tail with care: ``remaining`` is identically 0 at hour 168, so
    ``mean_v`` there is 0 by construction, and ``sd(remaining)`` shrinks
    monotonically toward the week's end, mechanically compressing everything
    past roughly hour 144. The curve's RISE (against that shrinking scale) is
    informative; its terminal decay is largely structural. Do not read a peak
    hour off this curve precisely — the gated verdicts, not the curve, decide.
    """
    points: list[CurvePoint] = []
    for hour in range(1, WEEK_BARS + 1):
        values = [o.value for o in build_observations(weeks, hour, cfg)]
        points.append(
            CurvePoint(
                hour=hour,
                n_weeks=len(values),
                mean_v=float(np.mean(values)) if values else 0.0,
            )
        )
    return points


def _sharpe(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    arr = np.asarray(values, dtype=np.float64)
    sd = float(np.std(arr, ddof=1))
    if sd == 0.0:
        return None
    return float(np.mean(arr) / sd)


def family_stamps(weeks: Sequence[SymbolWeek], cfg: PathConfig) -> FamilyStamps:
    """DSR / PBO / MinTRL over the gated-hour family (spec §6).

    The hours are reported together and none is selected, but the stamps make
    the family size visible. PBO needs equal-length columns, so it runs over the
    intersection of weeks present at every hour. Degrades to None rather than
    raising, so a thin run still renders a report.
    """
    series = {h: build_observations(weeks, h, cfg) for h in cfg.hours}
    by_hour = {h: [o.value for o in obs] for h, obs in series.items()}

    # Explicit annotation: mypy strict will not narrow `float | None` -> `float`
    # through a dict comprehension's `if` clause.
    usable: dict[int, float] = {}
    for h, values in by_hour.items():
        sr = _sharpe(values)
        if sr is not None:
            usable[h] = sr
    if not usable:
        return FamilyStamps(len(cfg.hours), None, None, None, None, None)

    best_hour = max(usable, key=lambda h: usable[h])
    best_sr = usable[best_hour]

    dsr: float | None = None
    n_obs = len(by_hour[best_hour])
    if n_obs >= 2 and len(usable) >= 2:
        dsr = deflated_sharpe_ratio(
            best_sr, n_obs, trial_srs=[usable[h] for h in sorted(usable)]
        )

    min_trl: float | None = None
    if best_sr > 0.0:
        value = min_track_record_length(best_sr, confidence=0.95)
        min_trl = None if value == float("inf") else value

    pbo: float | None = None
    common = set.intersection(*({o.week for o in series[h]} for h in cfg.hours))
    # cscv_pbo's default n_splits=14 needs >= 2 rows per block, so >= 28 rows.
    if len(common) >= 28:
        order = sorted(common)
        matrix = np.array(
            [
                [next(o.value for o in series[h] if o.week == w) for h in cfg.hours]
                for w in order
            ],
            dtype=np.float64,
        )
        try:
            pbo = cscv_pbo(matrix).pbo
        except ValueError:
            pbo = None

    return FamilyStamps(len(cfg.hours), best_hour, best_sr, dsr, pbo, min_trl)

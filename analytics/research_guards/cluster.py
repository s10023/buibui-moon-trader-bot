"""Cluster-aware CI and design-effect deflation for grouped observations.

A block bootstrap absorbs **serial** dependence — it resamples runs of
observations that are adjacent *in the array it is handed*. Two things defeat it
on this repo's audit panels, and they are separate failures:

1. **Cross-sectional dependence is not adjacency.** Three symbols firing on the
   same UTC day are three rows scattered through the array, and no block length
   reaches them.
2. **Adjacency itself is meaningless here.** Not one trade query feeding an
   :class:`~analytics.audit_guard.AuditCell` carries an ``ORDER BY`` — every
   ``ORDER BY`` in the eleven consumers is on an OHLCV or run-selection query —
   so the array order is DuckDB's storage order and the block bootstrap
   resamples runs of rows with no temporal relationship at all.

Measured on this repo's own panel (125 cells cut ``strategy × timeframe ×
direction`` at ``n ≥ 30``, 166,384 distinct trades,
``docs/audits/2026-08-25-st80-audit-guard-cluster-key.md``): median ICC by UTC
day **0.445**, median design effect **1.670**, cluster CIs **1.251×** wider than
the block-bootstrap ones they replace. The analytic route (median ``√DEFF``,
1.292) and the resampling route (1.251) agree, which is why both are implemented
here rather than one standing in for the other.

⚠ **The median is not the decision statistic.** DEFF is heavily skewed —
trade-weighted **4.991**, max **9.89** — because the deflation concentrates in
the high-volume 15m cells, which are 64.4% of the live ledger. The cells that
currently carry significance are exactly the cells the correction bites.

Two functions, because a verdict needs both legs and they fail differently:

* :func:`cluster_bootstrap_ci` resamples whole **clusters**, so the CI widens to
  match the real information content.
* :func:`cluster_stats` returns the design effect and ``n_eff = n_obs / DEFF``
  for the **significance** leg. That leg is the larger of the two here — an
  undeflated ``t = sr·√n_trades`` called 70 of 125 cells significant where 52
  survive.

⚠ **This is the SAME correction as the 2.92× cross-sectional deflator, computed
a second way — never a second correction to apply beside it.**
``analytics.forecast.effective_independent_series`` is
``n_eff = k / (1 + (k−1)·ρ)``; a design effect on a fully-populated
cross-section is ``DEFF = 1 + (k−1)·ρ``, so
``n_eff = k / DEFF`` is the same line read twice. At ``AGENTS.md``'s filed
``ρ = 0.315``, ``k = 25``: ``DEFF = 8.56`` and ``25 / 8.56 = 2.92``, the filed
figure to three digits. Apply one or the other in any one place, never both.

The overlap buys a free safety property: ``AGENTS.md``'s rule that *book-day rows
are already aggregated and must NOT be deflated again* enforces itself under a
day key. One row per day is a singleton cluster, so ICC is 0, DEFF is 1, and the
deflation is a no-op — a caller cannot double-count by forgetting the rule.

⚠ **The deflator can only shrink.** ``icc`` is clamped to ``[0, 1]`` and
``design_effect`` to ``>= 1``, so a negative sample ICC — routine on small
panels — never manufactures *more* independence than the raw count. The failure
this guards against is a confident number, so the safe direction is fewer
effective observations, never more.

Pure math (numpy only), no DB / IO.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from analytics.research_guards.bootstrap import BootstrapCI

MS_PER_DAY = 86_400_000


@dataclass(frozen=True)
class ClusterStats:
    """Design-effect deflation for one grouped sample.

    ``n_eff`` is the count to hand a t-statistic, never ``n_obs``. It is
    clamped to ``[1, n_obs]``: a single cluster carries one observation's worth
    of information no matter how many rows it holds, and perfectly uncorrelated
    clusters carry exactly the raw count and no more.
    """

    n_obs: int
    n_clusters: int
    mean_size: float
    icc: float
    design_effect: float
    n_eff: float


def utc_day_keys(ts_ms: Sequence[int]) -> list[int]:
    """UTC calendar day per millisecond stamp — this repo's cluster unit.

    ⚠ **On a 24/7 tape this is a LOWER BOUND on the dependence unit, not the
    right answer**, and the distinction is the whole reason this function does
    not simply mirror the equities fork's. There a UTC date and a trading
    session name the same thing, so the day is exact. Here the tape is
    continuous and the UTC midnight is a cut through it, so a trade at 23:50 and
    one at 00:10 share a driver and land in different clusters.

    **Measured, and it does not plateau at the day** (125 cells, median DEFF):

    ==========  ========
    unit        med DEFF
    ==========  ========
    12h          1.508
    **UTC day**  **1.670**
    2 days       1.873
    1 week       2.366
    ==========  ========

    Dependence keeps being found as the window widens, so every figure this key
    produces under-corrects. It is chosen anyway because it is the only unit
    with an independent anchor on this tape — the ``1d`` bar, ``day_filter``,
    and the regime / ADR / DOW context are all keyed to the UTC day — and a
    wider window would be a free parameter picked to move a verdict. Read a
    ``design_effect`` as a floor and a surviving verdict as conservative; a
    verdict that dies under this key would have died harder under a wider one.

    Keys are opaque and compared only for equality, so a caller with a better
    unit can pass anything hashable — a ``(symbol, day)`` tuple, an era id, an
    ISO date. One definition lives here so two consumers cannot silently
    cluster on different units and report comparable numbers.
    """
    return [int(t) // MS_PER_DAY for t in ts_ms]


def _group_indices(cluster_key: Sequence[Hashable]) -> list[npt.NDArray[np.int64]]:
    """Row indices per distinct key, order-stable so a seeded run reproduces."""
    order: dict[Hashable, list[int]] = {}
    for i, k in enumerate(cluster_key):
        order.setdefault(k, []).append(i)
    return [np.asarray(v, dtype=np.int64) for v in order.values()]


def cluster_stats(
    values: npt.NDArray[np.float64],
    cluster_key: Sequence[Hashable],
) -> ClusterStats:
    """One-way random-effects ICC and the design effect it implies.

    ``ICC = (MSB - MSW) / (MSB + (m0 - 1)·MSW)`` with the unbalanced-design
    adjusted cluster size ``m0 = (N - Σnᵢ²/N) / (k - 1)``; the design effect is
    then ``1 + (m̄ - 1)·ICC`` on the *plain* mean cluster size, matching the
    figure the audit reports.

    Three degenerate shapes, all resolved toward LESS information:

    * **one cluster** — every observation is in it, so ``n_eff`` is 1. This is
      the honest answer, not a guard: a single day of trades is one draw.
    * **every observation its own cluster** — ``MSW`` has no degrees of freedom,
      so ICC is 0 and ``n_eff == n_obs``. Nothing is clustered, so nothing is
      deflated. This is also the shape that makes the book-day rule
      self-enforcing (see the module docstring).
    * **no dispersion** — the ICC denominator vanishes and ICC is 0.
    """
    n_obs = int(values.shape[0])
    groups = _group_indices(cluster_key)
    k = len(groups)
    if n_obs == 0 or k == 0:
        return ClusterStats(n_obs, k, 0.0, 0.0, 1.0, 0.0)

    sizes = np.asarray([g.shape[0] for g in groups], dtype=np.float64)
    mean_size = float(sizes.mean())

    if k == 1:
        # Total clustering: one draw, however many rows it carries.
        return ClusterStats(n_obs, 1, mean_size, 1.0, float(n_obs), 1.0)
    if k == n_obs:
        # Singleton clusters: MSW has no degrees of freedom and nothing is grouped.
        return ClusterStats(n_obs, k, mean_size, 0.0, 1.0, float(n_obs))

    grand = float(values.mean())
    group_means = np.asarray([float(values[g].mean()) for g in groups])
    ss_between = float((sizes * (group_means - grand) ** 2).sum())
    ss_within = float(
        sum(float(((values[g] - values[g].mean()) ** 2).sum()) for g in groups)
    )
    ms_between = ss_between / (k - 1)
    ms_within = ss_within / (n_obs - k)

    m0 = (n_obs - float((sizes**2).sum()) / n_obs) / (k - 1)
    denom = ms_between + (m0 - 1.0) * ms_within
    icc = 0.0 if denom <= 0.0 else (ms_between - ms_within) / denom
    icc = float(min(1.0, max(0.0, icc)))

    design_effect = max(1.0, 1.0 + (mean_size - 1.0) * icc)
    n_eff = float(min(float(n_obs), max(1.0, n_obs / design_effect)))
    return ClusterStats(n_obs, k, mean_size, icc, design_effect, n_eff)


def cluster_bootstrap_ci(
    values: npt.NDArray[np.float64],
    cluster_key: Sequence[Hashable],
    stat_fn: Callable[[npt.NDArray[np.float64]], float],
    *,
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int | None = None,
) -> BootstrapCI:
    """Percentile CI resampling whole CLUSTERS with replacement.

    The unit of resampling is the cluster, so every observation sharing a key
    travels together and the same-day correlation the block bootstrap could not
    reach is preserved in each replicate.

    Replicates are variable length by construction — drawing ``k`` clusters with
    replacement lands a different row count each time — which is correct and is
    why ``stat_fn`` must be a scale-free statistic such as a mean.

    Fewer than two clusters yields ``(-inf, +inf)``: an interval that cannot
    clear any bar, so the verdict falls to ``INSUFFICIENT`` rather than to a
    narrow CI invented from a single draw.
    """
    n_obs = int(values.shape[0])
    groups = _group_indices(cluster_key)
    k = len(groups)
    point = stat_fn(values) if n_obs else float("nan")
    if n_obs == 0 or k < 2:
        return BootstrapCI(point, float("-inf"), float("inf"), alpha, 0)

    rng = np.random.default_rng(seed)
    stats = np.empty(n_boot, dtype=np.float64)
    valid = 0
    for _b in range(n_boot):
        picks = rng.integers(0, k, size=k)
        idx = np.concatenate([groups[p] for p in picks])
        stat = stat_fn(values[idx])
        if np.isfinite(stat):
            stats[valid] = stat
            valid += 1
    if valid == 0:
        return BootstrapCI(point, float("-inf"), float("inf"), alpha, 0)

    used = stats[:valid]
    lo = float(np.percentile(used, 100.0 * alpha / 2.0))
    hi = float(np.percentile(used, 100.0 * (1.0 - alpha / 2.0)))
    return BootstrapCI(point, lo, hi, alpha, valid)

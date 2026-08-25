"""Tests for `analytics/research_guards/cluster.py`.

The module exists because a block bootstrap cannot reach cross-sectional
dependence, and because no trade query feeding an `AuditCell` is ordered, so its
adjacency was DuckDB storage order. Both legs of a verdict are priced on the
cluster unit; these tests pin the primitives.
"""

from __future__ import annotations

import numpy as np

from analytics.research_guards import (
    block_bootstrap_ci,
    cluster_bootstrap_ci,
    cluster_stats,
    utc_day_keys,
)

MS_PER_DAY = 86_400_000


def _mean(a: np.ndarray) -> float:
    return float(np.mean(a))


def _clustered(n_days: int, per_day: int, spread: float, seed: int) -> tuple:
    """A panel whose whole day moves together — the shape the block bootstrap
    is blind to."""
    rng = np.random.default_rng(seed)
    vals, keys = [], []
    for d in range(n_days):
        day_effect = rng.normal(0.0, spread)
        for _ in range(per_day):
            vals.append(day_effect + rng.normal(0.0, 0.01))
            keys.append(d)
    return np.asarray(vals, dtype=np.float64), keys


class TestUtcDayKeys:
    def test_floors_millis_to_the_utc_day(self) -> None:
        assert utc_day_keys([0, MS_PER_DAY - 1, MS_PER_DAY, 3 * MS_PER_DAY]) == [
            0,
            0,
            1,
            3,
        ]

    def test_two_stamps_in_one_day_share_a_key(self) -> None:
        # 09:00 and 23:59 on the same UTC date.
        a = 5 * MS_PER_DAY + 9 * 3_600_000
        b = 5 * MS_PER_DAY + 23 * 3_600_000
        assert utc_day_keys([a, b]) == [5, 5]

    def test_the_midnight_boundary_splits_a_shared_driver(self) -> None:
        """The known cost of this key on a 24/7 tape, pinned as a fact.

        23:50 and 00:10 are twenty minutes apart and land in different
        clusters. That is why `utc_day_keys` documents itself as a LOWER bound
        on the dependence unit — a test that hid this would be hiding the one
        thing a reader must know before quoting a design effect.
        """
        late = 5 * MS_PER_DAY + 23 * 3_600_000 + 50 * 60_000
        early = 6 * MS_PER_DAY + 10 * 60_000
        assert utc_day_keys([late, early]) == [5, 6]


class TestClusterStats:
    def test_singleton_clusters_deflate_nothing(self) -> None:
        """The property that makes AGENTS.md's book-day rule self-enforcing.

        One row per day means one observation per cluster, so a caller whose
        rows are ALREADY daily aggregates cannot double-deflate by forgetting
        the rule — the arithmetic is a no-op.
        """
        vals = np.asarray([0.1, -0.2, 0.3, 0.5, -0.1], dtype=np.float64)
        cs = cluster_stats(vals, list(range(5)))
        assert cs.n_clusters == 5
        assert cs.icc == 0.0
        assert cs.design_effect == 1.0
        assert cs.n_eff == 5.0

    def test_one_cluster_is_one_draw(self) -> None:
        vals = np.asarray([0.1] * 40, dtype=np.float64)
        cs = cluster_stats(vals, [7] * 40)
        assert cs.n_clusters == 1
        assert cs.n_eff == 1.0
        assert cs.design_effect == 40.0

    def test_a_correlated_panel_deflates(self) -> None:
        vals, keys = _clustered(n_days=40, per_day=8, spread=0.5, seed=1)
        cs = cluster_stats(vals, keys)
        assert cs.n_clusters == 40
        assert cs.icc > 0.9  # day effect dominates the within-day noise
        assert cs.design_effect > 6.0
        assert cs.n_eff < 60.0  # 320 rows are nowhere near 320 draws

    def test_the_deflator_can_only_shrink(self) -> None:
        """A negative sample ICC is routine on small panels and must never
        manufacture MORE independence than there are observations."""
        rng = np.random.default_rng(3)
        # Anti-correlated within cluster: raw ICC estimate goes negative.
        vals = np.asarray(
            [v for _ in range(30) for v in (1.0, -1.0)], dtype=np.float64
        ) + rng.normal(0, 0.01, 60)
        keys = [d for d in range(30) for _ in range(2)]
        cs = cluster_stats(vals, keys)
        assert cs.icc >= 0.0
        assert cs.design_effect >= 1.0
        assert cs.n_eff <= float(vals.shape[0])

    def test_no_dispersion_gives_no_deflation(self) -> None:
        vals = np.zeros(20, dtype=np.float64)
        cs = cluster_stats(vals, [d // 4 for d in range(20)])
        assert cs.icc == 0.0
        assert cs.design_effect == 1.0

    def test_empty_input_is_not_an_error(self) -> None:
        cs = cluster_stats(np.asarray([], dtype=np.float64), [])
        assert cs.n_obs == 0
        assert cs.n_eff == 0.0


class TestClusterBootstrapCI:
    def test_it_is_wider_than_the_block_bootstrap_it_replaces(self) -> None:
        """The headline: the block bootstrap is blind to this structure.

        The values are grouped by day but the ARRAY is symbol-blocked, exactly
        as `warning_audit.tag_trades` builds it — so same-day rows are maximally
        far apart and no block length reaches them.
        """
        vals, keys = _clustered(n_days=30, per_day=6, spread=0.4, seed=5)
        order = np.argsort([i % 6 for i in range(len(keys))], kind="stable")
        vals, keys = vals[order], [keys[i] for i in order]

        block = block_bootstrap_ci(
            vals, _mean, n_boot=1500, alpha=0.05, method="circular", seed=11
        )
        clus = cluster_bootstrap_ci(vals, keys, _mean, n_boot=1500, alpha=0.05, seed=11)
        assert (clus.hi - clus.lo) > 2.0 * (block.hi - block.lo)

    def test_fewer_than_two_clusters_refuses_rather_than_inventing_a_ci(self) -> None:
        vals = np.asarray([0.5] * 40, dtype=np.float64)
        ci = cluster_bootstrap_ci(vals, [1] * 40, _mean, n_boot=200, seed=2)
        assert ci.lo == float("-inf")
        assert ci.hi == float("inf")
        assert ci.n_valid == 0

    def test_it_is_reproducible_under_a_seed(self) -> None:
        vals, keys = _clustered(n_days=20, per_day=4, spread=0.3, seed=9)
        a = cluster_bootstrap_ci(vals, keys, _mean, n_boot=400, seed=42)
        b = cluster_bootstrap_ci(vals, keys, _mean, n_boot=400, seed=42)
        assert (a.lo, a.hi) == (b.lo, b.hi)

    def test_singleton_clusters_reproduce_an_iid_bootstrap(self) -> None:
        """With nothing grouped there is nothing to absorb, so the cluster
        bootstrap must not widen anything on its own."""
        rng = np.random.default_rng(17)
        vals = rng.normal(0.2, 1.0, 300)
        clus = cluster_bootstrap_ci(
            vals, list(range(300)), _mean, n_boot=1500, alpha=0.05, seed=4
        )
        block = block_bootstrap_ci(
            vals, _mean, n_boot=1500, alpha=0.05, method="circular", seed=4
        )
        ratio = (clus.hi - clus.lo) / (block.hi - block.lo)
        assert 0.8 < ratio < 1.25

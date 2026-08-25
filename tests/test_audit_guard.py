"""Tests for `analytics/audit_guard.py` — bootstrap-CI + haircut audit verdicts.

The engine replaces the crude ±0.05R bar in the audit tools with two gates that
must BOTH hold for an ENABLE/DISABLE verdict:

  1. a CLUSTER bootstrap CI on the suppressed slice's mean R must clear the
     ±bar on the correct side, and
  2. the Holm multiple-testing adjusted p-value (across the tested-cell family)
     must be significant (< alpha), on `n_eff = n / DEFF` rather than the
     trade count.

Cells below `min_n`, or carrying an unusable `cluster_key`, are INSUFFICIENT
and excluded from the haircut family.

Most fixtures here pass one distinct key per row. That is not boilerplate — it
is the fixture DECLARING that its draws are independent, which is what these
verdict tests have always assumed. `TestClustering` below is where the key
carries real structure.
"""

from __future__ import annotations

import numpy as np

from analytics import audit_guard
from analytics.audit_guard import (
    DECISION_CONCENTRATE,
    DECISION_DISABLE,
    DECISION_ENABLE,
    DECISION_INSUFFICIENT,
    AuditCell,
)


def _normal_cell(
    mean: float,
    std: float,
    n: int,
    *,
    seed: int,
    kept: list[float] | None = None,
    label: str = "c",
) -> AuditCell:
    rng = np.random.default_rng(seed)
    supp = rng.normal(mean, std, n).tolist()
    return AuditCell(
        label=label,
        supp_r=supp,
        cluster_key=list(range(n)),
        kept_r=kept or [],
    )


# Small n_boot keeps the suite fast; verdicts on well-separated slices are
# stable far below the production default.
_KW = {"n_boot": 1500, "seed": 7}


class TestEnableDisable:
    def test_enable_when_suppressed_slice_reliably_loses(self) -> None:
        # Noisy losers well below -bar → CI clears -bar, highly significant.
        cell = _normal_cell(-0.6, 0.7, 80, seed=1)
        [v] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        assert v.decision == DECISION_ENABLE
        assert v.ci_hi is not None and v.ci_hi <= -0.05
        assert v.adj_pvalue is not None and v.adj_pvalue < 0.05

    def test_disable_when_suppressed_slice_reliably_wins(self) -> None:
        # Suppressed slice are reliable winners; kept does NOT outperform.
        kept = np.random.default_rng(2).normal(0.1, 0.7, 80).tolist()
        cell = _normal_cell(0.6, 0.7, 80, seed=3, kept=kept)
        [v] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        assert v.decision == DECISION_DISABLE
        assert v.ci_lo is not None and v.ci_lo >= 0.05

    def test_insufficient_when_ci_straddles_the_bar(self) -> None:
        # Mean barely negative, wide noise → CI straddles -bar → INSUFFICIENT
        # even though the point estimate is < 0 (the OLD ±0.05R bar might fire).
        cell = _normal_cell(-0.03, 0.6, 80, seed=4)
        [v] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        assert v.decision == DECISION_INSUFFICIENT


class TestConcentrate:
    def test_concentrate_when_kept_outperforms_positive_suppressed(self) -> None:
        kept = np.random.default_rng(5).normal(1.2, 0.5, 80).tolist()
        cell = _normal_cell(0.3, 0.5, 80, seed=6, kept=kept)
        [v] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        assert v.decision == DECISION_CONCENTRATE

    def test_concentrate_folds_into_disable_when_disabled(self) -> None:
        kept = np.random.default_rng(5).normal(1.2, 0.5, 80).tolist()
        cell = _normal_cell(0.3, 0.5, 80, seed=6, kept=kept)
        [v] = audit_guard.evaluate_audit_cells(
            [cell],
            enable_concentrate=False,
            **_KW,  # type: ignore[arg-type]
        )
        assert v.decision == DECISION_DISABLE

    def test_no_concentrate_when_kept_does_not_clear_delta(self) -> None:
        # supp ~ +0.5, kept ~ +0.52 — delta < bar → stays DISABLE.
        kept = np.random.default_rng(7).normal(0.52, 0.4, 80).tolist()
        cell = _normal_cell(0.5, 0.4, 80, seed=8, kept=kept)
        [v] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        assert v.decision == DECISION_DISABLE


class TestMinN:
    def test_below_min_n_is_insufficient_and_excluded_from_family(self) -> None:
        small = _normal_cell(-0.6, 0.7, 10, seed=9, label="small")
        big = _normal_cell(-0.6, 0.7, 80, seed=10, label="big")
        verdicts = audit_guard.evaluate_audit_cells([small, big], **_KW)  # type: ignore[arg-type]
        by_label = {c.label: v for c, v in zip([small, big], verdicts, strict=True)}
        assert by_label["small"].decision == DECISION_INSUFFICIENT
        assert by_label["small"].adj_pvalue is None  # not bootstrapped / not tested
        # `big` evaluated as the SOLE family member (n_tests == 1).
        assert by_label["big"].n_tests == 1
        assert by_label["big"].decision == DECISION_ENABLE

    def test_empty_input_returns_empty(self) -> None:
        assert audit_guard.evaluate_audit_cells([]) == []


class TestHaircut:
    def test_adj_pvalue_grows_with_family_size(self) -> None:
        # Same strong cell judged alone vs inside a 15-cell family: the Holm
        # haircut can only make the adjusted p-value larger (or equal).
        target = _normal_cell(-0.4, 0.8, 120, seed=20, label="t")
        nulls = [_normal_cell(0.0, 1.0, 120, seed=30 + i) for i in range(14)]

        [alone] = audit_guard.evaluate_audit_cells([target], **_KW)  # type: ignore[arg-type]
        family = audit_guard.evaluate_audit_cells([target, *nulls], **_KW)  # type: ignore[arg-type]
        in_family = family[0]

        assert alone.adj_pvalue is not None and in_family.adj_pvalue is not None
        assert in_family.adj_pvalue >= alone.adj_pvalue

    def test_haircut_demotes_marginal_cell_to_insufficient(self) -> None:
        # A cell whose CI comfortably clears -bar and is significant ALONE
        # (raw p ~ 0.005) is demoted to INSUFFICIENT inside a 20-cell family,
        # purely by the multiple-testing haircut (Holm ×20 ~ 0.10) — its CI
        # still clears the bar.
        target = _normal_cell(-0.5, 1.43, 60, seed=42, label="marg")
        nulls = [_normal_cell(0.0, 1.0, 60, seed=50 + i) for i in range(19)]

        [alone] = audit_guard.evaluate_audit_cells([target], **_KW)  # type: ignore[arg-type]
        assert alone.decision == DECISION_ENABLE

        family = audit_guard.evaluate_audit_cells([target, *nulls], **_KW)  # type: ignore[arg-type]
        in_family = family[0]
        assert in_family.decision == DECISION_INSUFFICIENT
        # CI unchanged by family size → the flip is the haircut, not the CI.
        assert in_family.ci_hi is not None and in_family.ci_hi <= -0.05
        assert in_family.adj_pvalue is not None and in_family.adj_pvalue >= 0.05


class TestDegenerate:
    def test_zero_variance_deterministic_loss_enables(self) -> None:
        # 40 identical -1.0R trades: no sampling uncertainty → maximally
        # significant deterministic loss → ENABLE.
        cell = AuditCell(
            label="z",
            supp_r=[-1.0] * 40,
            cluster_key=list(range(40)),
            kept_r=[1.0] * 10,
        )
        [v] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        assert v.decision == DECISION_ENABLE

    def test_zero_variance_deterministic_win_disables(self) -> None:
        cell = AuditCell(
            label="z",
            supp_r=[1.0] * 40,
            cluster_key=list(range(40)),
            kept_r=[-1.0] * 10,
        )
        [v] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        assert v.decision == DECISION_DISABLE


class TestMethodOption:
    def test_bonferroni_method_runs(self) -> None:
        cell = _normal_cell(-0.6, 0.7, 80, seed=1)
        [v] = audit_guard.evaluate_audit_cells(
            [cell],
            haircut_method="bonferroni",
            **_KW,  # type: ignore[arg-type]
        )
        assert v.decision == DECISION_ENABLE


def test_powered_null_requires_the_ci_inside_the_bar() -> None:
    """A powered null is CI CONTAINMENT, never ``n >= min_n``.

    Both cells below have the SAME mean (0.0) and the SAME n (50, well past
    ``min_n``), so any criterion keyed on sample size calls both "powered".
    They differ only in dispersion, which is the thing that decides whether an
    effect the size of the bar has actually been ruled out.
    """
    # NOT an alternating [+x, -x] sequence: every even-length block of one of
    # those averages to exactly 0, so the circular block bootstrap returns
    # CI [0, 0] however large x is, and the "wide" cell is not wide at all.
    # Same shape for both, differing only by a factor of 100 in scale.
    tight = audit_guard.AuditCell("tight", [-0.01] * 25 + [0.01] * 25, list(range(50)))
    wide = audit_guard.AuditCell("wide", [-1.0] * 25 + [1.0] * 25, list(range(50)))

    tv, wv = audit_guard.evaluate_audit_cells([tight, wide], bar=0.05)

    # Positive control: the fixture must actually produce a wide CI, or the
    # discrimination below is vacuous regardless of what the assertions say.
    assert wv.ci_lo is not None and wv.ci_hi is not None
    assert wv.ci_hi - wv.ci_lo > 0.05

    # Neither clears the bar, so both are INSUFFICIENT decisions...
    assert tv.decision == audit_guard.DECISION_INSUFFICIENT
    assert wv.decision == audit_guard.DECISION_INSUFFICIENT
    assert tv.n_supp == wv.n_supp == 50  # positive control: n cannot separate them

    # ...but only the tight cell has ruled out an effect at the bar.
    assert tv.powered_null is True
    assert wv.powered_null is False


def test_powered_null_is_false_when_the_cell_was_never_tested() -> None:
    """Below ``min_n`` no CI is computed, so containment is unknowable."""
    (v,) = audit_guard.evaluate_audit_cells(
        [audit_guard.AuditCell("thin", [0.0] * 5, list(range(5)))], bar=0.05, min_n=30
    )
    assert v.decision == audit_guard.DECISION_INSUFFICIENT
    assert v.ci_lo is None
    assert v.powered_null is False


# --------------------------------------------------------------------------- #
# The cluster key: both legs, fail closed, and reported
# --------------------------------------------------------------------------- #


def _day_clustered(
    n_days: int, per_day: int, mean: float, spread: float, seed: int
) -> AuditCell:
    """A cell whose whole day moves together, with the array SYMBOL-BLOCKED so
    same-day rows are maximally far apart — the shape `warning_audit.tag_trades`
    builds and the one a block bootstrap structurally cannot reach.
    """
    rng = np.random.default_rng(seed)
    vals: list[float] = []
    keys: list[int] = []
    for _slot in range(per_day):
        for d in range(n_days):
            rng_day = np.random.default_rng(seed * 1000 + d)
            vals.append(mean + rng_day.normal(0.0, spread) + rng.normal(0.0, 0.01))
            keys.append(d)
    return AuditCell(label="clustered", supp_r=vals, cluster_key=keys)


class TestClustering:
    def test_a_clustered_cell_reports_its_real_sample_size(self) -> None:
        cell = _day_clustered(30, 8, mean=0.4, spread=0.5, seed=3)
        [v] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        assert v.n_supp == 240
        assert v.n_clusters == 30
        assert v.design_effect is not None and v.design_effect > 5.0

    def test_deflation_is_what_stops_a_clustered_cell_resolving(self) -> None:
        """The correction, isolated: SAME values, only the key changes.

        Told the rows are independent the cell resolves; told the truth — that
        240 rows are 30 days — it does not. Nothing else differs, so this is
        the deflation and not a width knob.
        """
        # mean 0.10 against a day-to-day spread of 0.6: 200 rows clear the bar
        # comfortably (CI [+0.149, +0.327], p 0.0000), 25 days do not
        # (CI [-0.019, +0.493], p 0.067). That gap IS the defect.
        cell = _day_clustered(25, 8, mean=0.10, spread=0.6, seed=3)
        as_independent = AuditCell(
            label="asserted-independent",
            supp_r=cell.supp_r,
            cluster_key=list(range(len(cell.supp_r))),
        )
        [honest] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        [naive] = audit_guard.evaluate_audit_cells([as_independent], **_KW)  # type: ignore[arg-type]

        assert naive.decision == DECISION_DISABLE
        assert honest.decision == DECISION_INSUFFICIENT
        assert naive.design_effect == 1.0
        assert honest.design_effect is not None and honest.design_effect > 5.0
        # Both legs move, and the CI is the visible one.
        assert honest.ci_hi is not None and naive.ci_hi is not None
        assert honest.ci_lo is not None and naive.ci_lo is not None
        assert (honest.ci_hi - honest.ci_lo) > (naive.ci_hi - naive.ci_lo)

    def test_significance_leg_uses_n_eff_not_the_trade_count(self) -> None:
        """The larger of the two channels. A cell with a modest Sharpe over
        many correlated rows is significant on `n` and not on `n_eff`."""
        cell = _day_clustered(40, 10, mean=0.15, spread=0.30, seed=8)
        flat = AuditCell(
            label="flat", supp_r=cell.supp_r, cluster_key=list(range(len(cell.supp_r)))
        )
        [honest] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        [naive] = audit_guard.evaluate_audit_cells([flat], **_KW)  # type: ignore[arg-type]
        assert naive.adj_pvalue is not None and honest.adj_pvalue is not None
        assert naive.adj_pvalue < 0.05
        assert honest.adj_pvalue > naive.adj_pvalue

    def test_a_mismatched_key_fails_closed_and_says_so(self) -> None:
        bad = AuditCell(label="bad", supp_r=[0.5] * 40, cluster_key=[1, 2, 3])
        [v] = audit_guard.evaluate_audit_cells([bad], **_KW)  # type: ignore[arg-type]
        assert v.decision == DECISION_INSUFFICIENT
        assert v.ci_lo is None and v.adj_pvalue is None
        assert v.n_clusters is None and v.design_effect is None
        assert "cluster_key length 3 != n_supp 40" in v.reasons[0]

    def test_a_mismatched_key_leaves_the_holm_family(self) -> None:
        """A cell that could not be tested must not inflate the haircut
        denominator — the same rule `n < min_n` already obeys."""
        good = _normal_cell(-0.6, 0.7, 80, seed=1, label="good")
        bad = AuditCell(label="bad", supp_r=[0.5] * 40, cluster_key=[1])
        verdicts = audit_guard.evaluate_audit_cells([good, bad], **_KW)  # type: ignore[arg-type]
        assert [v.n_tests for v in verdicts] == [1, 1]

    def test_an_untested_cell_reports_no_design_effect(self) -> None:
        thin = AuditCell(label="thin", supp_r=[0.1] * 5, cluster_key=list(range(5)))
        [v] = audit_guard.evaluate_audit_cells([thin], min_n=30, **_KW)  # type: ignore[arg-type]
        assert v.decision == DECISION_INSUFFICIENT
        assert v.n_clusters is None and v.design_effect is None

    def test_already_daily_rows_are_not_deflated_twice(self) -> None:
        """AGENTS.md's book-day rule, enforced by arithmetic rather than memory.

        One row per day is a singleton cluster, so a consumer that has already
        aggregated to book-days gets a design effect of exactly 1.0 and the
        verdict it would have had before this change.
        """
        cell = AuditCell(
            label="daily",
            supp_r=np.random.default_rng(4).normal(-0.6, 0.7, 80).tolist(),
            cluster_key=[20_000 + d for d in range(80)],
        )
        [v] = audit_guard.evaluate_audit_cells([cell], **_KW)  # type: ignore[arg-type]
        assert v.design_effect == 1.0
        assert v.n_clusters == 80
        assert v.decision == DECISION_ENABLE

"""Tests for analytics/research_guards/sharpe.py — the shared Sharpe primitive.

This function was copied byte-identically into five sleeve report modules and
fed straight into DSR and the bootstrap statistic, i.e. two of the three legs
``passes_gate`` compares across sleeves. The tests that matter here are
therefore not "does it divide" but the two ways five copies could disagree:
the degenerate-input contract, and the annualisation convention that already
collides by name with ``analytics.xsmom.diagnostics._ann_sharpe``.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

from analytics.research_guards import ann_sharpe, per_period_sharpe
from analytics.xsmom.diagnostics import _ann_sharpe as _diagnostics_ann_sharpe


def _arr(*xs: float) -> npt.NDArray[np.float64]:
    return np.asarray(xs, dtype=np.float64)


class TestPerPeriodSharpe:
    def test_it_is_mean_over_sample_sd(self) -> None:
        r = _arr(0.01, 0.02, -0.01, 0.03)
        expected = float(np.mean(r) / np.std(r, ddof=1))
        assert per_period_sharpe(r) == expected

    def test_sample_sd_not_population_sd(self) -> None:
        # ddof=1. With ddof=0 the denominator shrinks and the Sharpe inflates,
        # which is exactly the kind of one-copy change that used to be able to
        # desynchronise the sleeves.
        r = _arr(0.01, 0.02, -0.01, 0.03)
        population = float(np.mean(r) / np.std(r, ddof=0))
        assert per_period_sharpe(r) != population
        assert per_period_sharpe(r) < population

    def test_fewer_than_two_observations_is_zero(self) -> None:
        assert per_period_sharpe(_arr()) == 0.0
        assert per_period_sharpe(_arr(0.05)) == 0.0

    def test_exactly_flat_series_is_zero_not_nan(self) -> None:
        # sd == 0 would divide by zero; the guard returns 0.0 so a dead
        # warm-up cannot propagate NaN into DSR.
        assert per_period_sharpe(_arr(0.01, 0.01, 0.01)) == 0.0

    def test_near_flat_series_is_NOT_zeroed(self) -> None:
        # THE distinction the 1e-12 floor makes, and it runs the opposite way
        # to what the name suggests: it rejects only a near-exactly-flat series,
        # so tightly clustered returns still yield a finite, enormous Sharpe.
        # Shaped after the bos/1d/long case in CLAUDE.md (36 trades around
        # -1.0076R at sd 0.0022) — but note that case belongs to
        # recalibrate_lib._sharpe, a different function on a different guard
        # (sd == 0.0) feeding star ratings. Pinned here because a future
        # dispersion floor for the SLEEVE path belongs in one place now.
        r = np.full(36, -1.0076, dtype=np.float64)
        r[::2] += 0.0022
        sharpe = per_period_sharpe(r)
        assert sharpe != 0.0
        assert abs(sharpe) > 100.0

    def test_sign_follows_the_mean(self) -> None:
        assert per_period_sharpe(_arr(-0.01, -0.02, -0.03)) < 0.0
        assert per_period_sharpe(_arr(0.01, 0.02, 0.03)) > 0.0


class TestAnnSharpe:
    def test_the_factor_is_applied_as_given(self) -> None:
        # ann_factor is ALREADY sqrt(periods) — the five report modules pass
        # math.sqrt(cfg.annualization_days).
        r = _arr(0.01, 0.02, -0.01, 0.03)
        ann = math.sqrt(252.0)
        assert ann_sharpe(r, ann) == per_period_sharpe(r) * ann

    def test_degenerate_inputs_stay_zero_after_annualising(self) -> None:
        assert ann_sharpe(_arr(0.01), math.sqrt(252.0)) == 0.0
        assert ann_sharpe(_arr(0.01, 0.01), math.sqrt(252.0)) == 0.0


class TestTheTwoConventionsAreDistinct:
    """``ann_sharpe`` and ``diagnostics._ann_sharpe`` differ by a sqrt.

    Same name, same shape, different meaning of the second argument. Swapping
    one for the other changes every number it touches by sqrt(annualization
    days) — about 15.9x at 252 — and nothing raises. This pins the relation so
    that a future "cleanup" unifying them has to fail a test first.
    """

    def test_diagnostics_roots_its_argument_and_the_shared_one_does_not(
        self,
    ) -> None:
        r = _arr(0.01, 0.02, -0.01, 0.03, 0.005)
        days = 252.0
        assert _diagnostics_ann_sharpe(r, days) == ann_sharpe(r, math.sqrt(days))

    def test_passing_raw_days_to_the_shared_one_is_wrong_by_sqrt(self) -> None:
        r = _arr(0.01, 0.02, -0.01, 0.03, 0.005)
        days = 252.0
        wrong = ann_sharpe(r, days)
        right = _diagnostics_ann_sharpe(r, days)
        assert not math.isclose(wrong, right)
        assert math.isclose(wrong / right, math.sqrt(days))


class TestNoSleeveKeepsItsOwnCopy:
    def test_all_five_report_modules_use_the_shared_primitive(self) -> None:
        # The duplication this module exists to end: five byte-identical
        # copies, no shared import. An `is` check, so re-defining a private
        # copy in any sleeve fails here rather than drifting silently.
        from analytics.carry import report as carry_report
        from analytics.combine import report as combine_report
        from analytics.cvd import report as cvd_report
        from analytics.forecast import report as forecast_report
        from analytics.xsmom import report as xsmom_report

        for module in (
            xsmom_report,
            carry_report,
            combine_report,
            forecast_report,
            cvd_report,
        ):
            assert module.per_period_sharpe is per_period_sharpe
            assert module.ann_sharpe is ann_sharpe
            assert not hasattr(module, "_per_period_sharpe")

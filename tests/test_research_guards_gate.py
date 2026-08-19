"""Tests for analytics/research_guards/gate.py — the published sleeve gate.

The point of a shared definition is that it cannot drift per sleeve, so these
tests assert the THREE legs positively *and* assert that the two reported
stamps are absent — a gate that quietly grew a fourth leg would still pass a
suite that only ever checked passing cases.
"""

from __future__ import annotations

import math

from analytics.carry.report import CarryReport, carry_gate_verdict
from analytics.combine.report import CombineReport, combine_gate_verdict
from analytics.cvd.report import CVDReport, cvd_gate_verdict
from analytics.research_guards import GATE_DSR, GATE_PBO, passes_gate
from analytics.xsmom.report import XSReport, xs_gate_verdict

PASS = {"dsr": 0.99, "pbo": 0.30, "boot_lo": 0.10}

# (dsr, pbo, boot_lo) — one clear case plus one failure per leg, and the two
# published boundaries. Shared so every sleeve is asked the same questions.
_GATE_CASES = (
    (0.99, 0.30, 0.10),
    (0.94, 0.30, 0.10),
    (0.99, 0.60, 0.10),
    (0.99, 0.30, -0.01),
    (0.95, 0.50, 1e-9),
)


class TestPassesGate:
    def test_all_three_legs_clear(self) -> None:
        assert passes_gate(**PASS) is True

    def test_each_leg_can_fail_alone(self) -> None:
        # One-at-a-time, so a leg that silently stopped being consulted shows
        # up here rather than in a sleeve verdict months later.
        assert passes_gate(**{**PASS, "dsr": 0.94}) is False
        assert passes_gate(**{**PASS, "pbo": 0.51}) is False
        assert passes_gate(**{**PASS, "boot_lo": 0.0}) is False

    def test_thresholds_are_inclusive_where_published(self) -> None:
        # "DSR >= 0.95" and "PBO <= 0.5" — exactly at the bound must PASS, and
        # boot_lo is strict (> 0), so exactly zero must FAIL.
        assert passes_gate(dsr=GATE_DSR, pbo=GATE_PBO, boot_lo=1e-9) is True
        assert passes_gate(dsr=GATE_DSR, pbo=GATE_PBO, boot_lo=0.0) is False

    def test_nan_pbo_fails_explicitly(self) -> None:
        # CSCV returns NaN when the trial matrix is too small to split.
        #
        # MEASURED, so nobody re-derives it: deleting the `math.isnan(pbo)`
        # guard from passes_gate leaves all 13 tests in this file GREEN. That
        # is an equivalent mutant, not a hole — `nan <= 0.5` is already False,
        # so the guard buys explicitness rather than behaviour. Do not "prove"
        # it is load-bearing by tightening this test; it isn't. The other four
        # mutations (dropping each leg, and boot_lo > 0 -> >= 0) all fail here.
        assert passes_gate(dsr=0.99, pbo=math.nan, boot_lo=0.10) is False

    def test_nan_in_any_other_leg_also_fails(self) -> None:
        assert passes_gate(dsr=math.nan, pbo=0.30, boot_lo=0.10) is False
        assert passes_gate(dsr=0.99, pbo=0.30, boot_lo=math.nan) is False

    def test_published_thresholds_are_the_published_numbers(self) -> None:
        assert GATE_DSR == 0.95
        assert GATE_PBO == 0.5


def _xs(**kw: float) -> XSReport:
    base: dict[str, float] = {
        "sharpe_annual": 1.375,
        "sortino_annual": 2.0,
        "max_dd": -0.2,
        "calmar": 1.0,
        "annual_return": 0.3,
        "annual_vol": 0.2,
        "dsr": 0.997,
        "pbo": 0.295,
        "boot_lo": 0.15,
        "boot_hi": 0.9,
        "min_trl": 7035.0,
        "corr_to_trend": 0.37,
        "trend_sharpe": 0.36,
    }
    base.update(kw)
    return XSReport(n_obs=2475, **base)


class TestXsGateVerdict:
    def test_the_deploy_core_clears_its_own_gate(self) -> None:
        # The published numbers: +1.375 Sharpe, DSR 0.997, PBO 0.295.
        assert xs_gate_verdict(_xs()) is True

    def test_corr_to_trend_is_a_stamp_not_a_leg(self) -> None:
        # THE regression this guards. P3 spec criterion 4 originally read
        # "corr_to_trend near zero"; the sleeve cleared at +0.37 and the
        # criterion was amended to a human-read disqualifier. Coding it as a
        # pass condition would fail the one sleeve that carries capital.
        assert xs_gate_verdict(_xs(corr_to_trend=0.99)) is True
        assert xs_gate_verdict(_xs(corr_to_trend=-0.99)) is True

    def test_min_trl_is_a_stamp_not_a_leg(self) -> None:
        # The four-leg form is a documented recurring misquote. The core needs
        # ~7035 obs to confirm Sharpe > 1.0 and has ~2475, so a MinTRL leg
        # would fail it.
        assert xs_gate_verdict(_xs(min_trl=1e9)) is True

    def test_sharpe_alone_cannot_rescue_a_failed_leg(self) -> None:
        assert xs_gate_verdict(_xs(sharpe_annual=99.0, pbo=0.9)) is False

    def test_each_leg_fails_the_verdict(self) -> None:
        assert xs_gate_verdict(_xs(dsr=0.94)) is False
        assert xs_gate_verdict(_xs(pbo=0.51)) is False
        assert xs_gate_verdict(_xs(boot_lo=-0.01)) is False
        assert xs_gate_verdict(_xs(pbo=math.nan)) is False


def _combine(**kw: float) -> CombineReport:
    base: dict[str, float] = {
        "sharpe_annual": 1.145,
        "sortino_annual": 1.6,
        "max_dd": -0.25,
        "calmar": 0.8,
        "annual_return": 0.25,
        "annual_vol": 0.2,
        "dsr": 0.98,
        "pbo": 0.35,
        "boot_lo": 0.08,
        "boot_hi": 0.8,
        "min_trl": 5000.0,
        "corr_xs_trend": 0.37,
        "realized_idm": 1.2,
        "vol_xs": 0.2,
        "vol_trend": 0.2,
        "vol_combined": 0.2,
        "diversification_mult": 1.2,
        "sharpe_xs": 1.375,
        "sharpe_trend": 0.36,
        "xs_contribution": 0.1,
        "trend_contribution": 0.05,
    }
    base.update(kw)
    return CombineReport(n_obs=2475, **base)


class TestCombineStillAgrees:
    def test_combine_verdict_is_unchanged_by_the_refactor(self) -> None:
        # combine_gate_verdict became a thin adapter over passes_gate; these
        # pin that the behaviour it had before is the behaviour it has now.
        assert combine_gate_verdict(_combine()) is True
        assert combine_gate_verdict(_combine(dsr=0.94)) is False
        assert combine_gate_verdict(_combine(pbo=0.51)) is False
        assert combine_gate_verdict(_combine(boot_lo=0.0)) is False
        assert combine_gate_verdict(_combine(pbo=math.nan)) is False

    def test_both_sleeves_agree_on_identical_gate_inputs(self) -> None:
        # The whole reason for one shared definition. If these ever diverge,
        # a sleeve has grown a private threshold again.
        for dsr, pbo, boot_lo in _GATE_CASES:
            assert xs_gate_verdict(_xs(dsr=dsr, pbo=pbo, boot_lo=boot_lo)) is (
                combine_gate_verdict(_combine(dsr=dsr, pbo=pbo, boot_lo=boot_lo))
            )


def _carry(**kw: float) -> CarryReport:
    base: dict[str, float] = {
        "sharpe_annual": 0.03,
        "sortino_annual": 0.05,
        "max_dd": -0.3,
        "calmar": 0.1,
        "annual_return": 0.01,
        "annual_vol": 0.2,
        "dsr": 0.20,
        "pbo": 0.70,
        "boot_lo": -0.20,
        "boot_hi": 0.30,
        "min_trl": 1e6,
        "corr_to_xs": 0.05,
        "xs_sharpe": 1.375,
        "corr_to_trend": 0.10,
        "trend_sharpe": 0.36,
    }
    base.update(kw)
    return CarryReport(n_obs=2475, **base)


def _cvd(**kw: float) -> CVDReport:
    base: dict[str, float] = {
        "sharpe_annual": -0.147,
        "max_dd": -0.4,
        "annual_return": -0.05,
        "annual_vol": 0.2,
        "dsr": 0.532,
        "pbo": 0.849,
        "boot_lo": -0.642,
        "boot_hi": 0.10,
        "min_trl": 1e6,
        "corr_to_xsmom": -0.04,
        "xsmom_sharpe": 1.375,
    }
    base.update(kw)
    return CVDReport(n_obs=2475, inverted=False, **base)


class TestEverySleeveDelegates:
    """All four coded verdicts, against one shared truth.

    ``carry_gate_verdict`` restated ``0.95`` / ``0.5`` inline until 2026-08-19
    while its three siblings delegated. The two forms agreed on every input, so
    no test could have failed — which is the point: this class pins agreement
    by CONSTRUCTION, so the next move of ``GATE_DSR`` / ``GATE_PBO`` cannot
    leave one sleeve behind quietly.
    """

    def test_all_four_sleeves_agree_with_passes_gate(self) -> None:
        for dsr, pbo, boot_lo in _GATE_CASES:
            want = passes_gate(dsr=dsr, pbo=pbo, boot_lo=boot_lo)
            assert xs_gate_verdict(_xs(dsr=dsr, pbo=pbo, boot_lo=boot_lo)) is want
            assert (
                combine_gate_verdict(_combine(dsr=dsr, pbo=pbo, boot_lo=boot_lo))
                is want
            )
            assert carry_gate_verdict(_carry(dsr=dsr, pbo=pbo, boot_lo=boot_lo)) is want
            assert cvd_gate_verdict(_cvd(dsr=dsr, pbo=pbo, boot_lo=boot_lo)) is want

    def test_carry_verdict_is_unchanged_by_the_delegation(self) -> None:
        # The shelved verdict itself: +0.03 Sharpe, fails all three legs.
        assert carry_gate_verdict(_carry()) is False
        assert carry_gate_verdict(_carry(dsr=0.99, pbo=0.30, boot_lo=0.10)) is True
        assert carry_gate_verdict(_carry(dsr=0.99, pbo=0.30, boot_lo=0.0)) is False
        assert carry_gate_verdict(_carry(dsr=0.99, pbo=math.nan, boot_lo=0.10)) is False

    def test_the_shelved_sleeves_still_read_shelved(self) -> None:
        # cvd: DSR 0.532, PBO 0.849, boot_lo -0.642 — three failing legs.
        assert cvd_gate_verdict(_cvd()) is False
        assert carry_gate_verdict(_carry()) is False

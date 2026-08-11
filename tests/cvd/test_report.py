import numpy as np
import pytest

from analytics.cvd.report import CVDReport, cvd_gate_verdict, evaluate_cvd
from analytics.forecast.config import ForecastConfig


def _returns(mean: float, n: int = 800, seed: int = 2) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(mean, 0.01, n)


def _trials(mean: float) -> dict[str, np.ndarray]:
    return {f"span{s}": _returns(mean, seed=s) for s in (8, 16, 32, 64)} | {
        "combined": _returns(mean, seed=99)
    }


def test_report_carries_n_obs_and_the_correlation_stamp() -> None:
    r = _returns(0.001)
    rep = evaluate_cvd(r, ForecastConfig(), _trials(0.001), xsmom_returns=r)
    assert rep.n_obs == len(r)
    assert rep.corr_to_xsmom == pytest.approx(1.0)


def test_corr_to_xsmom_is_near_zero_for_independent_books() -> None:
    rep = evaluate_cvd(
        _returns(0.001, seed=1),
        ForecastConfig(),
        _trials(0.001),
        xsmom_returns=_returns(0.001, seed=77),
    )
    assert abs(rep.corr_to_xsmom) < 0.2


def test_corr_to_xsmom_handles_a_length_mismatch_by_aligning_tails() -> None:
    rep = evaluate_cvd(
        _returns(0.001, n=800),
        ForecastConfig(),
        _trials(0.001),
        xsmom_returns=_returns(0.001, n=500, seed=1),
    )
    assert np.isfinite(rep.corr_to_xsmom)


def test_gate_delegates_and_never_restates_thresholds() -> None:
    passing = CVDReport(
        sharpe_annual=1.5,
        max_dd=-0.1,
        annual_return=0.3,
        annual_vol=0.2,
        n_obs=800,
        dsr=0.99,
        pbo=0.2,
        boot_lo=0.4,
        boot_hi=2.0,
        min_trl=100.0,
        corr_to_xsmom=0.1,
        xsmom_sharpe=1.3,
        folded_to_magnitude=False,
    )
    assert cvd_gate_verdict(passing) is True
    assert cvd_gate_verdict(CVDReport(**{**passing.__dict__, "boot_lo": -0.1})) is False
    assert cvd_gate_verdict(CVDReport(**{**passing.__dict__, "pbo": 0.9})) is False
    assert cvd_gate_verdict(CVDReport(**{**passing.__dict__, "dsr": 0.5})) is False


def test_nan_pbo_fails_the_gate() -> None:
    """A single-config run yields NaN PBO, which must not read as a pass."""
    rep = CVDReport(
        sharpe_annual=1.5,
        max_dd=-0.1,
        annual_return=0.3,
        annual_vol=0.2,
        n_obs=800,
        dsr=0.99,
        pbo=float("nan"),
        boot_lo=0.4,
        boot_hi=2.0,
        min_trl=100.0,
        corr_to_xsmom=0.1,
        xsmom_sharpe=1.3,
        folded_to_magnitude=False,
    )
    assert cvd_gate_verdict(rep) is False


def test_negative_sharpe_collapses_dsr_unless_folded() -> None:
    """The H8/H14 defect: a reliably-negative book scores DSR ~0, making a
    negative verdict structurally unreachable. Folding to magnitude is the fix."""
    r = _returns(-0.001)
    trials = _trials(-0.001)
    signed = evaluate_cvd(r, ForecastConfig(), trials, xsmom_returns=r)
    folded = evaluate_cvd(
        r, ForecastConfig(), trials, xsmom_returns=r, fold_to_magnitude=True
    )
    assert signed.dsr < 0.5
    assert folded.dsr > signed.dsr
    assert folded.folded_to_magnitude is True
    assert signed.folded_to_magnitude is False

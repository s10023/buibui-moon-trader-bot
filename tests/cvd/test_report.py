import numpy as np
import pytest

from analytics.cvd.report import CVDReport, cvd_gate_verdict, evaluate_cvd
from analytics.forecast.config import ForecastConfig
from analytics.research_guards import deflated_sharpe_ratio


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


def _sharpe(r: np.ndarray) -> float:
    """Independent re-implementation of the per-period Sharpe `evaluate_cvd`
    uses internally, so the expected value below is computed from first
    principles rather than by importing the module's private helper."""
    sd = float(np.std(r, ddof=1))
    return float(np.mean(r)) / sd if sd > 1e-12 else 0.0


def test_folding_moves_trial_variance_not_just_the_target() -> None:
    """`_trials(-0.001)` (used above) draws every span from the SAME negative
    mean, so all trial Sharpes share one sign. `abs()` on a uniformly-signed
    list is a pure sign flip, and Var(-X) == Var(X) — so that trial family
    cannot distinguish "folded both" from "folded target only". A half-fix
    (fold sr_d, leave trial_srs signed) sails through the test above with a
    bit-identical DSR either way.

    This test uses a MIXED-sign, asymmetric-magnitude trial family instead, so
    `sr_variance` genuinely changes under `abs()`. Rather than an inequality,
    it pins the exact DSR a fully-folded implementation must produce: the
    expected value is computed independently by calling
    `deflated_sharpe_ratio` directly with the folded target and folded trial
    Sharpes. A half-fix would feed unfolded trial Sharpes into that same call
    and land on a different `sr_variance` (hence a different expected-max-Sharpe
    benchmark, hence a different DSR), so the equality fails.
    """
    r = _returns(-0.001, seed=2)
    mixed_trials = {
        "span8": _returns(-0.003, seed=8),
        "span16": _returns(0.0015, seed=16),
        "span32": _returns(-0.0006, seed=32),
        "span64": _returns(0.002, seed=64),
        "combined": _returns(-0.001, seed=99),
    }

    rep = evaluate_cvd(
        r, ForecastConfig(), mixed_trials, xsmom_returns=r, fold_to_magnitude=True
    )

    folded_target_sr = abs(_sharpe(r))
    folded_trial_srs = [abs(_sharpe(np.asarray(v))) for v in mixed_trials.values()]
    expected_dsr = deflated_sharpe_ratio(
        folded_target_sr, len(r), trial_srs=folded_trial_srs
    )

    assert rep.dsr == pytest.approx(expected_dsr)

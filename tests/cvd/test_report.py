import numpy as np
import pandas as pd
import pytest

from analytics.cvd.report import CVDReport, cvd_gate_verdict, evaluate_cvd
from analytics.forecast.config import ForecastConfig
from analytics.research_guards import cscv_pbo


def _returns(mean: float, n: int = 800, seed: int = 2) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(mean, 0.01, n)


def _trials(mean: float) -> dict[str, np.ndarray]:
    return {f"span{s}": _returns(mean, seed=s) for s in (8, 16, 32, 64)} | {
        "combined": _returns(mean, seed=99)
    }


def _idx(n: int, start: str = "2024-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="D", tz="UTC")


def test_report_carries_n_obs_and_the_correlation_stamp() -> None:
    r = _returns(0.001)
    idx = _idx(len(r))
    rep = evaluate_cvd(
        r,
        ForecastConfig(),
        _trials(0.001),
        xsmom_returns=r,
        portfolio_index=idx,
        xsmom_index=idx,
    )
    assert rep.n_obs == len(r)
    assert rep.corr_to_xsmom == pytest.approx(1.0)


def test_corr_to_xsmom_is_near_zero_for_independent_books() -> None:
    a = _returns(0.001, seed=1)
    b = _returns(0.001, seed=77)
    idx = _idx(len(a))
    rep = evaluate_cvd(
        a,
        ForecastConfig(),
        _trials(0.001),
        xsmom_returns=b,
        portfolio_index=idx,
        xsmom_index=idx,
    )
    assert abs(rep.corr_to_xsmom) < 0.2


def test_corr_to_xsmom_aligns_by_date_not_position() -> None:
    """FINDING 1: the CVD book's index is always a SUBSET of the benchmark's
    (the forecast matrix's index is spot ∩ perp dates; the benchmark's is the
    closes' union). Whenever the spot leg's history ends earlier than the
    perp leg's, the two return arrays are correlated on the SAME calendar
    days but offset by a few array positions — a positional `[-n:]` tail
    slice compares day *d* of one book against day *d+lag* of the other and
    manufactures a decorrelation reading exactly where the study is decisive.

    Two series share one common factor on the SAME days; the book is missing
    the LAST `lag` days the benchmark has (mirroring a stale spot backfill),
    so the book's array is a strict prefix of what would align with the
    benchmark's array position-for-position. `corr_to_xsmom` must recover the
    true overlap correlation, not the position-shifted artefact.
    """
    rng = np.random.default_rng(3)
    n_full = 800
    common = rng.normal(0.0, 0.01, n_full)
    noise_a = rng.normal(0.0, 0.006, n_full)
    noise_b = rng.normal(0.0, 0.006, n_full)
    xsmom_full = 0.8 * common + noise_a
    book_full = 0.8 * common + noise_b

    lag = 5
    n_book = n_full - lag
    idx_full = _idx(n_full)
    idx_book = idx_full[:n_book]  # book ends `lag` days before the benchmark

    rep = evaluate_cvd(
        book_full[:n_book],
        ForecastConfig(),
        _trials(0.001),
        xsmom_returns=xsmom_full,
        portfolio_index=idx_book,
        xsmom_index=idx_full,
    )

    # Independently computed, date-correct correlation on the true overlap.
    expected = float(np.corrcoef(xsmom_full[:n_book], book_full[:n_book])[0, 1])
    assert rep.corr_to_xsmom == pytest.approx(expected, abs=1e-9)

    # The positional artefact the OLD `[-n:]` implementation would have
    # produced instead: book_full[:n_book] against xsmom_full's own tail
    # (xsmom_full[-n_book:] == xsmom_full[lag:]) — a different `lag`-day
    # calendar window. It must NOT be what the stamp reports.
    positional_artefact = float(
        np.corrcoef(book_full[:n_book], xsmom_full[-n_book:])[0, 1]
    )
    assert rep.corr_to_xsmom != pytest.approx(positional_artefact, abs=1e-2)


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
        inverted=False,
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
        inverted=False,
    )
    assert cvd_gate_verdict(rep) is False


# A "reliably negative" book: consistently negative-mean trial family, tuned
# so the SIGNED read fails every leg and the INVERTED read clears the gate —
# reused by both invert tests below.
_NEG_BOOK_MEAN = -0.004
_NEG_TRIAL_SPAN_MEANS = (-0.005, -0.003, -0.0035, -0.0045)
_NEG_TRIAL_COMBINED_MEAN = -0.004


def _neg_book_and_trials() -> tuple[np.ndarray, dict[str, np.ndarray]]:
    r = _returns(_NEG_BOOK_MEAN, seed=2)
    trials = {
        f"span{s}": _returns(m, seed=s)
        for s, m in zip((8, 16, 32, 64), _NEG_TRIAL_SPAN_MEANS, strict=True)
    } | {"combined": _returns(_NEG_TRIAL_COMBINED_MEAN, seed=99)}
    return r, trials


def test_invert_makes_a_negative_book_gate_reachable() -> None:
    """FINDING 4: the predecessor `abs()`-on-Sharpes-only fold left `boot_lo`
    computed from the RAW, unfolded returns — DSR could cross 0.95 while
    `boot_lo` stayed negative, so a genuinely mirrored (sign-inverted) book
    could never clear `cvd_gate_verdict`, no matter how strong the inverted
    effect. `invert=True` negates the portfolio AND every trial's returns
    before any metric is derived, so DSR, PBO, and `boot_lo` move together.

    This is the exact assertion the old implementation fails: with it, the
    signed read below still fails the gate, but so does the inverted read,
    because `boot_lo` never leaves negative territory.
    """
    r, trials = _neg_book_and_trials()
    idx = _idx(len(r))

    signed = evaluate_cvd(
        r,
        ForecastConfig(),
        trials,
        xsmom_returns=r,
        portfolio_index=idx,
        xsmom_index=idx,
    )
    inverted = evaluate_cvd(
        r,
        ForecastConfig(),
        trials,
        xsmom_returns=r,
        portfolio_index=idx,
        xsmom_index=idx,
        invert=True,
    )

    assert signed.inverted is False
    assert inverted.inverted is True

    assert signed.boot_lo < 0.0
    assert cvd_gate_verdict(signed) is False

    assert inverted.boot_lo > 0.0
    assert inverted.dsr > signed.dsr
    assert cvd_gate_verdict(inverted) is True


def test_invert_negates_the_trial_family_not_just_the_portfolio() -> None:
    """PBO is computed purely from `trial_returns` — it never touches
    `portfolio_return`. So a half-fix that negates only the portfolio and
    leaves the trial family signed would still report the SIGNED family's
    overfitting probability under `invert=True` (a DSR-only check would miss
    this: `expected_max_sharpe` depends on `variance(trial_srs)`, which is
    invariant under a uniform sign flip, so DSR alone cannot distinguish a
    full negation from a portfolio-only one).

    Pin the exact PBO a full negation must produce, computed independently
    via `cscv_pbo` on the negated trial matrix, and confirm it differs from
    the signed family's PBO (otherwise this pin would be vacuous).
    """
    r, trials = _neg_book_and_trials()
    idx = _idx(len(r))

    signed = evaluate_cvd(
        r,
        ForecastConfig(),
        trials,
        xsmom_returns=r,
        portfolio_index=idx,
        xsmom_index=idx,
    )
    inverted = evaluate_cvd(
        r,
        ForecastConfig(),
        trials,
        xsmom_returns=r,
        portfolio_index=idx,
        xsmom_index=idx,
        invert=True,
    )

    negated_mat = np.column_stack([-np.asarray(v) for v in trials.values()])
    expected_pbo = cscv_pbo(negated_mat).pbo

    assert inverted.pbo == pytest.approx(expected_pbo)
    assert inverted.pbo != pytest.approx(signed.pbo)

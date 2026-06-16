"""Tests for analytics.forecast.report — G2Report + evaluate."""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.book import ForecastBookResult
from analytics.forecast.config import ForecastConfig
from analytics.forecast.report import G2Report, evaluate


def _result(returns: np.ndarray) -> ForecastBookResult:
    idx = pd.date_range("2021-01-01", periods=len(returns), freq="D")
    return ForecastBookResult(
        daily_index=idx,
        portfolio_return=returns,
        pre_governor_return=returns,
        governor=np.ones(len(returns)),
        active_count=np.full(len(returns), 1, dtype=np.int64),
        per_instrument_net={"AAA": pd.Series(returns, index=idx)},
    )


def test_positive_drift_gives_positive_sharpe_and_report_shape() -> None:
    rng = np.random.default_rng(0)
    r = 0.001 + 0.01 * rng.standard_normal(800)  # positive-drift noise
    res = _result(r)
    trials = {"combined": r, "s64_256": r * 0.9}
    rep = evaluate(res, ForecastConfig(), trial_returns=trials)
    assert isinstance(rep, G2Report)
    assert rep.sharpe_annual > 0.0
    assert rep.n_obs == 800
    assert rep.boot_lo <= rep.sharpe_annual <= rep.boot_hi
    assert 0.0 <= rep.pbo <= 1.0
    assert rep.min_trl > 0.0


def test_flat_returns_degenerate_to_zero() -> None:
    res = _result(np.zeros(500))
    rep = evaluate(res, ForecastConfig(), trial_returns={"combined": np.zeros(500)})
    assert rep.sharpe_annual == 0.0


def test_pbo_returns_none_equals_explicit_trial_returns() -> None:
    rng = np.random.default_rng(2)
    r = 0.001 + 0.01 * rng.standard_normal(800)
    res = _result(r)
    fam = {"combined": r, "s8_32": r * 1.1, "s64_256": r * 0.2}
    a = evaluate(res, ForecastConfig(), trial_returns=fam)
    b = evaluate(res, ForecastConfig(), trial_returns=fam, pbo_returns=fam)
    assert (a.pbo == b.pbo) or (np.isnan(a.pbo) and np.isnan(b.pbo))


def test_pbo_returns_overrides_pbo_family_keeps_dsr() -> None:
    rng = np.random.default_rng(3)
    r = 0.001 + 0.01 * rng.standard_normal(800)
    res = _result(r)
    dsr_family = {"combined": r, "s8_32": r * 1.1, "s64_256": r * 0.2}
    pbo_family = {"a": r, "b": r * 0.95, "c": -r, "d": r * 0.3}
    base = evaluate(res, ForecastConfig(), trial_returns=dsr_family)
    split = evaluate(
        res, ForecastConfig(), trial_returns=dsr_family, pbo_returns=pbo_family
    )
    # DSR is deflated against trial_returns only -> unchanged by pbo_returns
    assert split.dsr == base.dsr
    assert 0.0 <= split.pbo <= 1.0

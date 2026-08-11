from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd

from analytics.forecast.book import instrument_returns
from analytics.forecast.config import ForecastConfig
from analytics.forecast.ewmac import combine_forecasts


def _trend_close(n: int = 500) -> pd.Series:
    idx = pd.date_range("2021-01-01", periods=n, freq="D")
    return pd.Series(np.linspace(100.0, 400.0, n), index=idx)


def test_uptrend_yields_positive_net_no_funding() -> None:
    close = _trend_close()
    funding = pd.Series(0.0, index=close.index)
    out = instrument_returns(close, funding, ForecastConfig())
    # a clean uptrend held long should net positive over the path
    assert out["net"].sum() > 0.0
    # leverage should be long (positive) once warmed up
    assert out["leverage"].dropna().iloc[-1] > 0.0


def test_position_is_causal_no_lookahead() -> None:
    close = _trend_close()
    funding = pd.Series(0.0, index=close.index)
    base = instrument_returns(close, funding, ForecastConfig())

    # Perturb a MIDDLE bar: leverage at index k is sized from info ≤ k-1, so
    # close[k] must not affect leverage[:k+1].
    k = len(close) // 2
    bumped = close.copy()
    bumped.iloc[k] *= 1.5
    after = instrument_returns(bumped, funding, ForecastConfig())

    pd.testing.assert_series_equal(
        base["leverage"].iloc[: k + 1],
        after["leverage"].iloc[: k + 1],
        check_names=False,
    )

    # Positive control — the assertion above is a "did NOT change" claim, which
    # is equally satisfied by "the invariant holds" and by "the bump never
    # reached the sizing". The latter is how the xsmom causality guard was
    # vacuous (see tests/xsmom/test_book.py), and this fixture is exactly the
    # shape that invites it: `_trend_close` is a PERFECTLY LINEAR ramp, whose
    # near-zero realised vol drives leverage to ~187x. That was measured, not
    # assumed — the bump injects vol and collapses leverage to ~0.17 at k+1
    # (delta ~1.9e+02), so the stimulus is live and nothing is cap-saturated.
    # NaN would satisfy a bare `!=`, so require finite first.
    lev_base = float(base["leverage"].iloc[k + 1])
    lev_after = float(after["leverage"].iloc[k + 1])
    assert np.isfinite(lev_base) and np.isfinite(lev_after), (
        "leverage[k+1] must be warmed up for the control to mean anything"
    )
    assert abs(lev_after - lev_base) > 1e-9, (
        "perturbation never propagated to leverage[k+1] — the causality "
        "assertion above is vacuous and would pass with the causal shift removed"
    )


def test_funding_sign_long_pays_short_receives() -> None:
    close = _trend_close()
    up_fund = pd.Series(0.001, index=close.index)  # positive funding
    out_long = instrument_returns(close, up_fund, ForecastConfig())
    # long in an uptrend with positive funding -> positive funding COST
    assert out_long["funding_cost"].dropna().iloc[-1] > 0.0

    down = pd.Series(np.linspace(400.0, 100.0, len(close)), index=close.index)
    out_short = instrument_returns(down, up_fund, ForecastConfig())
    # short (downtrend) with positive funding -> negative cost (a credit)
    assert out_short["funding_cost"].dropna().iloc[-1] < 0.0


def test_turnover_cost_nonnegative_and_charged_on_change() -> None:
    close = _trend_close()
    funding = pd.Series(0.0, index=close.index)
    out = instrument_returns(close, funding, ForecastConfig())
    assert (out["turnover_cost"].dropna() >= 0.0).all()
    assert out["turnover_cost"].dropna().sum() > 0.0  # leverage ramps -> some cost


def test_weighted_combine_has_no_lookahead() -> None:
    close = pd.Series(np.linspace(100.0, 160.0, 400))
    funding = pd.Series(0.0, index=close.index)
    cfg = dataclasses.replace(ForecastConfig(), weights=(0.6, 0.3, 0.1, 0.0))

    base = instrument_returns(close, funding, cfg)
    bumped = close.copy()
    bumped.iloc[300] *= 1.5
    pert = instrument_returns(bumped, pd.Series(0.0, index=close.index), cfg)

    # leverage on day d uses forecast through d-1; perturbing close[300] can only
    # move leverage from day 301 onward. Days 0..299 must be byte-identical.
    pd.testing.assert_series_equal(
        base["leverage"].iloc[:300],
        pert["leverage"].iloc[:300],
        check_names=False,
    )


def test_injected_forecast_matching_internal_is_byte_identical() -> None:
    """None and an explicitly-passed identical forecast must agree exactly."""
    idx = pd.date_range("2020-01-01", periods=400, freq="D", tz="UTC")
    rng = np.random.default_rng(5)
    close = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0, 0.02, 400))), index=idx)
    funding = pd.Series(0.0, index=idx)
    cfg = ForecastConfig()

    internal = combine_forecasts(
        close, cfg.speeds, cfg.fdm, cfg.vol_span, cfg.cap, weights=cfg.weights
    )
    baseline = instrument_returns(close, funding, cfg)
    injected = instrument_returns(close, funding, cfg, forecast=internal)
    pd.testing.assert_frame_equal(baseline, injected)


def test_injected_forecast_is_shifted_by_the_book_not_the_caller() -> None:
    """A constant forecast must produce leverage from day 1 of vol warm-up,
    shifted one day — proving the book applies the shift to injected series
    exactly as it does to internal ones."""
    idx = pd.date_range("2020-01-01", periods=400, freq="D", tz="UTC")
    rng = np.random.default_rng(6)
    close = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0, 0.02, 400))), index=idx)
    funding = pd.Series(0.0, index=idx)
    cfg = ForecastConfig()

    spiky = pd.Series(0.0, index=idx)
    spiky.iloc[200] = 10.0
    out = instrument_returns(close, funding, cfg, forecast=spiky)
    assert out["leverage"].iloc[200] == 0.0
    assert out["leverage"].iloc[201] != 0.0

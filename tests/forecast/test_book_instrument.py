from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.book import instrument_returns
from analytics.forecast.config import ForecastConfig


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

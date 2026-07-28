from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.xsmom.book import XSBookResult, xs_leverage
from analytics.xsrev.book import run_xsrev_backtest
from analytics.xsrev.config import ReversalConfig
from analytics.xsrev.forecast import reversal_forecast_matrix


def _closes() -> dict[str, pd.Series]:
    idx = pd.date_range("2021-01-01", periods=400, freq="D", tz="UTC")
    return {
        "STRONG": pd.Series(np.linspace(100.0, 400.0, 400), index=idx),
        "WEAK": pd.Series(np.linspace(400.0, 100.0, 400), index=idx),
    }


def _fundings(closes: dict[str, pd.Series]) -> dict[str, pd.Series]:
    return {s: pd.Series(0.0, index=c.index) for s, c in closes.items()}


def test_reversal_leverage_is_inverse_of_momentum() -> None:
    # Reversal SHORTS the recent winner and LONGS the recent loser — the exact
    # inverse of XS momentum's sign on the same monotone cross-section.
    closes = _closes()
    cfg = ReversalConfig()
    lev = xs_leverage(
        closes, cfg.sleeve_cfg, forecasts=reversal_forecast_matrix(closes, cfg)
    )
    last = lev.iloc[-1]
    assert last["STRONG"] < 0.0  # winner held short
    assert last["WEAK"] > 0.0  # loser held long


def test_run_xsrev_backtest_shape_and_finite() -> None:
    closes = _closes()
    res = run_xsrev_backtest(closes, _fundings(closes), ReversalConfig())
    assert isinstance(res, XSBookResult)
    assert res.portfolio_return.shape[0] == 400
    assert not np.isnan(res.portfolio_return).any()
    assert res.active_count.max() == 2


def test_run_xsrev_backtest_is_causal_no_lookahead() -> None:
    closes = _closes()
    cfg = ReversalConfig()
    base = xs_leverage(
        closes, cfg.sleeve_cfg, forecasts=reversal_forecast_matrix(closes, cfg)
    )
    k = 250
    bumped = {s: c.copy() for s, c in closes.items()}
    bumped["STRONG"].iloc[k] *= 1.5
    after = xs_leverage(
        bumped, cfg.sleeve_cfg, forecasts=reversal_forecast_matrix(bumped, cfg)
    )
    pd.testing.assert_frame_equal(
        base.iloc[: k + 1], after.iloc[: k + 1], check_names=False
    )

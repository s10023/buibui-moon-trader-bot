from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.xsmom.execution import ExecutionCostConfig, dollar_adv


def _idx(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")


def test_execution_cost_config_defaults() -> None:
    cfg = ExecutionCostConfig()
    assert cfg.capital == 1_000_000.0
    assert cfg.impact == "sqrt"
    assert cfg.adv_window == 30
    # tiers: major tightest, alt widest
    assert cfg.major_bps < cfg.mid_bps < cfg.alt_bps


def test_dollar_adv_trailing_median_and_causal_shift() -> None:
    # dollar volume = 10 for first 5 days, then 100 thereafter.
    idx = _idx(10)
    dv = pd.Series([10.0] * 5 + [100.0] * 5, index=idx)
    adv = dollar_adv({"X": dv}, window=3)["X"]
    # window=3 median, then shift(1): row d uses days d-3..d-1.
    # First 3 rows are NaN (no full prior window after shift).
    assert adv.iloc[:3].isna().all()
    # Day index 3 uses days 0,1,2 -> median(10,10,10) = 10.
    assert adv.iloc[3] == 10.0
    # Day index 4 uses days 1,2,3 -> 10.
    assert adv.iloc[4] == 10.0


def test_dollar_adv_is_causal_to_same_day_volume() -> None:
    idx = _idx(10)
    dv = pd.Series(np.linspace(1.0, 10.0, 10), index=idx)
    base = dollar_adv({"X": dv}, window=3)["X"]
    bumped = dv.copy()
    bumped.iloc[5] *= 100.0  # perturb day 5
    out = dollar_adv({"X": bumped}, window=3)["X"]
    # Day 5's own ADV must be unchanged (uses days 2,3,4 via shift) — the
    # load-bearing causal invariant: ADV[d] never depends on volume[d].
    assert out.iloc[5] == base.iloc[5]
    # The bump is not a no-op: it surfaces downstream (median is robust to a
    # single outlier, so the first changed value lands at day 7, not day 6).
    assert not np.array_equal(out.iloc[6:].to_numpy(), base.iloc[6:].to_numpy())

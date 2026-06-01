"""Tests for the IS/OOS time-split helper in tools/htf_ema_gate_replay.py."""

from __future__ import annotations

import pandas as pd

from tools.htf_ema_gate_replay import split_is_oos


def _df(times: list[int]) -> pd.DataFrame:
    return pd.DataFrame({"entry_time": times, "pnl_r": [0.0] * len(times)})


def test_split_is_oos_partitions_by_time_quantile() -> None:
    df = _df([10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
    is_df, oos_df = split_is_oos(df, oos_frac=0.3)
    # Latest 30% by entry_time go to OOS.
    assert sorted(oos_df["entry_time"]) == [80, 90, 100]
    assert sorted(is_df["entry_time"]) == [10, 20, 30, 40, 50, 60, 70]


def test_split_is_oos_zero_frac_returns_all_in_sample() -> None:
    df = _df([1, 2, 3])
    is_df, oos_df = split_is_oos(df, oos_frac=0.0)
    assert len(is_df) == 3
    assert oos_df.empty

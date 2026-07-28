from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.xsrev.config import ReversalConfig
from analytics.xsrev.forecast import (
    combine_reversal_forecasts,
    reversal_forecast_matrix,
    scaled_reversal_forecast,
)


def _idx(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")


def test_reversal_sign_short_recent_winner() -> None:
    # A steadily rising price is a recent WINNER -> reversal forecast is negative.
    close = pd.Series(np.linspace(100.0, 200.0, 80), index=_idx(80))
    f = scaled_reversal_forecast(close, window=3, scalar=10.0, vol_span=32, cap=20.0)
    assert f.dropna().iloc[-1] < 0.0
    # A falling price is a recent LOSER -> reversal forecast is positive.
    down = pd.Series(np.linspace(200.0, 100.0, 80), index=_idx(80))
    g = scaled_reversal_forecast(down, window=3, scalar=10.0, vol_span=32, cap=20.0)
    assert g.dropna().iloc[-1] > 0.0


def test_reversal_forecast_is_causal() -> None:
    # Bumping a LATER close must not change forecast values strictly before it.
    close = pd.Series(np.linspace(100.0, 150.0, 80), index=_idx(80))
    bumped = close.copy()
    k = 60
    bumped.iloc[k] *= 1.3
    a = scaled_reversal_forecast(close, 3, 10.0, 32, 20.0)
    b = scaled_reversal_forecast(bumped, 3, 10.0, 32, 20.0)
    np.testing.assert_allclose(a.iloc[:k].to_numpy(), b.iloc[:k].to_numpy())


def test_reversal_forecast_capped() -> None:
    # Tiny vol + a big move saturates the cap.
    close = pd.Series(np.linspace(100.0, 100.5, 60), index=_idx(60))
    close.iloc[-1] = 130.0  # violent late move, tiny prior vol
    f = scaled_reversal_forecast(close, 2, 10.0, 32, 20.0)
    assert f.dropna().abs().max() <= 20.0 + 1e-9


def test_combine_is_mean_times_fdm_recapped() -> None:
    close = pd.Series(np.linspace(100.0, 130.0, 90), index=_idx(90))
    windows = (2, 3)
    parts = [scaled_reversal_forecast(close, w, 10.0, 32, 20.0) for w in windows]
    expected = (pd.concat(parts, axis=1).mean(axis=1) * 1.25).clip(-20.0, 20.0)
    got = combine_reversal_forecasts(close, windows, 10.0, 1.25, 32, 20.0)
    np.testing.assert_allclose(
        got.dropna().to_numpy(), expected.reindex(got.index).dropna().to_numpy()
    )


def test_matrix_aligned_to_union_with_nan_warmup() -> None:
    closes = {
        "UP": pd.Series(np.linspace(100.0, 200.0, 100), index=_idx(100)),
        "DOWN": pd.Series(np.linspace(200.0, 100.0, 100), index=_idx(100)),
    }
    m = reversal_forecast_matrix(closes, ReversalConfig())
    assert list(m.columns) == ["UP", "DOWN"]
    assert len(m) == 100
    assert m.iloc[0].isna().all()  # warmup NaN preserved (not filled 0)
    # last bar: winner UP short (negative), loser DOWN long (positive)
    assert m.iloc[-1]["UP"] < 0.0
    assert m.iloc[-1]["DOWN"] > 0.0


def test_crowding_sign_fades_building_crowded_long() -> None:
    from analytics.xsrev.forecast import crowding_forecast_matrix

    idx = _idx(60)
    closes = {"AAA": pd.Series(np.linspace(100.0, 120.0, 60), index=idx)}
    # crowded long (funding > 0) with a late OI SPIKE (inflow suddenly strong) ->
    # the OI-growth z-score is clearly positive at the last bar -> fade the
    # building crowded long -> forecast negative. NB a linear OI ramp would NOT
    # work: its growth-rate DECELERATES on a rising base, so the end z-score would
    # go negative and flip the sign.
    fundings = {"AAA": pd.Series(0.001, index=idx)}
    oi_vals = np.full(60, 1e6)
    oi_vals[-1] = 1.5e6
    ois = {"AAA": pd.Series(oi_vals, index=idx)}
    m = crowding_forecast_matrix(closes, fundings, ois, ReversalConfig())
    assert m["AAA"].dropna().iloc[-1] < 0.0

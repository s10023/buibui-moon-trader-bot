import numpy as np
import pandas as pd
import pytest

from analytics.cvd.forecast import (
    DEFAULT_SPANS,
    cvd_combined_forecast,
    cvd_forecast,
    cvd_forecast_matrix,
)


def _x(n: int, seed: int = 3) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    return pd.Series(rng.uniform(-1.0, 1.0, size=n), index=idx)


def test_spans_are_the_fast_legs_of_default_speeds() -> None:
    assert DEFAULT_SPANS == (8, 16, 32, 64)


def test_forecast_is_capped_both_sides() -> None:
    x = pd.Series(
        [0.0] * 40 + [50.0] * 40,
        index=pd.date_range("2020-01-01", periods=80, freq="D", tz="UTC"),
    )
    out = cvd_forecast(x, span=8, vol_span=32, cap=20.0)
    assert out.max() <= 20.0
    assert out.min() >= -20.0


def test_warmup_is_nan_not_zero() -> None:
    """NaN warm-up is load-bearing: xs_demeaned_forecasts skips NaN so only
    warmed-up instruments contribute to the cross-sectional mean. Zeros would
    silently drag that mean."""
    out = cvd_forecast(_x(100), span=8, vol_span=32, cap=20.0)
    assert out.iloc[:32].isna().all()
    assert out.iloc[-1] == out.iloc[-1]


def test_forecast_is_unshifted_and_uses_the_same_day() -> None:
    """CRITICAL: both books shift internally. This module must NOT shift.

    Changing x on the LAST day must move the last forecast value. If it does
    not, someone has added a shift here and the books will double-shift.
    """
    x = _x(120)
    base = cvd_forecast(x, span=8, vol_span=32, cap=20.0)
    bumped = x.copy()
    bumped.iloc[-1] = bumped.iloc[-1] + 5.0
    after = cvd_forecast(bumped, span=8, vol_span=32, cap=20.0)
    assert base.iloc[-1] != after.iloc[-1]


def test_causality_a_future_value_never_moves_a_past_forecast() -> None:
    """Injected-violation test: perturb the LAST day, assert nothing before it
    moves. A guard that has never been shown to fail is not known to cover
    anything, so this asserts the failure mode directly."""
    x = _x(120)
    base = cvd_forecast(x, span=8, vol_span=32, cap=20.0)
    bumped = x.copy()
    bumped.iloc[-1] = bumped.iloc[-1] + 5.0
    after = cvd_forecast(bumped, span=8, vol_span=32, cap=20.0)
    pd.testing.assert_series_equal(base.iloc[:-1], after.iloc[:-1])


def test_causality_guard_catches_an_injected_lookahead() -> None:
    """Prove the test above is non-vacuous by feeding it a deliberately
    look-ahead series and asserting the comparison FAILS."""
    x = _x(120)
    leaky_base = cvd_forecast(x, span=8, vol_span=32, cap=20.0).shift(-1)
    bumped = x.copy()
    bumped.iloc[-1] = bumped.iloc[-1] + 5.0
    leaky_after = cvd_forecast(bumped, span=8, vol_span=32, cap=20.0).shift(-1)
    with pytest.raises(AssertionError):
        pd.testing.assert_series_equal(leaky_base.iloc[:-1], leaky_after.iloc[:-1])


def test_combined_is_capped_after_fdm() -> None:
    out = cvd_combined_forecast(
        _x(300), spans=DEFAULT_SPANS, fdm=1.25, vol_span=32, cap=20.0
    )
    assert out.dropna().abs().max() <= 20.0


def test_matrix_is_union_indexed_with_symbol_columns() -> None:
    a = _x(120, seed=1)
    b = _x(90, seed=2)
    mat = cvd_forecast_matrix(
        {"BTCUSDT": a, "ETHUSDT": b},
        spans=DEFAULT_SPANS,
        fdm=1.25,
        vol_span=32,
        cap=20.0,
    )
    assert list(mat.columns) == ["BTCUSDT", "ETHUSDT"]
    assert len(mat.index) == 120
    assert mat.index.equals(a.index.union(b.index))

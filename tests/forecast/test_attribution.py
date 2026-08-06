from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analytics.forecast.attribution import (
    attribution_frame,
    book_day_attribution,
    dominant_regime,
    effective_independent_series,
    instrument_day_attribution,
    regime_labels,
)
from analytics.forecast.book import ForecastBookResult
from analytics.forecast.config import ForecastConfig
from analytics.regime import classify_series


def _bars(n: int = 400) -> pd.DataFrame:
    """Daily OHLCV that changes regime part-way through.

    First half trends hard enough to clear the slope threshold; second half is
    flat. The point is only that ``classify_series`` returns more than one label
    on it — the lag tests below assert that explicitly rather than assuming it.
    """
    idx = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    ramp = np.linspace(100.0, 400.0, n // 2)
    flat = np.full(n - n // 2, 400.0)
    close = np.concatenate([ramp, flat])
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
        },
        index=idx,
    )


def _result(
    idx: pd.DatetimeIndex,
    per_instrument: dict[str, pd.Series],
    port: np.ndarray | None = None,
) -> ForecastBookResult:
    n = len(idx)
    zeros = np.zeros(n, dtype=np.float64)
    return ForecastBookResult(
        daily_index=idx,
        portfolio_return=zeros if port is None else port,
        pre_governor_return=zeros,
        governor=zeros,
        active_count=np.zeros(n, dtype=np.int64),
        per_instrument_net=per_instrument,
    )


class TestRegimeLabelLag:
    """The label must be knowable BEFORE the return it is used to explain."""

    def test_lag_one_is_the_previous_bars_label(self) -> None:
        bars = _bars()
        raw = classify_series(bars, "1d")
        lagged = regime_labels({"AAAUSDT": bars}, lag=1)["AAAUSDT"]
        pd.testing.assert_series_equal(lagged, raw.shift(1), check_names=False)

    def test_lag_zero_is_the_contemporaneous_label(self) -> None:
        bars = _bars()
        raw = classify_series(bars, "1d")
        same = regime_labels({"AAAUSDT": bars}, lag=0)["AAAUSDT"]
        pd.testing.assert_series_equal(same, raw, check_names=False)

    def test_fixture_actually_changes_label_so_the_lag_is_detectable(self) -> None:
        """Positive control — without this the two tests above are vacuous.

        If the fixture produced one constant label, ``shift(1)`` would be a
        no-op and both assertions would pass whether or not the lag is applied.
        """
        bars = _bars()
        lag0 = regime_labels({"AAAUSDT": bars}, lag=0)["AAAUSDT"]
        lag1 = regime_labels({"AAAUSDT": bars}, lag=1)["AAAUSDT"]
        assert lag0.dropna().nunique() > 1, "fixture must span >1 regime"
        differing = (lag0 != lag1) & lag0.notna() & lag1.notna()
        assert differing.any(), "shift(1) must be observable on this fixture"

    def test_negative_lag_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="lag must be >= 0"):
            regime_labels({"AAAUSDT": _bars()}, lag=-1)

    def test_empty_frame_is_skipped(self) -> None:
        assert regime_labels({"AAAUSDT": pd.DataFrame()}) == {}


class TestInstrumentDayAttribution:
    def test_returns_land_in_their_own_regime_cell(self) -> None:
        idx = pd.date_range("2021-01-01", periods=4, freq="D", tz="UTC")
        net = pd.Series([0.01, 0.02, -0.03, -0.04], index=idx)
        labels = {"AAAUSDT": pd.Series(["trend", "trend", "range", "range"], index=idx)}
        cells = instrument_day_attribution(
            _result(idx, {"AAAUSDT": net}), labels, ForecastConfig()
        )
        by_regime = {c.regime: c for c in cells}
        assert by_regime["trend"].n_obs == 2
        assert by_regime["range"].n_obs == 2
        assert by_regime["trend"].mean_return == pytest.approx(0.015)
        assert by_regime["range"].mean_return == pytest.approx(-0.035)

    def test_shares_sum_to_one_and_n_obs_sum_to_total(self) -> None:
        idx = pd.date_range("2021-01-01", periods=6, freq="D", tz="UTC")
        net = pd.Series([0.01, 0.02, -0.03, -0.04, 0.05, 0.06], index=idx)
        labels = {
            "AAAUSDT": pd.Series(
                ["trend", "range", "high_vol", "trend", "range", "high_vol"], index=idx
            )
        }
        cells = instrument_day_attribution(
            _result(idx, {"AAAUSDT": net}), labels, ForecastConfig()
        )
        assert sum(c.n_obs for c in cells) == 6
        assert sum(c.share for c in cells) == pytest.approx(1.0)

    def test_days_the_sleeve_held_nothing_are_excluded(self) -> None:
        """A NaN net is warm-up or a missing bar — not a zero-return day."""
        idx = pd.date_range("2021-01-01", periods=4, freq="D", tz="UTC")
        net = pd.Series([np.nan, np.nan, 0.02, 0.04], index=idx)
        labels = {"AAAUSDT": pd.Series(["trend"] * 4, index=idx)}
        cells = instrument_day_attribution(
            _result(idx, {"AAAUSDT": net}), labels, ForecastConfig()
        )
        assert len(cells) == 1
        assert cells[0].n_obs == 2
        assert cells[0].mean_return == pytest.approx(0.03)

    def test_days_with_a_missing_label_are_excluded(self) -> None:
        idx = pd.date_range("2021-01-01", periods=3, freq="D", tz="UTC")
        net = pd.Series([0.01, 0.02, 0.03], index=idx)
        labels = {"AAAUSDT": pd.Series([None, "trend", "trend"], index=idx)}
        cells = instrument_day_attribution(
            _result(idx, {"AAAUSDT": net}), labels, ForecastConfig()
        )
        assert sum(c.n_obs for c in cells) == 2

    def test_pools_across_instruments(self) -> None:
        idx = pd.date_range("2021-01-01", periods=2, freq="D", tz="UTC")
        per = {
            "AAAUSDT": pd.Series([0.01, 0.02], index=idx),
            "BBBUSDT": pd.Series([0.03, 0.04], index=idx),
        }
        labels = {
            "AAAUSDT": pd.Series(["trend", "trend"], index=idx),
            "BBBUSDT": pd.Series(["trend", "trend"], index=idx),
        }
        cells = instrument_day_attribution(_result(idx, per), labels, ForecastConfig())
        assert len(cells) == 1
        assert cells[0].n_obs == 4

    def test_instrument_without_labels_is_skipped(self) -> None:
        idx = pd.date_range("2021-01-01", periods=2, freq="D", tz="UTC")
        per = {
            "AAAUSDT": pd.Series([0.01, 0.02], index=idx),
            "BBBUSDT": pd.Series([9.0, 9.0], index=idx),
        }
        labels = {"AAAUSDT": pd.Series(["trend", "trend"], index=idx)}
        cells = instrument_day_attribution(_result(idx, per), labels, ForecastConfig())
        assert sum(c.n_obs for c in cells) == 2

    def test_no_instruments_gives_no_cells(self) -> None:
        idx = pd.date_range("2021-01-01", periods=2, freq="D", tz="UTC")
        assert instrument_day_attribution(_result(idx, {}), {}, ForecastConfig()) == []


class TestDominantRegime:
    def test_majority_label_wins(self) -> None:
        idx = pd.date_range("2021-01-01", periods=1, freq="D", tz="UTC")
        per = {s: pd.Series([0.0], index=idx) for s in ("A", "B", "C")}
        labels = {
            "A": pd.Series(["trend"], index=idx),
            "B": pd.Series(["trend"], index=idx),
            "C": pd.Series(["range"], index=idx),
        }
        assert dominant_regime(_result(idx, per), labels).iloc[0] == "trend"

    def test_tie_breaks_by_regime_priority_not_symbol_order(self) -> None:
        idx = pd.date_range("2021-01-01", periods=1, freq="D", tz="UTC")
        per = {s: pd.Series([0.0], index=idx) for s in ("Z", "A")}
        labels = {
            "Z": pd.Series(["range"], index=idx),
            "A": pd.Series(["high_vol"], index=idx),
        }
        assert dominant_regime(_result(idx, per), labels).iloc[0] == "high_vol"

    def test_instruments_not_held_do_not_vote(self) -> None:
        idx = pd.date_range("2021-01-01", periods=1, freq="D", tz="UTC")
        per = {
            "A": pd.Series([np.nan], index=idx),
            "B": pd.Series([np.nan], index=idx),
            "C": pd.Series([0.01], index=idx),
        }
        labels = {
            "A": pd.Series(["range"], index=idx),
            "B": pd.Series(["range"], index=idx),
            "C": pd.Series(["trend"], index=idx),
        }
        assert dominant_regime(_result(idx, per), labels).iloc[0] == "trend"

    def test_day_with_nothing_held_is_nan(self) -> None:
        idx = pd.date_range("2021-01-01", periods=1, freq="D", tz="UTC")
        per = {"A": pd.Series([np.nan], index=idx)}
        labels = {"A": pd.Series(["trend"], index=idx)}
        assert pd.isna(dominant_regime(_result(idx, per), labels).iloc[0])


class TestBookDayAttribution:
    def test_book_return_lands_in_the_dominant_regime_cell(self) -> None:
        idx = pd.date_range("2021-01-01", periods=2, freq="D", tz="UTC")
        per = {"A": pd.Series([0.0, 0.0], index=idx)}
        labels = {"A": pd.Series(["trend", "range"], index=idx)}
        port = np.array([0.05, -0.01], dtype=np.float64)
        cells = book_day_attribution(_result(idx, per, port), labels, ForecastConfig())
        by_regime = {c.regime: c for c in cells}
        assert by_regime["trend"].mean_return == pytest.approx(0.05)
        assert by_regime["range"].mean_return == pytest.approx(-0.01)


class TestEffectiveIndependentSeries:
    """Pooled symbol-days are not independent draws; the t-stat must say so."""

    def test_uncorrelated_series_barely_deflate(self) -> None:
        rng = np.random.default_rng(0)
        idx = pd.date_range("2021-01-01", periods=2000, freq="D", tz="UTC")
        per = {f"S{i}": pd.Series(rng.normal(size=2000), index=idx) for i in range(10)}
        n_eff, deflator = effective_independent_series(per)
        assert n_eff > 8.0
        assert deflator == pytest.approx(1.0, abs=0.15)

    def test_perfectly_correlated_series_collapse_to_one(self) -> None:
        rng = np.random.default_rng(1)
        idx = pd.date_range("2021-01-01", periods=500, freq="D", tz="UTC")
        base = pd.Series(rng.normal(size=500), index=idx)
        per = {f"S{i}": base.copy() for i in range(9)}
        n_eff, deflator = effective_independent_series(per)
        assert n_eff == pytest.approx(1.0, abs=1e-6)
        assert deflator == pytest.approx(3.0, abs=1e-6)

    def test_correlated_cross_section_deflates_the_reported_t_stat(self) -> None:
        """Positive control — the correction must visibly change the output.

        Without this, ``t_stat`` could equal ``t_stat_naive`` in every real case
        and the tests above would still pass.
        """
        rng = np.random.default_rng(2)
        idx = pd.date_range("2021-01-01", periods=400, freq="D", tz="UTC")
        common = rng.normal(size=400)
        per = {
            f"S{i}": pd.Series(common + 0.3 * rng.normal(size=400), index=idx)
            for i in range(8)
        }
        labels = {s: pd.Series(["trend"] * 400, index=idx) for s in per}
        cells = instrument_day_attribution(_result(idx, per), labels, ForecastConfig())
        assert len(cells) == 1
        assert abs(cells[0].t_stat) < abs(cells[0].t_stat_naive)

    def test_negatively_correlated_series_never_inflate_the_t_stat(self) -> None:
        """The deflator is clamped at 1.0 — it must never make a cell look MORE
        significant than the naive computation."""
        rng = np.random.default_rng(3)
        idx = pd.date_range("2021-01-01", periods=300, freq="D", tz="UTC")
        base = rng.normal(size=300)
        per = {
            "A": pd.Series(base, index=idx),
            "B": pd.Series(-base, index=idx),
        }
        _, deflator = effective_independent_series(per)
        assert deflator >= 1.0

    def test_single_instrument_is_not_deflated(self) -> None:
        idx = pd.date_range("2021-01-01", periods=10, freq="D", tz="UTC")
        per = {"A": pd.Series(np.arange(10, dtype=float), index=idx)}
        assert effective_independent_series(per) == (1.0, 1.0)

    def test_book_day_view_is_undeflated(self) -> None:
        """Book-day returns are already aggregated — deflating twice is wrong."""
        idx = pd.date_range("2021-01-01", periods=5, freq="D", tz="UTC")
        per = {f"S{i}": pd.Series([0.0] * 5, index=idx) for i in range(5)}
        labels = {s: pd.Series(["trend"] * 5, index=idx) for s in per}
        port = np.array([0.01, -0.02, 0.03, 0.01, -0.01], dtype=np.float64)
        cells = book_day_attribution(_result(idx, per, port), labels, ForecastConfig())
        assert cells[0].t_stat == pytest.approx(cells[0].t_stat_naive)


class TestAttributionFrame:
    def test_empty_cells_give_an_empty_frame_with_columns(self) -> None:
        df = attribution_frame([])
        assert df.empty
        assert "sharpe_annual" in df.columns

    def test_frame_carries_one_row_per_cell(self) -> None:
        idx = pd.date_range("2021-01-01", periods=2, freq="D", tz="UTC")
        net = pd.Series([0.01, -0.02], index=idx)
        labels = {"A": pd.Series(["trend", "range"], index=idx)}
        cells = instrument_day_attribution(
            _result(idx, {"A": net}), labels, ForecastConfig()
        )
        df = attribution_frame(cells)
        assert len(df) == 2
        assert set(df["regime"]) == {"trend", "range"}

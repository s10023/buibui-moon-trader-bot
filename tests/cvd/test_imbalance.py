import numpy as np
import pandas as pd
import pytest

from analytics.cvd.imbalance import divergence, taker_imbalance

DAY = 86_400_000


def test_all_buyers_is_plus_one_all_sellers_is_minus_one() -> None:
    vol = pd.Series([10.0, 10.0, 10.0])
    tbv = pd.Series([10.0, 0.0, 5.0])
    out = taker_imbalance(vol, tbv)
    assert list(out) == [1.0, -1.0, 0.0]


def test_zero_volume_is_nan_not_an_exception() -> None:
    out = taker_imbalance(pd.Series([0.0]), pd.Series([0.0]))
    assert np.isnan(out.iloc[0])


def test_imbalance_is_scale_free_across_a_1000x_multiplier() -> None:
    """The 1000PEPE multiplier must cancel — this is why no rescaling is needed."""
    small = taker_imbalance(pd.Series([10.0]), pd.Series([6.0]))
    big = taker_imbalance(pd.Series([10_000.0]), pd.Series([6_000.0]))
    assert small.iloc[0] == big.iloc[0]


def _frame(times: list[int], vol: list[float], tbv: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"open_time": times, "volume": vol, "taker_buy_volume": tbv})


def test_divergence_is_spot_minus_perp_on_the_inner_join() -> None:
    spot = _frame([0, DAY], [10.0, 10.0], [10.0, 5.0])  # imb = +1.0, 0.0
    perp = _frame([0, DAY], [10.0, 10.0], [0.0, 5.0])  # imb = -1.0, 0.0
    out = divergence(spot, perp)
    assert list(out) == [2.0, 0.0]


def test_divergence_index_is_utc_midnight_timestamps() -> None:
    out = divergence(_frame([0], [10.0], [6.0]), _frame([0], [10.0], [4.0]))
    assert out.index[0] == pd.Timestamp("1970-01-01", tz="UTC")


def test_divergence_drops_days_present_on_only_one_venue() -> None:
    spot = _frame([0, DAY], [10.0, 10.0], [6.0, 6.0])
    perp = _frame([0], [10.0], [4.0])
    out = divergence(spot, perp)
    assert len(out) == 1


def test_daily_bars_are_information_complete_for_daily_cvd() -> None:
    """Spec section 3: aggregating intraday taker flow to a day is an IDENTITY.

    sum_i(2*tbv_i - vol_i) == 2*sum_i(tbv_i) - sum_i(vol_i). This is the test
    that lets a future session trust the cheap daily ingestion path instead of
    re-deriving it. Pure arithmetic — no network.
    """
    rng = np.random.default_rng(11)
    intraday_vol = rng.uniform(1.0, 100.0, size=96)
    intraday_tbv = intraday_vol * rng.uniform(0.0, 1.0, size=96)

    from_intraday = float((2.0 * intraday_tbv - intraday_vol).sum())
    day_vol = float(intraday_vol.sum())
    day_tbv = float(intraday_tbv.sum())
    from_daily = 2.0 * day_tbv - day_vol

    assert from_intraday == pytest.approx(from_daily)

    imb_daily = taker_imbalance(pd.Series([day_vol]), pd.Series([day_tbv])).iloc[0]
    assert imb_daily == pytest.approx(from_daily / day_vol)


def test_off_midnight_open_time_still_normalizes_to_utc_midnight() -> None:
    """Alignment with load_daily_inputs must hold by construction, not by luck.

    load_daily_inputs (analytics/forecast/replay.py) normalizes its index with
    .dt.normalize(). If divergence ever skips that step, an off-midnight
    open_time (e.g. an intraday bar, or a future non-Binance venue) produces an
    index entry the forecast-matrix union treats as a DISTINCT day from the real
    one, silently doubling rows and filling the panel with NaN.
    """
    noon = 12 * 3600 * 1000  # 12:00 UTC on day 0 — deliberately off-midnight
    out = divergence(_frame([noon], [10.0], [6.0]), _frame([noon], [10.0], [4.0]))
    assert out.index[0] == pd.Timestamp("1970-01-01", tz="UTC")


def test_divergence_collapses_same_day_duplicates_keeping_last_and_sorts() -> None:
    """Normalizing can newly collapse two distinct open_times onto one day.

    Mirrors load_daily_inputs' three-step idiom: normalize, then
    ``duplicated(keep="last")``, then ``sort_index()``. Two day-0 rows (03:00
    and 20:00 UTC) carry deliberately DIFFERENT divergence values so the test
    proves the LATER row survives, not merely that some row does; a day-1 row
    that sorts before day-0 in the input order proves the final ascending sort.
    """
    day0_early = 3 * 3600 * 1000  # 03:00 UTC day 0 -> divergence +1.0, discarded
    day0_late = 20 * 3600 * 1000  # 20:00 UTC day 0 -> divergence -1.0, kept
    day1 = DAY + 5 * 3600 * 1000  # 05:00 UTC day 1, listed out of order below

    times = [day1, day0_early, day0_late]
    spot = _frame(times, [10.0, 10.0, 10.0], [10.0, 10.0, 0.0])
    perp = _frame(times, [10.0, 10.0, 10.0], [10.0, 5.0, 5.0])
    out = divergence(spot, perp)

    assert list(out.index) == [
        pd.Timestamp("1970-01-01", tz="UTC"),
        pd.Timestamp("1970-01-02", tz="UTC"),
    ]
    assert list(out) == [-1.0, 0.0]

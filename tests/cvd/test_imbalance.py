import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analytics.cvd.imbalance import divergence, taker_imbalance

DAY = 86_400_000

_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"
_COMPLETENESS_FIXTURE = _FIXTURE_DIR / "cvd_daily_completeness_btcusdt_spot.json"


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


def test_daily_aggregation_is_an_algebraic_identity_not_an_empirical_claim() -> None:
    """ALGEBRAIC identity only: sum_i(2*tbv_i - vol_i) == 2*sum_i(tbv_i) -
    sum_i(vol_i), true by linearity of summation for ANY input and therefore
    incapable of failing.

    This does NOT test spec section 3's actual claim, which is empirical: does
    Binance's own 1d kline field 9 (taker_buy_base_asset_volume) equal the sum
    of that day's 15m field 9s? Defining "daily" values as the sum of
    "intraday" ones (as this test does) makes the assertion below a
    restatement of arithmetic, not a check against real data — it cannot fail
    for any input, including a hypothetical Binance that aggregates
    differently. FINDING 3 named this a tautology; the real claim is now
    tested empirically in
    ``test_binance_1d_taker_buy_volume_matches_the_sum_of_its_15m_bars``
    against a captured real sample. Kept here, renamed, because it is still a
    genuine (if narrower) regression guard on ``taker_imbalance``'s algebra.
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


def test_binance_1d_taker_buy_volume_matches_the_sum_of_its_15m_bars() -> None:
    """EMPIRICAL claim, spec sections 3 and 10: does Binance's own 1d kline
    field 9 (taker_buy_base_asset_volume) equal the sum of that day's 15m
    field 9s? Loads a captured real sample — BTCUSDT SPOT,
    2024-01-01..2024-01-08, one symbol-week, fetched via a single one-shot
    network call and committed as a fixture (``payload["source"]`` records
    the endpoint) — rather than re-deriving daily values from the intraday
    rows, so a genuine mismatch in Binance's own aggregation would show up
    here. This test itself makes no network call.

    Measured residual (2026-08-11, this exact fixture): both `volume` and
    `taker_buy_volume` sums matched the 1d kline's own fields to float64
    bit-exactness (0.0) on all 7 days — see the final-fix report. The
    tolerances below are generous float-accumulation slack, not a fit to the
    measured value.
    """
    payload = json.loads(_COMPLETENESS_FIXTURE.read_text())
    bars_15m: list[list[float]] = payload["bars_15m"]  # [open_time, vol, tbv]
    bars_1d: list[list[float]] = payload["bars_1d"]
    assert len(bars_1d) == 7
    assert len(bars_15m) == 7 * 96

    by_day: dict[int, list[tuple[float, float]]] = {}
    for open_time, vol, tbv in bars_15m:
        by_day.setdefault(int(open_time) // DAY, []).append((vol, tbv))

    max_vol_resid = 0.0
    max_tbv_resid = 0.0
    max_imb_resid = 0.0
    for open_time_1d, vol_1d, tbv_1d in bars_1d:
        day = int(open_time_1d) // DAY
        rows = by_day[day]
        assert len(rows) == 96, f"day {day} has {len(rows)} 15m bars, expected 96"
        agg_vol = sum(v for v, _ in rows)
        agg_tbv = sum(b for _, b in rows)

        max_vol_resid = max(max_vol_resid, abs(agg_vol - vol_1d))
        max_tbv_resid = max(max_tbv_resid, abs(agg_tbv - tbv_1d))

        imb_1d = taker_imbalance(pd.Series([vol_1d]), pd.Series([tbv_1d])).iloc[0]
        imb_agg = taker_imbalance(pd.Series([agg_vol]), pd.Series([agg_tbv])).iloc[0]
        max_imb_resid = max(max_imb_resid, abs(imb_1d - imb_agg))

    assert max_vol_resid < 1e-6
    assert max_tbv_resid < 1e-6
    assert max_imb_resid < 1e-9


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

"""USD/JPY carry-unwind state logic (H15). Pure — no network, no DB."""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.fx_carry import (
    MAG_SPAN,
    MAG_WINDOW,
    RUN_EQ2,
    RUN_GE3,
    RUN_LE1,
    YEN_STRONG,
    YEN_WEAK,
    build_weekly_from_daily,
    carry_family_key,
    expand_to_days,
    iso_week_key,
    label_magnitude_states,
    label_run_states,
    london_trading_date,
    yen_strength_runs,
)

_DAY_MS = 86_400_000


def test_london_date_in_bst_uses_london_not_utc() -> None:
    """A 23:00 UTC bar in BST is the NEXT day in London — the 58% case."""
    # 2024-07-14 23:00 UTC == 2024-07-15 00:00 London (BST, UTC+1)
    assert london_trading_date(1_720_998_000_000) == "2024-07-15"


def test_london_date_in_gmt_matches_utc() -> None:
    # 2024-01-15 00:00 UTC == 2024-01-15 00:00 London (GMT)
    assert london_trading_date(1_705_276_800_000) == "2024-01-15"


def test_iso_week_key_is_monday_anchored_and_year_safe() -> None:
    assert iso_week_key("2024-01-01") == "2024-W01"  # Monday
    assert iso_week_key("2024-01-07") == "2024-W01"  # Sunday, same ISO week
    assert iso_week_key("2024-01-08") == "2024-W02"
    # 2019-12-30 is a Monday belonging to ISO week 2020-W01
    assert iso_week_key("2019-12-30") == "2020-W01"


def test_weekly_takes_last_close_in_each_iso_week() -> None:
    # Four consecutive GMT-season days: Thu, Fri (W02), Mon, Tue (W03)
    base = 1_705_276_800_000  # 2024-01-15 Monday 00:00 UTC
    daily = pd.DataFrame(
        {
            "open_time": [base, base + _DAY_MS, base + 7 * _DAY_MS, base + 8 * _DAY_MS],
            "close": [140.0, 141.0, 142.0, 143.0],
        }
    )
    wk = build_weekly_from_daily(daily)
    assert list(wk["week_key"]) == ["2024-W03", "2024-W04"]
    assert list(wk["close"]) == [141.0, 143.0]
    # known_at is one full day after the last bar's open
    assert list(wk["known_at_ms"]) == [base + 2 * _DAY_MS, base + 9 * _DAY_MS]


def test_run_counter_counts_consecutive_down_weeks() -> None:
    close = pd.Series([150.0, 149.0, 148.0, 147.0, 148.0, 147.0])
    runs = yen_strength_runs(close)
    assert np.isnan(runs.iloc[0])
    assert list(runs.iloc[1:]) == [1.0, 2.0, 3.0, 0.0, 1.0]


def test_run_states_bucket_at_2_and_3() -> None:
    runs = pd.Series([np.nan, 0.0, 1.0, 2.0, 3.0, 5.0])
    states = label_run_states(runs)
    assert states.isna().iloc[0]
    assert list(states.iloc[1:]) == [RUN_LE1, RUN_LE1, RUN_EQ2, RUN_GE3, RUN_GE3]


def test_magnitude_labels_falling_usdjpy_as_yen_strong() -> None:
    """Yen strength is USD/JPY DOWN, so a NEGATIVE z is yen_strong.

    Inverting this flips the whole verdict and nothing downstream would notice.
    """
    rng = np.random.default_rng(7)
    quiet = 150.0 + np.cumsum(rng.normal(0.0, 0.05, MAG_WINDOW + MAG_SPAN + 5))
    crash = quiet[-1] - np.arange(1, MAG_SPAN + 1) * 4.0
    spike = quiet[-1] + np.arange(1, MAG_SPAN + 1) * 4.0

    down = label_magnitude_states(pd.Series(np.concatenate([quiet, crash])))
    up = label_magnitude_states(pd.Series(np.concatenate([quiet, spike])))
    assert down.iloc[-1] == YEN_STRONG
    assert up.iloc[-1] == YEN_WEAK


def test_expand_to_days_is_strictly_causal() -> None:
    """Day D may only see weeks fully known BEFORE D 00:00 UTC."""
    known = 10 * _DAY_MS  # week becomes known at day 10 00:00 UTC
    weekly = pd.DataFrame({"known_at_ms": [known], "state": [RUN_GE3]})
    days = pd.Index([9, 10, 11], dtype="int64")
    out = expand_to_days(weekly, "state", days)
    assert pd.isna(out.loc[9])  # before it is known
    assert out.loc[10] == RUN_GE3  # known exactly at 00:00
    assert out.loc[11] == RUN_GE3


def test_expand_to_days_mutation_guard() -> None:
    """MUTATION PROOF: shifting the tag one week earlier must change day 9.

    Positive control first — without it, 'day 9 is NaN' is equally satisfied by
    correct causality and by a perturbation that did nothing at all.
    """
    week_ms = 7 * _DAY_MS
    weekly = pd.DataFrame({"known_at_ms": [10 * _DAY_MS], "state": [RUN_GE3]})
    days = pd.Index([9], dtype="int64")

    assert pd.isna(expand_to_days(weekly, "state", days).loc[9])

    leaked = weekly.assign(known_at_ms=weekly["known_at_ms"] - week_ms)
    # POSITIVE CONTROL: the perturbation genuinely moves this row.
    assert expand_to_days(leaked, "state", days).loc[9] == RUN_GE3


def test_carry_family_key_separates_the_two_axes() -> None:
    assert carry_family_key(f"{RUN_GE3}|market") == ("run", "market")
    assert carry_family_key(f"{YEN_STRONG}|market") == ("magnitude", "market")


def test_vol_normalise_is_causal_and_dimensionless() -> None:
    """r_t / sigma_{t-1}: today's return may never touch today's vol estimate."""
    from tools.carry_unwind_audit import VOL_WINDOW, vol_normalise

    rng = np.random.default_rng(3)
    r = pd.Series(rng.normal(0.0, 0.03, 200))
    z = vol_normalise(r)

    assert z.iloc[:VOL_WINDOW].isna().all()  # warm-up stays NaN
    assert 0.7 < float(z.dropna().std(ddof=1)) < 1.5  # dimensionless, ~1

    # Causality: perturbing ONLY the last return must not move any earlier z.
    bumped = r.copy()
    bumped.iloc[-1] = bumped.iloc[-1] + 10.0
    z2 = vol_normalise(bumped)
    pd.testing.assert_series_equal(z.iloc[:-1], z2.iloc[:-1])
    # POSITIVE CONTROL: the perturbation genuinely moved the final value.
    assert float(z2.iloc[-1]) != float(z.iloc[-1])

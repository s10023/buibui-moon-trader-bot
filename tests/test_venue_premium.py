"""Tests for analytics/venue_premium.py (H14 pure library core).

Spec: docs/superpowers/specs/2026-08-04-h14-coinbase-premium-state-tag-design.md
"""

import numpy as np
import pandas as pd

from analytics.audit_guard import (
    DECISION_CONCENTRATE,
    DECISION_DISABLE,
    DECISION_ENABLE,
    DECISION_INSUFFICIENT,
    AuditCell,
)
from analytics.venue_premium import (
    VERDICT_AVOID,
    VERDICT_BUILD,
    VERDICT_INSUFFICIENT,
    VERDICT_NO_EDGE,
    build_premium_series,
    build_state_cells,
    causal_zscore,
    collapse_to_daily,
    evaluate_premium_states,
    label_changes,
    label_levels,
    map_verdict,
    sign_agrees_early_late,
)

DAY = 86_400_000


def _series(vals: list[float]) -> pd.Series:
    return pd.Series(vals, index=range(len(vals)), dtype=float)


def test_premium_removes_the_peg_deviation() -> None:
    cb = _series([100.0])
    bn = _series([100.0])
    usdt = _series([0.99])
    out = build_premium_series(cb, bn, usdt)
    assert out["prem_raw"].iloc[0] == 0.0
    # peg-adjusted: 100 / (100 * 0.99) - 1 == +1.0101%
    assert abs(out["prem_adj"].iloc[0] - 0.010101) < 1e-5
    assert abs(out["peg_dev"].iloc[0] + 0.01) < 1e-12


def test_causal_zscore_never_uses_the_future() -> None:
    rng = np.random.default_rng(0)
    base = _series(list(rng.normal(size=200)))
    z = causal_zscore(base, window=90)

    bumped = base.copy()
    k = 150
    bumped.iloc[k] += 10.0
    z2 = causal_zscore(bumped, window=90)

    # Everything strictly BEFORE the perturbed day is untouched.
    pd.testing.assert_series_equal(z.iloc[:k], z2.iloc[:k])
    # And the perturbation IS visible at k, proving the test can fail.
    assert z.iloc[k] != z2.iloc[k]


def test_label_levels_uses_the_pre_registered_thresholds() -> None:
    z = _series([-2.0, -1.0, 0.0, 1.0, 2.0, float("nan")])
    out = label_levels(z)
    assert list(out[:5]) == [
        "depressed",
        "depressed",
        "neutral",
        "elevated",
        "elevated",
    ]
    assert pd.isna(out.iloc[5])


def test_build_premium_series_aligns_on_shared_index() -> None:
    # bn_btc has an extra index entry (3) that cb_btc/cb_usdt don't share;
    # the output must be limited to the intersection of cb_btc and bn_btc.
    cb = pd.Series([100.0, 101.0, 102.0], index=[0, 1, 2], dtype=float)
    bn = pd.Series([100.0, 101.0, 102.0, 103.0], index=[0, 1, 2, 3], dtype=float)
    usdt = pd.Series([1.0, 1.0, 1.0], index=[0, 1, 2], dtype=float)
    out = build_premium_series(cb, bn, usdt)
    assert list(out.index) == [0, 1, 2]
    assert list(out.columns) == ["prem_raw", "prem_adj", "peg_dev"]


def test_causal_zscore_is_nan_during_warmup() -> None:
    rng = np.random.default_rng(1)
    base = _series(list(rng.normal(size=50)))
    z = causal_zscore(base, window=90)
    # window=90 but only 50 points exist -> every value should be NaN warm-up.
    assert z.isna().all()


def test_causal_zscore_uses_prior_window_not_current_value() -> None:
    # A window of constant 1.0s followed by one huge outlier: the z-score at
    # the outlier's own index must be computed from the PRIOR window's mean/std
    # (both would be 0/NaN-guarded), not incorporate the outlier itself into
    # the window used to score it.
    vals = [1.0] * 90 + [1000.0]
    s = _series(vals)
    z = causal_zscore(s, window=90)
    # prior window (indices 0..89, all 1.0) has std=0 -> NaN guarded, so the
    # z at index 90 must be NaN (0/0 guarded via replace(0.0, nan)).
    assert pd.isna(z.iloc[90])


def test_label_changes_rising_and_falling_on_smoothed_series() -> None:
    # A4: label_changes applies a `span`-length rolling mean before diffing,
    # so short-horizon noise inside the smoothing window must NOT flip the
    # label — only the smoothed level's span-day change sign matters.
    vals = [10.0, 10.0, 10.0, 10.0, 10.0, 20.0, 20.0, 20.0, 20.0, 20.0]
    s = _series(vals)
    out = label_changes(s, span=5)
    # First `2*span - 1` entries are NaN warm-up: the rolling mean itself
    # needs `span` points to produce its first value (valid from index
    # span-1), and diff(span) additionally needs a value `span` positions
    # earlier, so the first valid diff lands at index 2*span - 1.
    warmup = 2 * 5 - 1
    assert out.iloc[:warmup].isna().all()
    # By the end, the smoothed series has risen from ~10 to ~20.
    assert out.iloc[-1] == "rising"


def test_label_changes_preserves_nan_warmup() -> None:
    s = _series([1.0, 2.0, 3.0])
    out = label_changes(s, span=5)
    assert out.isna().all()


# --------------------------------------------------------------------------- #
# Task 4: collapse_to_daily — one observation per day, one-day entry lag
# (spec Sec.5/Sec.6, amendments.md A2)
# --------------------------------------------------------------------------- #


def test_collapse_gives_one_observation_per_day_not_per_trade() -> None:
    # 4 trades, 2 days, one direction -> 2 observations, NOT 4.
    # NOTE (amendments.md A2): the `states` index is shifted one day EARLIER
    # than a naive same-day fixture would use, because collapse_to_daily maps
    # day d -> states[d - lag_days] (default lag_days=1), never states[d].
    trades = pd.DataFrame(
        {
            "entry_time": [DAY * 100 + 1, DAY * 100 + 2, DAY * 101 + 1, DAY * 101 + 2],
            "direction": ["long"] * 4,
            "pnl_r": [1.0, 3.0, -1.0, -3.0],
        }
    )
    states = pd.Series({99: "elevated", 100: "depressed"})
    out = collapse_to_daily(trades, states)
    assert len(out) == 2
    assert sorted(out["mean_r"]) == [-2.0, 2.0]
    assert set(out["state"]) == {"elevated", "depressed"}


def test_state_lag_maps_day_minus_lag_not_day_itself() -> None:
    # A trade entering during day 101 must be tagged with states[100] (the
    # last completed daily close strictly BEFORE its entry) — and must NOT
    # be tagged with states[101], which is look-ahead: day 101's own close
    # is not known until 00:00 UTC on day 102.
    trades = pd.DataFrame(
        {"entry_time": [DAY * 101 + 1], "direction": ["long"], "pnl_r": [1.0]}
    )
    states = pd.Series({100: "elevated", 101: "depressed"})
    out = collapse_to_daily(trades, states)
    assert out.loc[0, "state"] == "elevated"
    assert out.loc[0, "state"] != "depressed"


def test_state_lag_survives_gaps_in_the_day_index() -> None:
    # states has NO entry for day 101 (a gap, e.g. a day the premium fetch
    # missed). A trade on day 102 must look up states[101] (day - lag_days)
    # directly, find nothing, and be dropped. A POSITIONAL `.shift(1)` would
    # instead keep states' existing index [100, 102] and slide the VALUES
    # down by one position — silently handing day 102's lookup day 100's
    # value ("elevated") instead of correctly finding no entry.
    trades = pd.DataFrame(
        {"entry_time": [DAY * 102 + 1], "direction": ["long"], "pnl_r": [1.0]}
    )
    states = pd.Series({100: "elevated", 102: "depressed"})
    out = collapse_to_daily(trades, states)
    assert len(out) == 0


def test_collapse_on_empty_trades_returns_empty_frame() -> None:
    trades = pd.DataFrame(columns=["entry_time", "direction", "pnl_r"])
    out = collapse_to_daily(trades, pd.Series(dtype=object))
    assert list(out.columns) == ["day", "direction", "mean_r", "state"]
    assert len(out) == 0


# --------------------------------------------------------------------------- #
# Task 4: build_state_cells
# --------------------------------------------------------------------------- #


def test_build_state_cells_kept_is_same_direction_complement() -> None:
    daily = pd.DataFrame(
        {
            "day": [1, 2, 3, 4],
            "direction": ["long", "long", "long", "short"],
            "mean_r": [1.0, 2.0, -1.0, 5.0],
            "state": ["elevated", "elevated", "depressed", "elevated"],
        }
    )
    cells = build_state_cells(daily)
    by_label = {c.label: c for c in cells}
    # long/elevated: supp = the two elevated-long rows; kept = the one
    # depressed-long row. The short row must never leak into a long cell.
    assert sorted(by_label["elevated|long"].supp_r) == [1.0, 2.0]
    assert list(by_label["elevated|long"].kept_r) == [-1.0]
    assert list(by_label["depressed|long"].supp_r) == [-1.0]
    assert sorted(by_label["depressed|long"].kept_r) == [1.0, 2.0]
    # short/elevated has no same-direction complement.
    assert list(by_label["elevated|short"].supp_r) == [5.0]
    assert list(by_label["elevated|short"].kept_r) == []


def test_build_state_cells_on_empty_daily_returns_no_cells() -> None:
    daily = pd.DataFrame(columns=["day", "direction", "mean_r", "state"])
    assert build_state_cells(daily) == []


# --------------------------------------------------------------------------- #
# Task 4: _sign_agrees_early_late (amendments.md A3 point 2 — a verdict
# input, not a printed-only column)
# --------------------------------------------------------------------------- #


def test_sign_agrees_early_late_true_when_both_halves_positive() -> None:
    assert sign_agrees_early_late([1.0, 2.0, 3.0, 4.0]) is True


def test_sign_agrees_early_late_true_when_both_halves_negative() -> None:
    assert sign_agrees_early_late([-1.0, -2.0, -3.0, -4.0]) is True


def test_sign_agrees_early_late_false_when_signs_flip() -> None:
    # Early half strongly positive, late half strongly negative: the overall
    # mean can still be positive, but the early/late split must catch the
    # instability regardless.
    assert sign_agrees_early_late([10.0, 10.0, -9.0, -9.0]) is False


def test_sign_agrees_early_late_false_on_too_short_a_sequence() -> None:
    assert sign_agrees_early_late([1.0]) is False


# --------------------------------------------------------------------------- #
# Task 4: _map_verdict — the sign inversion (spec Sec.7), the A1 INSUFFICIENT
# split, and the A3 full pre-committed gate. Mirrors the direct-unit-test
# style of tests/test_indicator_condition.py's `_map_verdict` coverage.
# --------------------------------------------------------------------------- #


def _verdict(
    decision: str,
    *,
    powered_null: bool = True,
    n_days_ok: bool = True,
    dsr: float | None = 0.97,
    pbo: float | None = 0.2,
    stable: bool = True,
) -> str:
    """``map_verdict`` with a "clears the full gate" default for every
    keyword — each test overrides exactly the one input it means to fail.
    """
    return map_verdict(
        decision,
        powered_null=powered_null,
        n_days_ok=n_days_ok,
        dsr=dsr,
        pbo=pbo,
        stable=stable,
    )


def test_map_disable_full_gate_is_build() -> None:
    assert _verdict(DECISION_DISABLE) == VERDICT_BUILD


def test_map_disable_is_never_avoid() -> None:
    # Guardrail against the intuitive-but-wrong DISABLE->AVOID map (the
    # inversion that bit ST9 and H8).
    assert _verdict(DECISION_DISABLE) != VERDICT_AVOID


def test_map_enable_full_gate_is_avoid() -> None:
    assert _verdict(DECISION_ENABLE) == VERDICT_AVOID


def test_map_enable_is_never_build() -> None:
    assert _verdict(DECISION_ENABLE) != VERDICT_BUILD


def test_map_concentrate_is_always_no_edge() -> None:
    assert _verdict(DECISION_CONCENTRATE) == VERDICT_NO_EDGE


def test_map_insufficient_without_ci_containment_is_insufficient() -> None:
    # Renamed 2026-08-13: the trigger is a CI that did NOT rule out an effect
    # at the bar, which includes -- but is not limited to -- n below the floor.
    assert (
        _verdict(
            DECISION_INSUFFICIENT,
            powered_null=False,
            n_days_ok=False,
            dsr=None,
            pbo=None,
            stable=False,
        )
        == VERDICT_INSUFFICIENT
    )


def test_map_insufficient_powered_null_is_no_edge_not_insufficient() -> None:
    # amendments.md A1: audit_guard's INSUFFICIENT conflates "n < min_n"
    # (genuinely underpowered) with "the CI/Holm gate never cleared". A cell
    # whose CI RULED OUT an effect at the bar must map to NO-EDGE — collapsing
    # it into INSUFFICIENT makes the spec's most likely outcome (spec Sec.8
    # branch 2: all cells NO-EDGE) unreachable.
    #
    # ⚠ The trigger was `n >= MIN_N` until 2026-08-13, which is a sample-size
    # floor and not power: it made NO-EDGE ALWAYS reachable instead, and H14
    # published 10 such cells whose CIs were a median 5.5x the bar.
    assert (
        _verdict(
            DECISION_INSUFFICIENT, n_days_ok=False, dsr=None, pbo=None, stable=False
        )
        == VERDICT_NO_EDGE
    )


def test_map_disable_fails_mintrl_is_no_edge() -> None:
    assert _verdict(DECISION_DISABLE, n_days_ok=False) == VERDICT_NO_EDGE


def test_map_disable_fails_dsr_is_no_edge() -> None:
    assert _verdict(DECISION_DISABLE, dsr=0.80) == VERDICT_NO_EDGE


def test_map_disable_fails_pbo_is_no_edge() -> None:
    assert _verdict(DECISION_DISABLE, pbo=0.7) == VERDICT_NO_EDGE


def test_map_disable_fails_stability_is_no_edge() -> None:
    # amendments.md A3 point 2: early/late sign agreement is a VERDICT INPUT.
    assert _verdict(DECISION_DISABLE, stable=False) == VERDICT_NO_EDGE


def test_map_disable_missing_dsr_or_pbo_is_no_edge() -> None:
    assert _verdict(DECISION_DISABLE, dsr=None) == VERDICT_NO_EDGE
    assert _verdict(DECISION_DISABLE, pbo=None) == VERDICT_NO_EDGE


# --------------------------------------------------------------------------- #
# Task 4: evaluate_premium_states — the full public pipeline over AuditCells
# --------------------------------------------------------------------------- #


def test_sign_inversion_is_mapped_the_right_way_round() -> None:
    # A reliably POSITIVE slice must come back as BUILD, not AVOID.
    good = AuditCell(
        label="elevated|long", supp_r=[0.5] * 40 + [0.4] * 40, kept_r=[0.0] * 80
    )
    bad = AuditCell(
        label="depressed|long", supp_r=[-0.5] * 40 + [-0.4] * 40, kept_r=[0.0] * 80
    )
    verdicts = dict(evaluate_premium_states([good, bad]))
    assert verdicts["elevated|long"] == VERDICT_BUILD
    assert verdicts["depressed|long"] == VERDICT_AVOID


def test_underpowered_cell_is_insufficient_not_no_edge() -> None:
    thin = AuditCell(label="elevated|short", supp_r=[0.5] * 5, kept_r=[0.0] * 5)
    assert dict(evaluate_premium_states([thin]))["elevated|short"] != VERDICT_NO_EDGE
    assert (
        dict(evaluate_premium_states([thin]))["elevated|short"] == VERDICT_INSUFFICIENT
    )


def test_evaluate_premium_states_on_empty_cells_returns_empty_list() -> None:
    assert evaluate_premium_states([]) == []


def test_evaluate_premium_states_rejects_an_unrecognized_state_label() -> None:
    bogus = AuditCell(label="not_a_real_state|long", supp_r=[0.5] * 40)
    try:
        evaluate_premium_states([bogus])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for an unrecognized state token")

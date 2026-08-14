"""Unit tests for the ST9/H11 SL-horizon audit library."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analytics.sl_horizon import (
    BASELINE_ARM,
    DECISION_CONFIRMED_BAD,
    DECISION_INSUFFICIENT,
    DECISION_NO_DIFFERENCE,
    DECISION_SUSPECT,
    DEFAULT_MULTIPLIERS,
    SIGNAL_KEY,
    ArmResult,
    SLGridConfig,
    SLVerdict,
    arm_label,
    atr_by_open_time,
    baseline_levels,
    build_paired_table,
    counterfactual_levels,
    describe_horizon,
    evaluate_sl_grid,
    negative_claim_licensed,
    resolve_arm,
    window_for_signal,
)


def test_default_grid_is_the_a_priori_one() -> None:
    assert DEFAULT_MULTIPLIERS == (0.5, 1.0, 1.5, 2.0, 3.0)


def test_config_defaults_match_live_hold_horizons() -> None:
    cfg = SLGridConfig()
    assert cfg.max_hold_bars_by_tf["15m"] == 96
    assert cfg.max_hold_bars_by_tf["1h"] == 48
    assert cfg.max_hold_bars_by_tf["4h"] == 30
    assert cfg.max_hold_bars_by_tf["1d"] == 14
    assert cfg.baseline_pct == 0.02


def test_config_rejects_empty_grid() -> None:
    with pytest.raises(ValueError, match="multipliers"):
        SLGridConfig(multipliers=())


def test_config_rejects_non_positive_multiplier() -> None:
    with pytest.raises(ValueError, match="multipliers"):
        SLGridConfig(multipliers=(1.0, 0.0))


def test_arm_label_is_stable_and_distinct() -> None:
    # `:g` drops the trailing zero, so 1.0 -> "atr_1". Task 5's _k_from_arm
    # inverts this, and Task 4's fixtures use these exact strings.
    assert arm_label(1.0) == "atr_1"
    assert arm_label(0.5) == "atr_0.5"
    assert arm_label(2.0) == "atr_2"
    assert arm_label(1.0) != BASELINE_ARM


def test_baseline_levels_long_is_two_percent_below_entry() -> None:
    sl, tp = baseline_levels(100.0, "long", baseline_pct=0.02, tp_r=3.0)
    assert sl == pytest.approx(98.0)
    assert tp == pytest.approx(106.0)


def test_baseline_levels_short_mirrors_long() -> None:
    sl, tp = baseline_levels(100.0, "short", baseline_pct=0.02, tp_r=3.0)
    assert sl == pytest.approx(102.0)
    assert tp == pytest.approx(94.0)


def test_counterfactual_levels_scale_with_atr_and_k() -> None:
    sl, tp = counterfactual_levels(100.0, "long", atr=2.0, k=1.5, tp_r=3.0)
    assert sl == pytest.approx(97.0)  # 100 - 1.5 * 2.0
    assert tp == pytest.approx(109.0)  # 100 + 3.0 * 3.0


def test_counterfactual_tp_tracks_tp_r_times_sl_distance() -> None:
    for tp_r in (1.0, 2.5, 4.0):
        sl, tp = counterfactual_levels(100.0, "short", atr=1.0, k=2.0, tp_r=tp_r)
        sl_dist = abs(100.0 - sl)
        assert abs(100.0 - tp) == pytest.approx(tp_r * sl_dist)


def test_counterfactual_rejects_unknown_direction() -> None:
    with pytest.raises(ValueError, match="direction"):
        counterfactual_levels(100.0, "sideways", atr=1.0, k=1.0, tp_r=3.0)


def test_counterfactual_rejects_non_positive_risk() -> None:
    with pytest.raises(ValueError, match="sl_dist"):
        counterfactual_levels(100.0, "long", atr=0.0, k=1.0, tp_r=3.0)


def _ramp_ohlcv(n: int = 40, step: float = 1.0) -> pd.DataFrame:
    """Deterministic OHLCV with a constant 2.0-wide bar and a `step` drift."""
    open_times = [1_000 + i * 100 for i in range(n)]
    closes = [100.0 + i * step for i in range(n)]
    return pd.DataFrame(
        {
            "open_time": open_times,
            "open": [c - 0.5 for c in closes],
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [10.0] * n,
        }
    )


def _varying_range_ohlcv(n: int = 40, tr_step: float = 10.0) -> pd.DataFrame:
    """OHLCV whose true range grows every bar, so ATR14 is index-sensitive.

    Close is held constant so the high/low-vs-prev-close legs of the true-range
    formula are always exactly half the high-low leg, which means true range at
    bar i (i >= 1) is exactly ``i * tr_step`` with no ambiguity. Unlike
    `_ramp_ohlcv` (constant 2.0 true range -> constant ATR14 everywhere, so an
    off-by-one lookup would go undetected), this fixture makes ATR14 strictly
    increasing with idx: adjacent indices differ by a fixed, large step.
    """
    open_times = [1_000 + i * 100 for i in range(n)]
    close = 100.0
    widths = [i * tr_step for i in range(n)]
    return pd.DataFrame(
        {
            "open_time": open_times,
            "open": [close] * n,
            "high": [close + w / 2.0 for w in widths],
            "low": [close - w / 2.0 for w in widths],
            "close": [close] * n,
            "volume": [10.0] * n,
        }
    )


def test_atr_by_open_time_returns_a_value_per_known_bar() -> None:
    df = _ramp_ohlcv()
    got = atr_by_open_time(df, [1_000 + 20 * 100, 1_000 + 30 * 100])
    assert set(got) == {3_000, 4_000}
    assert all(v is not None and v > 0.0 for v in got.values())


def test_atr_by_open_time_is_none_for_unknown_open_time() -> None:
    df = _ramp_ohlcv()
    assert atr_by_open_time(df, [999_999]) == {999_999: None}


def test_atr_by_open_time_is_none_at_the_first_bar() -> None:
    # _compute_atr14 needs a prior close, so idx 0 has no ATR.
    df = _ramp_ohlcv()
    assert atr_by_open_time(df, [1_000]) == {1_000: None}


def test_atr_by_open_time_matches_the_engine_primitive() -> None:
    from analytics.backtest.engine import _compute_atr14

    df = _ramp_ohlcv()
    idx = 25
    expected = _compute_atr14(
        df["high"].to_numpy(dtype=np.float64),
        df["low"].to_numpy(dtype=np.float64),
        df["close"].to_numpy(dtype=np.float64),
        idx,
    )
    got = atr_by_open_time(df, [int(df["open_time"].iloc[idx])])
    assert got[int(df["open_time"].iloc[idx])] == pytest.approx(expected)


def test_atr_by_open_time_is_index_sensitive() -> None:
    """`_ramp_ohlcv`'s constant true range means every index yields the same
    ATR14, so a silent off-by-one in the open_time -> position lookup would
    still pass `test_atr_by_open_time_matches_the_engine_primitive` above (it
    would just quietly match the wrong row). This fixture's true range grows
    every bar, so neighbouring indices give measurably different ATR14 —
    proving the lookup actually resolves to the right row, not a nearby one.
    """
    from analytics.backtest.engine import _compute_atr14

    df = _varying_range_ohlcv()
    idx = 25
    open_times = [int(df["open_time"].iloc[i]) for i in (idx - 1, idx, idx + 1)]
    got = atr_by_open_time(df, open_times)

    highs = df["high"].to_numpy(dtype=np.float64)
    lows = df["low"].to_numpy(dtype=np.float64)
    closes = df["close"].to_numpy(dtype=np.float64)
    expected = {
        ot: _compute_atr14(highs, lows, closes, i)
        for ot, i in zip(open_times, (idx - 1, idx, idx + 1), strict=True)
    }

    for ot in open_times:
        assert got[ot] == pytest.approx(expected[ot])

    before, at, after = (got[ot] for ot in open_times)
    assert before is not None and at is not None and after is not None
    assert abs(at - before) > 1.0
    assert abs(at - after) > 1.0


def test_atr_by_open_time_handles_empty_frame() -> None:
    empty = pd.DataFrame(
        columns=["open_time", "open", "high", "low", "close", "volume"]
    )
    assert atr_by_open_time(empty, [1_000]) == {1_000: None}


def test_window_engine_convention_starts_at_the_bar_after_signal() -> None:
    df = _ramp_ohlcv(n=20)
    entry, highs, lows, closes = window_for_signal(
        df, sig_idx=5, convention="engine", max_hold_bars=4
    )
    # Engine enters at the OPEN of sig_idx + 1 and scans from that bar inclusive.
    assert entry == pytest.approx(float(df["open"].iloc[6]))
    assert len(highs) == 4
    assert highs[0] == pytest.approx(float(df["high"].iloc[6]))


def test_window_live_convention_starts_strictly_after_signal() -> None:
    df = _ramp_ohlcv(n=20)
    entry, highs, lows, closes = window_for_signal(
        df, sig_idx=5, convention="live", max_hold_bars=4
    )
    # Live uses the signal candle's close as entry, window strictly after it.
    assert entry == pytest.approx(float(df["close"].iloc[5]))
    assert len(highs) == 4
    assert highs[0] == pytest.approx(float(df["high"].iloc[6]))


def test_window_returns_empty_when_no_forward_bars() -> None:
    df = _ramp_ohlcv(n=8)
    _, highs, _, _ = window_for_signal(
        df, sig_idx=7, convention="engine", max_hold_bars=4
    )
    assert len(highs) == 0


def test_window_rejects_unknown_convention() -> None:
    df = _ramp_ohlcv(n=20)
    with pytest.raises(ValueError, match="convention"):
        window_for_signal(df, sig_idx=5, convention="nope", max_hold_bars=4)


def _flat_window(
    n: int, price: float = 100.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A window that never moves — guarantees an expiry, not a win or loss."""
    arr = np.full(n, price, dtype=np.float64)
    return arr.copy(), arr.copy(), arr.copy()


def test_resolve_arm_deducts_cost_from_realized_r() -> None:
    highs, lows, closes = _flat_window(10)
    res = resolve_arm(
        highs,
        lows,
        closes,
        direction="long",
        entry=100.0,
        sl_price=98.0,
        tp_r=3.0,
        max_hold_bars=10,
        round_trip_cost_pct=0.0014,
        funding_r=0.0,
    )
    assert isinstance(res, ArmResult)
    # risk = 2.0; cost = 0.0014 * 100 / 2.0 = 0.07R
    assert res.cost_r == pytest.approx(0.07)
    assert res.net_r == pytest.approx(res.realized_r - 0.07)
    assert res.net_r < res.realized_r


def test_resolve_arm_zero_cost_leaves_realized_r_untouched() -> None:
    highs, lows, closes = _flat_window(10)
    res = resolve_arm(
        highs,
        lows,
        closes,
        direction="long",
        entry=100.0,
        sl_price=98.0,
        tp_r=3.0,
        max_hold_bars=10,
        round_trip_cost_pct=0.0,
        funding_r=0.0,
    )
    assert res is not None
    assert res.cost_r == pytest.approx(0.0)
    assert res.net_r == pytest.approx(res.realized_r)


def test_resolve_arm_cost_in_r_grows_as_the_stop_tightens() -> None:
    highs, lows, closes = _flat_window(10)
    wide = resolve_arm(
        highs,
        lows,
        closes,
        direction="long",
        entry=100.0,
        sl_price=98.0,
        tp_r=3.0,
        max_hold_bars=10,
        round_trip_cost_pct=0.0014,
        funding_r=0.0,
    )
    tight = resolve_arm(
        highs,
        lows,
        closes,
        direction="long",
        entry=100.0,
        sl_price=99.5,
        tp_r=3.0,
        max_hold_bars=10,
        round_trip_cost_pct=0.0014,
        funding_r=0.0,
    )
    assert wide is not None
    assert tight is not None
    # Same cash cost, four times the risk denominator -> four times the R cost.
    assert tight.cost_r > wide.cost_r
    assert tight.cost_r == pytest.approx(4.0 * wide.cost_r)


def test_resolve_arm_subtracts_funding() -> None:
    highs, lows, closes = _flat_window(10)
    res = resolve_arm(
        highs,
        lows,
        closes,
        direction="long",
        entry=100.0,
        sl_price=98.0,
        tp_r=3.0,
        max_hold_bars=10,
        round_trip_cost_pct=0.0,
        funding_r=0.03,
    )
    assert res is not None
    assert res.net_r == pytest.approx(res.realized_r - 0.03)


def test_resolve_arm_returns_none_on_empty_window() -> None:
    empty = np.array([], dtype=np.float64)
    assert (
        resolve_arm(
            empty,
            empty,
            empty,
            direction="long",
            entry=100.0,
            sl_price=98.0,
            tp_r=3.0,
            max_hold_bars=10,
            round_trip_cost_pct=0.0,
            funding_r=0.0,
        )
        is None
    )


def test_resolve_arm_returns_none_on_zero_risk() -> None:
    highs, lows, closes = _flat_window(10)
    assert (
        resolve_arm(
            highs,
            lows,
            closes,
            direction="long",
            entry=100.0,
            sl_price=100.0,
            tp_r=3.0,
            max_hold_bars=10,
            round_trip_cost_pct=0.0,
            funding_r=0.0,
        )
        is None
    )


def _arm_rows() -> pd.DataFrame:
    """Two signals × two arms, plus one signal missing an arm."""
    return pd.DataFrame(
        [
            # signal A — complete
            {
                "symbol": "BTCUSDT",
                "tf": "1h",
                "strategy": "pin_bar",
                "direction": "long",
                "open_time": 1,
                "arm": "flat_2pct",
                "net_r": -1.0,
                "outcome": "loss",
                "exit_bar": 3,
                "sl_dist_pct": 0.02,
            },
            {
                "symbol": "BTCUSDT",
                "tf": "1h",
                "strategy": "pin_bar",
                "direction": "long",
                "open_time": 1,
                "arm": "atr_1",
                "net_r": 0.5,
                "outcome": "expired",
                "exit_bar": 9,
                "sl_dist_pct": 0.01,
            },
            # signal B — complete
            {
                "symbol": "BTCUSDT",
                "tf": "1h",
                "strategy": "pin_bar",
                "direction": "long",
                "open_time": 2,
                "arm": "flat_2pct",
                "net_r": 3.0,
                "outcome": "win",
                "exit_bar": 5,
                "sl_dist_pct": 0.02,
            },
            {
                "symbol": "BTCUSDT",
                "tf": "1h",
                "strategy": "pin_bar",
                "direction": "long",
                "open_time": 2,
                "arm": "atr_1",
                "net_r": 1.0,
                "outcome": "win",
                "exit_bar": 2,
                "sl_dist_pct": 0.01,
            },
            # signal C — MISSING the atr_1 arm, must be dropped entirely
            {
                "symbol": "BTCUSDT",
                "tf": "1h",
                "strategy": "pin_bar",
                "direction": "long",
                "open_time": 3,
                "arm": "flat_2pct",
                "net_r": -1.0,
                "outcome": "loss",
                "exit_bar": 1,
                "sl_dist_pct": 0.02,
            },
        ]
    )


def test_signal_key_is_the_documented_tuple() -> None:
    assert SIGNAL_KEY == ["symbol", "tf", "strategy", "direction", "open_time"]


def test_build_paired_table_pivots_one_row_per_signal() -> None:
    wide = build_paired_table(_arm_rows(), arms=["flat_2pct", "atr_1"])
    assert len(wide) == 2
    assert set(wide["open_time"]) == {1, 2}


def test_build_paired_table_drops_signals_missing_any_arm() -> None:
    wide = build_paired_table(_arm_rows(), arms=["flat_2pct", "atr_1"])
    # Signal C resolved under the baseline only; dropping it (rather than
    # zero-filling) is what keeps the paired difference honest.
    assert 3 not in set(wide["open_time"])


def test_build_paired_table_preserves_net_r_per_arm() -> None:
    wide = build_paired_table(_arm_rows(), arms=["flat_2pct", "atr_1"])
    row = wide[wide["open_time"] == 1].iloc[0]
    assert row["flat_2pct"] == pytest.approx(-1.0)
    assert row["atr_1"] == pytest.approx(0.5)


def test_build_paired_table_empty_input_returns_empty_frame() -> None:
    empty = pd.DataFrame(
        columns=[*SIGNAL_KEY, "arm", "net_r", "outcome", "exit_bar", "sl_dist_pct"]
    )
    assert build_paired_table(empty, arms=["flat_2pct"]).empty


def test_describe_horizon_reports_expiry_rate_and_median_bars() -> None:
    got = describe_horizon(_arm_rows(), arm="flat_2pct")
    row = got.iloc[0]
    assert row["strategy"] == "pin_bar"
    assert row["tf"] == "1h"
    assert row["n"] == 3
    # one win, two losses, no expiries in the baseline arm
    assert row["expiry_rate"] == pytest.approx(0.0)
    assert row["median_bars"] == pytest.approx(3.0)


def test_describe_horizon_computes_sl_in_atr_units() -> None:
    rows = _arm_rows()
    rows["atr_pct"] = 0.005  # ATR is 0.5% of price
    got = describe_horizon(rows, arm="flat_2pct")
    # 2% stop / 0.5% ATR = 4 ATR-widths
    assert got.iloc[0]["median_sl_atr"] == pytest.approx(4.0)


def _paired(
    n: int, baseline: float, lifts: dict[str, float], *, noise: float = 0.01
) -> pd.DataFrame:
    """A paired table with a controlled per-arm lift over the baseline."""
    rng = np.random.default_rng(7)
    data: dict[str, object] = {
        "symbol": ["BTCUSDT"] * n,
        "tf": ["1h"] * n,
        "strategy": ["pin_bar"] * n,
        "direction": ["long"] * n,
        "open_time": list(range(n)),
        BASELINE_ARM: rng.normal(baseline, noise, n),
    }
    for arm, lift in lifts.items():
        data[arm] = np.asarray(data[BASELINE_ARM]) + rng.normal(lift, noise, n)
    return pd.DataFrame(data)


def test_insufficient_when_below_min_n() -> None:
    wide = _paired(10, -0.2, {"atr_1": 0.5})
    got = evaluate_sl_grid(wide, arms=["atr_1"], cfg=SLGridConfig(min_n=30))
    assert len(got) == 1
    assert got[0].decision == DECISION_INSUFFICIENT
    assert got[0].n == 10


def test_suspect_when_an_arm_clearly_beats_the_baseline() -> None:
    wide = _paired(400, -0.2, {"atr_1": 0.5})
    got = evaluate_sl_grid(wide, arms=["atr_1"], cfg=SLGridConfig())
    assert got[0].decision == DECISION_SUSPECT
    assert got[0].best_k == pytest.approx(1.0)
    assert got[0].best_lift is not None and got[0].best_lift > 0.05


def test_confirmed_bad_when_no_arm_helps_and_baseline_is_negative() -> None:
    wide = _paired(400, -0.2, {"atr_1": 0.0})
    got = evaluate_sl_grid(wide, arms=["atr_1"], cfg=SLGridConfig())
    assert got[0].decision == DECISION_CONFIRMED_BAD
    # ST27 positive control: assert the CONTAINMENT, not just the label. A test
    # that checks only the decision passes just as happily against the old
    # criterion, which is how this defect class survived review four times.
    assert got[0].ci_hi is not None and got[0].ci_hi < SLGridConfig().bar


def test_no_difference_when_no_arm_helps_but_baseline_is_positive() -> None:
    wide = _paired(400, 0.3, {"atr_1": 0.0})
    got = evaluate_sl_grid(wide, arms=["atr_1"], cfg=SLGridConfig())
    assert got[0].decision == DECISION_NO_DIFFERENCE
    assert got[0].ci_hi is not None and got[0].ci_hi < SLGridConfig().bar


# --- ST27: a negative claim needs a CI that rules an effect out ----------------


@pytest.mark.parametrize(
    ("n", "noise", "baseline"),
    [(50, 1.0, -0.2), (40, 0.5, -0.2), (60, 0.8, -0.2), (50, 1.0, 0.3)],
)
def test_underpowered_grid_is_insufficient_not_a_negative_claim(
    n: int, noise: float, baseline: float
) -> None:
    """No arm cleared the bar, but the CI cannot rule one out — so: INSUFFICIENT.

    This is the ST27 defect. Every one of these cells read CONFIRMED-BAD (or
    NO-DIFFERENCE on a positive baseline) before the fix, purely because no arm
    cleared ``+bar``. Enumerated across the input class rather than spot-checked:
    the pre-fix spot-checks all passed while the defect stood.
    """
    wide = _paired(n, baseline, {"atr_1": 0.0}, noise=noise)
    got = evaluate_sl_grid(wide, arms=["atr_1"], cfg=SLGridConfig())
    assert got[0].decision == DECISION_INSUFFICIENT
    # The reason the claim is withheld must be legible and must be the CI.
    assert got[0].ci_hi is not None and got[0].ci_hi >= SLGridConfig().bar
    assert any("NOT powered" in r for r in got[0].reasons)


def test_negative_claim_reports_the_witness_arm_that_licenses_it() -> None:
    """A CONFIRMED-BAD row carries the CI of the arm that came closest.

    The filed ST9 table reported all 29 negative verdicts with em-dashes in the
    CI columns, so no reader could check them. The widest upper bound IS the
    predicate, so that arm is the one a reader needs.
    """
    wide = _paired(400, -0.2, {"atr_0.5": -0.30, "atr_1": 0.0, "atr_3": -0.60})
    arms = ["atr_0.5", "atr_1", "atr_3"]
    got = evaluate_sl_grid(wide, arms=arms, cfg=SLGridConfig())[0]

    assert got.decision == DECISION_CONFIRMED_BAD
    assert got.best_k == pytest.approx(1.0)  # atr_1, the least-bad arm
    assert got.ci_lo is not None and got.ci_hi is not None
    assert got.best_lift is not None
    # dsr/pbo stay unset on this branch: no Sharpe is computed, which is why
    # CLAUDE.md's directional abs() fold never bites here.
    assert got.dsr is None and got.pbo is None


def test_gates_failure_is_insufficient_not_no_difference() -> None:
    """Cleared +bar, failed PBO — "cannot rule out overfit" is not "no difference".

    Five arms with an identical lift make the best-arm ranking pure noise, so PBO
    blows past its ceiling while the paired lift CI sits entirely above the bar.
    """
    arms = ["atr_0.5", "atr_1", "atr_1.5", "atr_2", "atr_3"]
    wide = _paired(400, -0.2, dict.fromkeys(arms, 0.20), noise=0.4)
    got = evaluate_sl_grid(wide, arms=arms, cfg=SLGridConfig())[0]

    assert got.decision == DECISION_INSUFFICIENT
    # The lift is REAL: its CI excludes zero and clears the bar. That is exactly
    # what makes NO-DIFFERENCE the wrong label.
    assert got.ci_lo is not None and got.ci_lo > SLGridConfig().bar
    assert got.pbo is not None and got.pbo > 0.5
    assert any("cleared the bar" in r for r in got.reasons)


def test_gate_failure_reason_names_only_the_gate_that_failed() -> None:
    arms = ["atr_0.5", "atr_1", "atr_1.5", "atr_2", "atr_3"]
    wide = _paired(400, -0.2, dict.fromkeys(arms, 0.20), noise=0.4)
    got = evaluate_sl_grid(wide, arms=arms, cfg=SLGridConfig())[0]

    reason = next(r for r in got.reasons if "overfit gates failed" in r)
    assert "pbo=" in reason
    assert "dsr=" not in reason  # DSR passed; saying otherwise is a false claim


@pytest.mark.parametrize(
    ("ci_his", "expected"),
    [
        ([0.01, 0.02], True),  # every arm contained
        ([0.01, 0.049], True),  # just inside
        ([0.01, 0.05], False),  # exactly at the bar is not below it
        ([0.01, 0.20], False),  # one arm reaches past the bar
        ([-0.90, -0.10], True),  # arms that reliably LOSE support the claim
        ([0.01, None], False),  # an untested arm rules nothing out
        ([None], False),
        ([], False),  # no arms tested
        ([0.01, float("nan")], False),
        ([0.01, float("inf")], False),
    ],
)
def test_negative_claim_licensed_enumerates_the_input_class(
    ci_his: list[float | None], expected: bool
) -> None:
    assert negative_claim_licensed(ci_his, bar=0.05) is expected


def test_ties_break_toward_the_larger_k() -> None:
    # Two arms with an identical lift: the wider, cheaper-to-trade stop wins.
    wide = _paired(400, -0.2, {"atr_1": 0.5})
    wide["atr_2"] = wide["atr_1"]
    got = evaluate_sl_grid(wide, arms=["atr_1", "atr_2"], cfg=SLGridConfig())
    assert got[0].best_k == pytest.approx(2.0)


def test_verdicts_are_returned_per_strategy_tf_cell() -> None:
    a = _paired(400, -0.2, {"atr_1": 0.5})
    b = _paired(400, -0.2, {"atr_1": 0.0})
    b["tf"] = "4h"
    got = evaluate_sl_grid(pd.concat([a, b]), arms=["atr_1"], cfg=SLGridConfig())
    assert {v.tf for v in got} == {"1h", "4h"}
    assert all(isinstance(v, SLVerdict) for v in got)


def test_empty_input_returns_no_verdicts() -> None:
    empty = pd.DataFrame(columns=[*SIGNAL_KEY, BASELINE_ARM, "atr_1"])
    assert evaluate_sl_grid(empty, arms=["atr_1"], cfg=SLGridConfig()) == []

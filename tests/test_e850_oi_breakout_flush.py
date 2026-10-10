"""Tests for tools/e850_oi_breakout_flush.py — the E850 pre-registration's §9 hand-off."""

from __future__ import annotations

import math
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pytest

from tools import e850_oi_breakout_flush as e

REPO = Path(__file__).resolve().parent.parent
D0 = date(2023, 1, 1)
COSTS = e.Costs(fee_pct=0.0005, slippage_pct=0.0002)
ZERO = e.Costs(fee_pct=0.0, slippage_pct=0.0)


def _bars(
    closes: list[float],
    *,
    highs: list[float] | None = None,
    opens: list[float] | None = None,
    start: date = D0,
) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame(
        {
            "date": [start + timedelta(days=i) for i in range(n)],
            "open": opens if opens is not None else list(closes),
            "high": highs if highs is not None else list(closes),
            "low": list(closes),
            "close": closes,
        }
    )


def _random_bars(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1.0 + rng.normal(0.0005, 0.03, n))
    opn = np.concatenate([[100.0], close[:-1]]) * (1.0 + rng.normal(0, 0.003, n))
    high = np.maximum(close, opn) * (1.0 + np.abs(rng.normal(0, 0.01, n)))
    return _bars(list(close), highs=list(high), opens=list(opn))


def _hourly(
    oi: list[float], *, start: date = D0, extra_hours: bool = True
) -> pd.DataFrame:
    """One 23:00 row per day (the OI_D read), plus decoy rows at 22:00 and 00:00."""
    rows: list[tuple[int, float]] = []
    for i, v in enumerate(oi):
        base = e.day_ms(start + timedelta(days=i))
        rows.append((base + e.OI_STAMP_MS, v))
        if extra_hours:
            rows.append((base + 22 * 3_600_000, v * 50.0))
            rows.append((base, v * 0.02))
    df = pd.DataFrame(rows, columns=["timestamp", "oi_contracts"])
    return df.sort_values("timestamp").reset_index(drop=True)


def _random_oi(n: int, seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    return list(1e6 * np.exp(np.cumsum(rng.normal(0.0, 0.05, n))))


class TestBreak:
    def test_close_above_prior_20_highs_breaks_and_equal_does_not(self) -> None:
        highs = [100.0] * 25
        closes = [99.0] * 25
        closes[21] = 100.5  # above max(high 1..20) = 100
        closes[22] = 100.0  # equal: not a break
        highs[21] = highs[22] = 100.5
        f = e.signal_frame(_bars(closes, highs=highs), pd.Series(dtype=float))
        assert bool(f["brk"].iloc[21])
        assert not bool(f["brk"].iloc[22])  # max now includes high[21] = 100.5

    def test_window_is_the_20_bars_before_d_excluding_d(self) -> None:
        highs = [100.0] * 30
        highs[5] = 200.0  # inside D−20…D−1 for D = 25, outside for D = 26
        closes = [99.0] * 30
        closes[25] = closes[26] = 150.0
        f = e.signal_frame(_bars(closes, highs=highs), pd.Series(dtype=float))
        assert not bool(f["brk"].iloc[25])
        assert bool(f["brk"].iloc[26])

    def test_no_break_before_20_prior_bars_exist(self) -> None:
        closes = [float(i + 1) for i in range(25)]  # every close is a new high
        f = e.signal_frame(_bars(closes), pd.Series(dtype=float))
        assert not f["brk"].iloc[:20].any()
        assert f["brk"].iloc[20:].all()


class TestOiTrigger:
    def test_threshold_is_linear_percentile_of_d_minus_180_to_d_minus_1(self) -> None:
        oi = pd.Series(
            _random_oi(400, 1), index=[D0 + timedelta(days=i) for i in range(400)]
        )
        t = e.oi_trigger(oi)
        g = np.log((oi / oi.shift(5)).to_numpy(dtype=float))
        for i in (200, 300, 399):
            window = g[i - 180 : i]
            window = window[~np.isnan(window)]
            assert t["g_thr"].iloc[i] == pytest.approx(np.percentile(window, 67))
            assert t["g"].iloc[i] == pytest.approx(g[i])

    def test_fewer_than_90_values_never_qualifies(self) -> None:
        n = 120
        bars = _bars([float(i + 1) for i in range(n)])  # breaks every day from 20
        oi = [1e6 * 1.01**i for i in range(n)]
        f = e.signal_frame(bars, e.oi_daily(_hourly(oi)))
        # g first exists on day 5; >= 90 prior values first on day 5 + 90 = 95
        assert not f["treatment"].iloc[:95].any()
        assert f["control"].iloc[20:].all()

    def test_rising_oi_at_or_above_percentile_qualifies(self) -> None:
        n = 300
        oi = [1e6] * n
        rng = np.random.default_rng(3)
        for i in range(1, n):
            oi[i] = oi[i - 1] * math.exp(rng.normal(0, 0.01))
        for i in range(290, 300):
            oi[i] = oi[i - 1] * 1.05  # unusually fast build
        bars = _bars([float(i + 1) for i in range(n)])
        f = e.signal_frame(bars, e.oi_daily(_hourly(oi)))
        assert f["treatment"].iloc[295:].all()
        assert (f["g"] >= f["g_thr"]).iloc[295:].all()


class TestOiRead:
    def test_reads_only_the_2300_row(self) -> None:
        s = e.oi_daily(_hourly([10.0, 20.0, 30.0]))
        assert list(s.index) == [D0, D0 + timedelta(days=1), D0 + timedelta(days=2)]
        assert list(s) == [10.0, 20.0, 30.0]

    def test_zero_row_is_missing_not_a_reading(self) -> None:
        s = e.oi_daily(_hourly([10.0, 0.0, 30.0]))
        assert D0 + timedelta(days=1) not in s.index

    def test_missing_oi_d_or_d_minus_5_skips_the_decision_in_both_columns(
        self,
    ) -> None:
        n = 60
        bars = _bars([float(i + 1) for i in range(n)])
        h = _hourly([1e6] * n)
        gone = {40, 47}  # D = 40 lacks OI_D; D = 52 lacks OI_{D−5} (47)
        h = h[
            ~h["timestamp"].isin(
                [e.day_ms(D0 + timedelta(days=i)) + e.OI_STAMP_MS for i in gone]
            )
        ]
        f = e.signal_frame(bars, e.oi_daily(h))
        for i in (40, 45, 47, 52):
            assert not bool(f["decidable"].iloc[i])
            assert not bool(f["control"].iloc[i])
        assert bool(f["control"].iloc[41])  # OI_41 and OI_36 present

    def test_no_interpolation_across_a_gap(self) -> None:
        h = _hourly([1.0, 2.0, 3.0, 4.0])
        h = h[h["timestamp"] != e.day_ms(D0 + timedelta(days=2)) + e.OI_STAMP_MS]
        t = e.oi_trigger(e.oi_daily(h))
        assert math.isnan(t["oi_d"].iloc[2])


class TestTruncation:
    @pytest.mark.parametrize("seed", [11, 12])
    def test_flags_at_d_equal_a_run_cut_at_d(self, seed: int) -> None:
        n = 330
        bars = _random_bars(n, seed)
        h = _hourly(_random_oi(n, seed + 100))
        full = e.signal_frame(bars, e.oi_daily(h))
        assert full["treatment"].sum() > 3  # the fixture exercises the treatment
        for i in range(200, n):
            want = (bool(full["control"].iloc[i]), bool(full["treatment"].iloc[i]))
            assert e.truncated_flags(bars, h, bars["date"].iloc[i]) == want

    def test_a_peeking_frame_is_caught(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real = e.signal_frame

        def peeking(bars: pd.DataFrame, oi: pd.Series, jt: Any = ()) -> pd.DataFrame:
            f = real(bars, oi, jt)
            nxt = f["close"].shift(-1) < f["close"]  # reads D+1
            f["treatment"] = f["control"] & nxt.fillna(False).astype(bool)
            return f

        monkeypatch.setattr(e, "signal_frame", peeking)
        n = 330
        bars = _random_bars(n, 11)
        h = _hourly(_random_oi(n, 111))
        full = e.signal_frame(bars, e.oi_daily(h))
        diffs = sum(
            (bool(full["control"].iloc[i]), bool(full["treatment"].iloc[i]))
            != e.truncated_flags(bars, h, bars["date"].iloc[i])
            for i in range(200, n)
        )
        assert diffs > 0


class TestSubset:
    @pytest.mark.parametrize("seed", [21, 22, 23])
    def test_treatment_is_a_subset_of_control(self, seed: int) -> None:
        n = 500
        f = e.signal_frame(
            _random_bars(n, seed), e.oi_daily(_hourly(_random_oi(n, seed)))
        )
        assert not (f["treatment"] & ~f["control"]).any()
        assert f["control"].sum() > f["treatment"].sum() > 0


def _frame_with(triggers: dict[str, list[int]], n: int = 40) -> pd.DataFrame:
    f = _bars([100.0] * n)
    f["sigma20"] = 0.02
    for col, idx in triggers.items():
        f[col] = False
        f.loc[idx, col] = True
    return f


class TestPositions:
    def test_retrigger_while_held_is_skipped_d5_included(self) -> None:
        f = _frame_with({"treatment": [10, 13, 15, 16]})
        st = e.BuildStats()
        pos = e.build_positions("X", f, "treatment", ZERO, st)
        assert [p.trigger_date for p in pos] == [f["date"][10], f["date"][16]]
        assert st.skipped_held == 2

    def test_each_column_skips_separately(self) -> None:
        f = _frame_with({"treatment": [10], "control": [8, 10, 14]})
        pt = e.build_positions("X", f, "treatment", ZERO, e.BuildStats())
        pc = e.build_positions("X", f, "control", ZERO, e.BuildStats())
        assert [p.trigger_date for p in pt] == [f["date"][10]]
        assert [p.trigger_date for p in pc] == [f["date"][8], f["date"][14]]

    def test_exit_after_sample_end_is_dropped(self) -> None:
        f = _frame_with({"control": [30]})
        end = f["date"][34]  # D+5 = row 35 closes after it
        st = e.BuildStats()
        assert e.build_positions("X", f, "control", ZERO, st, sample_end=end) == []
        assert st.out_of_sample == 1

    def test_short_pnl_open_to_close_then_close_to_close(self) -> None:
        closes = [100.0] * 20
        opens = [100.0] * 20
        opens[6], closes[6] = 100.0, 95.0  # D+1: open 100 -> close 95
        closes[7] = 90.25  # D+2: -5% close-to-close
        f = _bars(closes, opens=opens)
        f["sigma20"] = 0.05
        f["control"] = False
        f.loc[5, "control"] = True
        (p,) = e.build_positions("X", f, "control", ZERO, e.BuildStats())
        assert p.notional == pytest.approx(20.0)
        assert p.dates == list(f["date"][6:11])
        assert p.gross_r[0] == pytest.approx(20.0 * 0.05)  # short gains on a fall
        assert p.gross_r[1] == pytest.approx(20.0 * 0.05)
        assert p.gross_r[2] == pytest.approx(-20.0 * (100.0 / 90.25 - 1.0))

    def test_cost_is_round_trip_split_entry_and_exit(self) -> None:
        f = _frame_with({"control": [5]})
        (p,) = e.build_positions("X", f, "control", COSTS, e.BuildStats())
        half = (COSTS.fee_pct + COSTS.slippage_pct) * p.notional
        assert p.cost_r == pytest.approx([half, 0.0, 0.0, 0.0, half])
        assert p.event_r == pytest.approx(-2 * (0.0005 + 0.0002) * 50.0)

    def test_costs_come_from_the_production_toml(self) -> None:
        c = e.load_costs()
        assert c.fee_pct > 0 and c.slippage_pct > 0


class TestBookDays:
    def test_mean_across_open_positions_per_day(self) -> None:
        d = [D0 + timedelta(days=i) for i in range(3)]
        p1 = e.Position("A", D0, 0.1, 10.0, d[:2], [1.0, 2.0], [0.0, 0.0])
        p2 = e.Position("B", D0, 0.1, 10.0, d[1:], [4.0, 6.0], [0.0, 1.0])
        s = e.book_days([p1, p2])
        assert list(s) == [1.0, 3.0, 5.0]

    def test_union_matrix_fills_idle_days_with_zero(self) -> None:
        a = pd.Series([1.0], index=[D0])
        b = pd.Series([2.0], index=[D0 + timedelta(days=2)])
        m = e.union_calendar_matrix([a, b])
        assert m.tolist() == [[1.0, 0.0], [0.0, 0.0], [0.0, 2.0]]


class TestJumps:
    def test_zero_rows_are_placeholders_and_jumps_skip_over_them(self) -> None:
        h = pd.DataFrame(
            {
                "timestamp": [0, 3_600_000, 7_200_000, 10_800_000],
                "oi_contracts": [100.0, 0.0, 101.0, 400.0],
            }
        )
        rep = e.classify_jumps({"X": h}, explained={})
        assert rep.zero_rows == 1
        assert [(j.timestamp, j.explanation) for j in rep.jumps] == [(10_800_000, None)]

    def test_named_jump_is_explained(self) -> None:
        h = pd.DataFrame({"timestamp": [0, 3_600_000], "oi_contracts": [1.0, 5.0]})
        rep = e.classify_jumps({"X": h}, explained={("X", 3_600_000): "why"})
        assert rep.unexplained == []

    def test_ena_listing_jump_is_the_named_timestamp(self) -> None:
        ((sym, ts),) = e.EXPLAINED_JUMPS
        assert sym == "ENAUSDT"
        assert ts == e.day_ms(date(2024, 4, 2)) + 14 * 3_600_000

    def test_unexplained_jump_blocks_decisions_whose_inputs_span_it(self) -> None:
        n = 400
        bars = _bars([float(i + 1) for i in range(n)])
        jump = e.day_ms(D0 + timedelta(days=100)) + 5 * 3_600_000
        f = e.signal_frame(bars, e.oi_daily(_hourly([1e6] * n)), [jump])
        blocked = np.flatnonzero(f["jump_blocked"].to_numpy())
        # g_104 = ln(OI_104 / OI_99) spans the jump and sits in D = 284's window
        assert blocked.min() == 100 and blocked.max() == 100 + 184
        assert not f["control"].iloc[100:285].any()
        assert bool(f["control"].iloc[285])


class TestReadings:
    def _gate(
        self, passes: bool, *, pn: bool = False, lo: float = -0.1, hi: float = 0.1
    ) -> e.GateResult:
        return e.GateResult(
            n=100, mean_r=0.0, sd_r=1.0, sharpe_per_obs=0.0, sharpe_annual=0.0,
            control_sharpe_annual=0.0, sr_variance=0.005, dsr=0.0, pbo=0.0,
            pbo_days=100, boot_lo=0.0, boot_hi=0.0, mean_ci_lo=lo, mean_ci_hi=hi,
            required_effect_r=0.2, powered_null=pn, passes=passes,
        )  # fmt: skip

    def test_order_fail_then_beta_then_xs_then_attribution(self) -> None:
        good_b = e.BetaGuard(100, 0.1, 2.0, -1.0)
        good_x = e.XsOverlap(0.1, 100, 0.1, 200)
        good_a = e.Attribution(50, 100, 0.0, 0.0, 0.5, 4.0, 2.0, 2.0)
        assert (
            e.verdict(self._gate(False, pn=True), good_b, good_x, good_a)[0] == "FAIL"
        )
        bad_b = e.BetaGuard(100, -0.01, -0.2, -1.0)
        assert e.verdict(self._gate(True), bad_b, good_x, good_a)[0] == "BETA"
        bad_x = e.XsOverlap(0.1, 100, -0.5, 200)
        assert e.verdict(self._gate(True), good_b, bad_x, good_a)[0] == "XS-OVERLAP"
        weak_a = e.Attribution(50, 100, 0.0, 0.0, 0.5, 3.0, 2.0, 1.5)
        assert (
            e.verdict(self._gate(True), good_b, good_x, weak_a)[0]
            == "BREAKOUT-REVERSAL"
        )
        assert e.verdict(self._gate(True), good_b, good_x, good_a)[0] == "PASS"

    def test_fail_without_containment_is_not_a_filed_no(self) -> None:
        b, x = e.BetaGuard(1, 0, 0, 0), e.XsOverlap(0, 0, 0, 0)
        a = e.Attribution(1, 1, 0, 0, 0, 0, 1, 0)
        assert "NOT LICENSED" in e.verdict(self._gate(False), b, x, a)[1]
        assert (
            "LICENSED: a filed NO" in e.verdict(self._gate(False, pn=True), b, x, a)[1]
        )

    def test_fail_with_ci_wholly_below_zero_says_the_short_loses(self) -> None:
        b, x = e.BetaGuard(1, 0, 0, 0), e.XsOverlap(0, 0, 0, 0)
        a = e.Attribution(1, 1, 0, 0, 0, 0, 1, 0)
        v, why = e.verdict(self._gate(False, lo=-0.3, hi=-0.05), b, x, a)
        assert v == "FAIL" and "short loses" in why and "no effect" in why

    def test_beta_guard_recovers_intercept(self) -> None:
        rng = np.random.default_rng(5)
        idx = [D0 + timedelta(days=i) for i in range(500)]
        m = pd.Series(rng.normal(0, 0.03, 500), index=idx)
        r = 0.2 - 10.0 * m + pd.Series(rng.normal(0, 0.01, 500), index=idx)
        bg = e.beta_guard(r, m)
        assert bg.intercept == pytest.approx(0.2, abs=0.01)
        assert bg.slope == pytest.approx(-10.0, abs=0.1)

    def test_attribution_deflates_the_welch_t(self) -> None:
        a = e.attribution([1.0, 2.0, 3.0], [0.0, 1.0, 2.0, 3.0], 2.0)
        assert a.diff_r == pytest.approx(0.5)
        assert a.t_deflated == pytest.approx(a.t_raw / 2.0)


def test_connection_is_pinned_to_utc(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    duckdb.connect(str(db)).close()
    conn = e.connect(str(db))
    row = conn.execute("SELECT current_setting('TimeZone')").fetchone()
    assert row is not None and row[0] == "UTC"
    conn.close()


def test_panel_is_the_universe_minus_paxg() -> None:
    syms = e.panel_symbols()
    assert len(syms) == 24 and "PAXGUSDT" not in syms and "BTCUSDT" in syms


def test_bare_invocation_works() -> None:
    proc = subprocess.run(
        [sys.executable, "tools/e850_oi_breakout_flush.py", "--help"],
        cwd=REPO,
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "--k2-only" in proc.stdout

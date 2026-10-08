"""Tests for tools/e841_holdout.py — the E841 pre-registration's §9 hand-off."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tools import e841_holdout as e

REPO = Path(__file__).resolve().parent.parent
D0 = date(2024, 1, 1)
COSTS = e.Costs(fee_pct=0.0005, slippage_pct=0.0002)
ZERO = e.Costs(fee_pct=0.0, slippage_pct=0.0)


def _bars(
    closes: list[float],
    *,
    opens: list[float] | None = None,
    volume: float = 1e9,
    start: date = D0,
) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame(
        {
            "date": [start + timedelta(days=i) for i in range(n)],
            "open": opens if opens is not None else list(closes),
            "close": closes,
            "volume": [volume] * n,
        }
    )


def _noise_closes(n: int, seed: int, sd: float = 0.01) -> list[float]:
    rng = np.random.default_rng(seed)
    return list(100.0 * np.cumprod(1.0 + rng.normal(0.0, sd, n)))


def _spike(closes: list[float], at: int, ret: float) -> list[float]:
    """Multiply every close from ``at`` on, so bar ``at`` returns exactly ``ret``."""
    out = list(closes)
    for i in range(at, len(out)):
        out[i] *= 1.0 + ret
    return out


def _flat_hedge(n: int, start: date = D0) -> e.HedgeBook:
    return e.HedgeBook.from_panel({"BTCUSDT": _bars([100.0] * n, start=start)})


class TestHoldoutList:
    def test_appendix_is_402_unique_and_outside_the_universe(self) -> None:
        syms = e.load_holdout_symbols(REPO / e.SPEC_PATH)
        assert len(syms) == e.HOLDOUT_SIZE == len(set(syms))
        uni = e.load_universe(REPO / "config/universe.toml")
        assert not set(syms) & set(uni)


class TestCosts:
    def test_read_from_config_not_restated(self, tmp_path: Path) -> None:
        cfg = tmp_path / "p.toml"
        cfg.write_text(
            "fee_pct = 0.0009\n[backtest]\nslippage_bps = 3.0\n", encoding="utf-8"
        )
        c = e.load_costs(cfg)
        assert c.fee_pct == pytest.approx(0.0009)
        assert c.slippage_pct == pytest.approx(0.0003)
        assert c.per_leg_round_trip == pytest.approx(2 * 0.0012)


class TestTrigger:
    def test_needs_both_ret_and_z(self) -> None:
        base = _noise_closes(60, seed=1, sd=0.002)
        f = e.signal_frame(_bars(_spike(base, 40, 0.06)))
        assert bool(f["trigger"].iloc[40])
        # ret 4.9% with a tiny prior sd: z is huge, ret fails
        f2 = e.signal_frame(_bars(_spike(base, 40, 0.049)))
        assert not bool(f2["trigger"].iloc[40])
        # ret 6% on a 5% prior sd: ret passes, z (~1.2) fails
        wild = _noise_closes(60, seed=2, sd=0.05)
        f3 = e.signal_frame(_bars(_spike(wild, 40, 0.06)))
        assert f3["z"].iloc[40] < e.Z_MIN
        assert not bool(f3["trigger"].iloc[40])

    def test_sigma_is_ddof1_over_the_20_returns_before_d(self) -> None:
        f = e.signal_frame(_bars(_noise_closes(60, seed=3)))
        d = 45
        expect = np.std(f["ret"].iloc[d - 20 : d].to_numpy(), ddof=1)
        assert f["sigma20"].iloc[d] == pytest.approx(expect)
        assert np.isnan(f["sigma20"].iloc[20])  # only 19 prior returns exist
        assert not np.isnan(f["sigma20"].iloc[21])

    def test_eligibility_listing_and_liquidity(self) -> None:
        closes = _noise_closes(100, seed=4)
        f = e.signal_frame(_bars(closes))
        assert not bool(f["listed_ok"].iloc[59])
        assert bool(f["listed_ok"].iloc[60])
        thin = e.signal_frame(_bars(closes, volume=1.0))
        assert not bool(thin["liquid_ok"].iloc[80])


class TestK2Fixture:
    def test_counts_and_per_date_means(self) -> None:
        base = _noise_closes(80, seed=5, sd=0.002)
        a = _spike(base, 40, 0.10)
        b = _spike(_spike(base, 40, 0.07), 60, 0.08)
        panel = {"AUSDT": _bars(a), "BUSDT": _bars(b)}
        k = e.k2_raw(panel)
        assert k.symbol_days == 3
        assert k.dates == 2  # bar 40 shared by both symbols
        fa, fb = (e.signal_frame(panel[s]) for s in ("AUSDT", "BUSDT"))
        fwd = [
            (
                fa["close"].iloc[45] / fa["close"].iloc[40]
                - 1
                + fb["close"].iloc[45] / fb["close"].iloc[40]
                - 1
            )
            / 2,
            fb["close"].iloc[65] / fb["close"].iloc[60] - 1,
        ]
        assert k.event_mean_pct == pytest.approx(np.mean(fwd) * 100)

    def test_trigger_without_five_forward_bars_is_not_counted(self) -> None:
        base = _noise_closes(50, seed=6, sd=0.002)
        k = e.k2_raw({"AUSDT": _bars(_spike(base, 46, 0.10))})
        assert k.symbol_days == 0

    def test_match_is_exact_on_counts(self) -> None:
        t = e.K2Target()
        assert e.K2Result(1079, 506, 5.7449, 1.1461).matches(t)
        assert not e.K2Result(1078, 506, 5.745, 1.146).matches(t)
        assert not e.K2Result(1079, 506, 5.7456, 1.146).matches(t)


def _causal_violation(
    fn: Callable[[pd.DataFrame], pd.DataFrame], df: pd.DataFrame, cols: list[str]
) -> int | None:
    full = fn(df)
    for t in range(e.LIQ_WINDOW, len(df)):
        cut = fn(df.iloc[: t + 1].reset_index(drop=True))
        for c in cols:
            a, b = full[c].iloc[t], cut[c].iloc[t]
            if not (a == b or (pd.isna(a) and pd.isna(b))):
                return t
    return None


class TestTruncation:
    """Triggers (and eligibility) at D are identical when the series is cut at D."""

    COLS = ["trigger", "z", "sigma20", "eligible"]

    def _df(self) -> pd.DataFrame:
        closes = _noise_closes(160, seed=7, sd=0.01)
        for at in (70, 95, 130):
            closes = _spike(closes, at, 0.08)
        return _bars(closes)

    def test_signal_frame_is_causal(self) -> None:
        df = self._df()
        assert int(e.signal_frame(df)["trigger"].sum()) >= 3  # not vacuous
        assert _causal_violation(e.signal_frame, df, self.COLS) is None

    def test_harness_catches_injected_lookahead(self) -> None:
        def peeking(df: pd.DataFrame) -> pd.DataFrame:
            out = e.signal_frame(df)
            # centred window reads 10 returns after D
            out["sigma20"] = out["ret"].rolling(20, center=True).std()
            out["z"] = out["ret"] / out["sigma20"]
            return out

        assert _causal_violation(peeking, self._df(), self.COLS) is not None


class TestPositions:
    def test_retrigger_while_held_is_skipped_never_stacked(self) -> None:
        base = _noise_closes(120, seed=8, sd=0.002)
        closes = base
        for at in (70, 72, 75, 76):  # 72 and 75 inside 71..75 → skipped; 76 trades
            closes = _spike(closes, at, 0.08)
        panel = {"AUSDT": _bars(closes)}
        pos, st = e.build_positions(panel, _flat_hedge(120), ZERO)
        f = e.signal_frame(panel["AUSDT"])
        trig = [i for i in (70, 72, 75, 76) if bool(f["trigger"].iloc[i])]
        assert 70 in trig and 75 in trig and 76 in trig
        assert [p.trigger_date for p in pos] == [
            D0 + timedelta(days=70),
            D0 + timedelta(days=76),
        ]
        assert st.skipped_held == len(trig) - 2
        days = [d for p in pos for d in p.dates]
        assert len(days) == len(set(days))  # never two positions on one day

    def test_trigger_whose_exit_is_after_sample_end_is_out(self) -> None:
        base = _noise_closes(90, seed=9, sd=0.002)
        panel = {"AUSDT": _bars(_spike(base, 80, 0.08))}
        end = D0 + timedelta(days=84)  # D+5 = day 85
        pos, st = e.build_positions(panel, _flat_hedge(90), ZERO, sample_end=end)
        assert pos == [] and st.out_of_sample == 1
        pos2, _ = e.build_positions(
            panel, _flat_hedge(90), ZERO, sample_end=end + timedelta(days=1)
        )
        assert len(pos2) == 1

    def test_long_leg_is_mtm_on_entry_notional_and_sized_one_over_sigma(self) -> None:
        base = _noise_closes(100, seed=10, sd=0.002)
        closes = _spike(base, 70, 0.08)
        opens = list(closes)
        opens[71] = closes[70] * 1.01  # D+1 open gaps up
        panel = {"AUSDT": _bars(closes, opens=opens)}
        (p,), _ = e.build_positions(panel, _flat_hedge(100), ZERO)
        f = e.signal_frame(panel["AUSDT"])
        assert p.notional == pytest.approx(1.0 / f["sigma20"].iloc[70])
        o = opens[71]
        assert p.gross_r[0] == pytest.approx(p.notional * (closes[71] - o) / o)
        assert p.gross_r[2] == pytest.approx(p.notional * (closes[73] - closes[72]) / o)
        assert sum(p.gross_r) == pytest.approx(p.notional * (closes[75] / o - 1))


class TestHedge:
    def test_basket_is_equal_notional_at_entry_and_never_rebalanced(self) -> None:
        n = 10
        x = [100.0] * n
        y = [100.0] * n
        y[3:] = [200.0] * (n - 3)  # Y doubles on day 3, then flat
        y[5:] = [100.0] * (n - 5)  # and halves back on day 5
        hb = e.HedgeBook.from_panel({"XUSDT": _bars(x), "YUSDT": _bars(y)})
        held = [D0 + timedelta(days=i) for i in range(2, 7)]
        m, mtm = hb.basket_mtm(held)
        assert m == 2
        # buy-and-hold on entry notional: +0.5 then -0.5; a daily-rebalanced
        # basket would give +0.5 then -0.25
        assert mtm == pytest.approx([0.0, 0.5, 0.0, -0.5, 0.0])
        assert sum(mtm) == pytest.approx(0.0)

    def test_members_are_symbols_with_a_bar_at_d_plus_1(self) -> None:
        late = _bars([50.0, 60.0, 70.0], start=D0 + timedelta(days=4))
        hb = e.HedgeBook.from_panel({"XUSDT": _bars([100.0] * 10), "LUSDT": late})
        m, mtm = hb.basket_mtm([D0 + timedelta(days=i) for i in range(2, 7)])
        assert m == 1 and mtm == pytest.approx([0.0] * 5)  # L listed after D+1

    def test_hedge_is_subtracted_times_n(self) -> None:
        base = _noise_closes(100, seed=11, sd=0.002)
        panel = {"AUSDT": _bars(_spike(base, 70, 0.08))}
        u = [100.0] * 100
        u[73:] = [110.0] * 27  # basket +10% on D+3
        hb = e.HedgeBook.from_panel({"BTCUSDT": _bars(u)})
        (p,), _ = e.build_positions(panel, hb, ZERO)
        (p0,), _ = e.build_positions(panel, _flat_hedge(100), ZERO)
        diff = [a - b for a, b in zip(p.gross_r, p0.gross_r, strict=True)]
        assert diff == pytest.approx([0.0, 0.0, -0.1 * p.notional, 0.0, 0.0])


class TestCost:
    def test_four_legs_half_on_entry_day_half_on_exit_day(self) -> None:
        closes = _noise_closes(100, seed=12, sd=0.002)
        closes = _spike(closes, 70, 0.08)
        closes[71:] = [closes[70]] * 29  # flat after the trigger
        panel = {"AUSDT": _bars(closes)}
        (p,), _ = e.build_positions(panel, _flat_hedge(100), COSTS)
        leg = 2 * (COSTS.fee_pct + COSTS.slippage_pct)
        assert p.gross_r == pytest.approx([0.0] * 5)
        assert p.cost_r == pytest.approx([leg * p.notional, 0, 0, 0, leg * p.notional])
        assert sum(p.net_r) == pytest.approx(
            -4 * (COSTS.fee_pct + COSTS.slippage_pct) * p.notional
        )


class TestBookDays:
    def test_mean_of_open_positions_per_day(self) -> None:
        mk = lambda s, d, r: e.Position(s, d, 1.0, 1.0, 1, [d], [r], [0.0])  # noqa: E731
        pos = [
            mk("A", D0, 1.0),
            mk("B", D0, 3.0),
            mk("A", D0 + timedelta(days=1), -1.0),
        ]
        bd = e.book_days(pos)
        assert list(bd["net_r"]) == [2.0, -1.0]
        assert list(bd["n_open"]) == [2, 1]


class TestClosedBarGuard:
    def test_refuses_a_possibly_forming_sample_end_bar(self) -> None:
        end = D0 + timedelta(days=9)
        with pytest.raises(RuntimeError, match="no bar after"):
            e.require_closed_and_cut({"A": _bars([1.0] * 10)}, end)

    def test_cuts_at_sample_end_once_a_later_bar_exists(self) -> None:
        end = D0 + timedelta(days=9)
        out = e.require_closed_and_cut({"A": _bars([1.0] * 11)}, end)
        assert out["A"]["date"].max() == end


class TestK1:
    def test_distil_power_argv_has_no_n_eff(self) -> None:
        argv = e.distil_power_argv(1000, 0.5, 0.005)
        assert "--n-eff" not in argv and "--n-series" not in argv
        assert argv[argv.index("--n-trials") + 1] == "3"

    def test_required_sharpe_rises_with_variance_and_breakeven_brackets(self) -> None:
        lo = e.required_annual_sharpe(1500, 0.005)
        hi = e.required_annual_sharpe(1500, 0.05)
        assert hi > lo > 0
        be = e.breakeven_sr_variance(1500, (lo + hi) / 2)
        assert be is not None and 0.005 < be < 0.05
        assert e.breakeven_sr_variance(1500, lo / 2) is None


def test_bare_invocation_works() -> None:
    proc = subprocess.run(
        [sys.executable, "tools/e841_holdout.py", "--help"],
        cwd=REPO,
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "--k2-explained" in proc.stdout

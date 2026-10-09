"""H13 (#848): NYSE calendar, the US-open ORB anchor, the engine's session-close
exit and tie-break, and the driver's pure pieces."""

from __future__ import annotations

import functools
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analytics.backtest.engine import run_backtest
from analytics.strategies.orb_breakout import detect_orb_breakout
from analytics.trading_calendar import nyse_sessions
from tests.test_lookahead import _first_lookahead_violation
from tools import h13_us_open_orb as h

REPO = Path(__file__).resolve().parent.parent
BAR = 900_000


def _ms(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=UTC).timestamp() * 1000)


def _day(y: int, m: int, d: int, price: float = 100.0) -> pd.DataFrame:
    """96 flat 15m bars for one UTC day: high +1, low -1 around ``price``."""
    t0 = _ms(y, m, d)
    t = [t0 + i * BAR for i in range(96)]
    return pd.DataFrame(
        {
            "open_time": t,
            "open": price,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price,
            "volume": 10.0,
        }
    )


def _set(df: pd.DataFrame, t: int, **cols: float) -> pd.DataFrame:
    i = df.index[df["open_time"] == t][0]
    for k, v in cols.items():
        df.loc[i, k] = v
    return df


def _times(sig: pd.DataFrame) -> list[tuple[int, str]]:
    return list(
        zip(
            sig["open_time"].astype("int64").tolist(),
            sig["direction"].astype(str).tolist(),
            strict=True,
        )
    )


class TestCalendar:
    def test_dst_moves_the_open_in_utc(self) -> None:
        s = {x.day: x for x in nyse_sessions(date(2026, 3, 6), date(2026, 3, 9))}
        assert s[date(2026, 3, 6)].open_ms == _ms(2026, 3, 6, 14, 30)  # EST
        assert s[date(2026, 3, 9)].open_ms == _ms(2026, 3, 9, 13, 30)  # EDT

    def test_early_close_is_13_et(self) -> None:
        (s,) = nyse_sessions(date(2024, 11, 29), date(2024, 11, 29))
        assert s.early_close and s.close_ms == _ms(2024, 11, 29, 18)

    def test_special_closure_and_observed_holidays_are_absent(self) -> None:
        days = {x.day for x in nyse_sessions(date(2022, 6, 1), date(2025, 1, 31))}
        for closed in (date(2025, 1, 9), date(2022, 6, 20), date(2023, 1, 2)):
            assert closed not in days
        assert date(2024, 12, 24) in days


class TestNyseAnchor:
    def test_range_window_and_one_fire_per_direction(self) -> None:
        df = _day(2026, 3, 9)  # EDT: open 13:30, close 20:00 UTC
        _set(df, _ms(2026, 3, 9, 15), close=102.0, high=102.5)
        _set(df, _ms(2026, 3, 9, 16), close=98.0, low=97.5)
        _set(df, _ms(2026, 3, 9, 17), close=103.0, high=103.5)  # second long: no
        sig = detect_orb_breakout(df, 2, anchor="nyse_open")
        assert _times(sig) == [
            (_ms(2026, 3, 9, 15), "long"),
            (_ms(2026, 3, 9, 16), "short"),
        ]
        assert sig["sl_price"].tolist() == [99.0, 101.0]

    def test_no_signal_inside_the_range_or_before_the_open(self) -> None:
        df = _day(2026, 3, 9)
        _set(df, _ms(2026, 3, 9, 13, 45), close=102.0)  # range bar 2
        _set(df, _ms(2026, 3, 9, 12), close=105.0, high=105.0)  # pre-open
        assert detect_orb_breakout(df, 2, anchor="nyse_open").empty

    def test_signal_bar_must_close_before_the_session_close(self) -> None:
        df = _day(2026, 3, 9)
        _set(df, _ms(2026, 3, 9, 19, 45), close=102.0)  # closes AT 20:00
        assert detect_orb_breakout(df, 2, anchor="nyse_open").empty
        _set(df, _ms(2026, 3, 9, 19, 30), close=102.0)
        sig = detect_orb_breakout(df, 2, anchor="nyse_open")
        assert _times(sig) == [(_ms(2026, 3, 9, 19, 30), "long")]

    def test_early_close_shortens_the_window(self) -> None:
        df = _day(2024, 11, 29)  # EST, early close: 14:30 -> 18:00 UTC
        _set(df, _ms(2024, 11, 29, 17, 45), close=102.0)
        assert detect_orb_breakout(df, 2, anchor="nyse_open").empty

    def test_holiday_has_no_anchor(self) -> None:
        df = _day(2025, 1, 9)  # national day of mourning
        _set(df, _ms(2025, 1, 9, 16), close=102.0)
        assert detect_orb_breakout(df, 2, anchor="nyse_open").empty

    def test_missing_range_bar_skips_the_day(self) -> None:
        df = _day(2026, 3, 9)
        _set(df, _ms(2026, 3, 9, 15), close=102.0)
        df = df[df["open_time"] != _ms(2026, 3, 9, 13, 45)].reset_index(drop=True)
        assert detect_orb_breakout(df, 2, anchor="nyse_open").empty

    def test_default_anchor_ignores_the_calendar(self) -> None:
        df = _day(2025, 1, 9)
        _set(df, _ms(2025, 1, 9, 16), close=102.0)
        assert _times(detect_orb_breakout(df, 2)) == [(_ms(2025, 1, 9, 16), "long")]


class TestNyseAnchorIsCausal:
    def test_no_lookahead_on_the_15m_fixture(self) -> None:
        df = pd.read_parquet(REPO / "tests/fixtures/btc_15m_200d.parquet")
        det = functools.partial(detect_orb_breakout, anchor="nyse_open")
        violation, tested = _first_lookahead_violation(det, df)
        assert tested > 0, "UNTESTED is not clean"
        assert violation is None


class TestK3:
    def test_default_anchor_reproduces_the_shipped_detector(self) -> None:
        ok, want, got = h.k3_fidelity(REPO / h.K3_FIXTURE, REPO / h.K3_GOLDEN)
        assert want == got == 522
        assert ok


def _bars(n: int, price: float = 100.0) -> pd.DataFrame:
    t0 = _ms(2026, 3, 9, 14)
    return pd.DataFrame(
        {
            "open_time": [t0 + i * BAR for i in range(n)],
            "open": price,
            "high": price + 0.2,
            "low": price - 0.2,
            "close": price,
            "volume": 10.0,
        }
    )


def _long_at_bar0(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [int(df["open_time"][0])],
            "direction": ["long"],
            "sl_price": [99.0],
        }
    )


def _run(df: pd.DataFrame, **kw: object) -> list[tuple[str, float | None]]:
    res = run_backtest(df, _long_at_bar0(df), "X", "15m", "orb", tp_r=1.5, **kw)  # type: ignore[arg-type]
    return [(t.outcome, t.exit_price) for t in res.trades]


class TestEngineTimeExit:
    def test_exits_at_the_close_of_the_last_bar_before_the_deadline(self) -> None:
        df = _bars(10)
        _set(df, int(df["open_time"][4]), close=100.4)
        deadline = int(df["open_time"][5])
        res = run_backtest(
            df,
            _long_at_bar0(df),
            "X",
            "15m",
            "orb",
            tp_r=1.5,
            time_exit_ms=lambda _t: deadline,
        )
        (t,) = res.trades
        assert (t.outcome, t.exit_price, t.exit_time) == ("time", 100.4, deadline)
        assert t.pnl_r == pytest.approx(0.4)

    def test_a_hit_after_the_deadline_is_not_seen(self) -> None:
        df = _bars(10)
        _set(df, int(df["open_time"][7]), high=101.6)
        deadline = int(df["open_time"][5])
        assert _run(df, time_exit_ms=lambda _t: deadline)[0][0] == "time"
        assert _run(df)[0][0] == "win"

    def test_a_hit_before_the_deadline_wins(self) -> None:
        df = _bars(10)
        _set(df, int(df["open_time"][3]), high=101.6)
        deadline = int(df["open_time"][5])
        assert _run(df, time_exit_ms=lambda _t: deadline) == [("win", 101.5)]

    def test_entry_at_or_after_the_deadline_takes_no_trade(self) -> None:
        df = _bars(10)
        deadline = int(df["open_time"][1])
        assert _run(df, time_exit_ms=lambda _t: deadline) == []

    def test_data_ending_before_the_deadline_leaves_it_open(self) -> None:
        df = _bars(10)
        deadline = int(df["open_time"][9]) + 10 * BAR
        assert _run(df, time_exit_ms=lambda _t: deadline) == [("open", None)]

    def test_none_deadline_is_no_deadline(self) -> None:
        df = _bars(10)
        assert _run(df, time_exit_ms=lambda _t: None) == _run(df) == [("open", None)]


class TestEngineTieBreak:
    def _tied(self) -> pd.DataFrame:
        df = _bars(10)
        return _set(df, int(df["open_time"][2]), high=101.6, low=98.9)

    def test_default_is_adverse_first(self) -> None:
        assert _run(self._tied()) == [("loss", 99.0)]
        assert _run(self._tied(), tie_break="adverse") == [("loss", 99.0)]

    def test_target_first_books_the_win(self) -> None:
        assert _run(self._tied(), tie_break="target") == [("win", 101.5)]

    def test_untied_bars_do_not_care(self) -> None:
        df = _bars(10)
        _set(df, int(df["open_time"][2]), low=98.9)
        assert _run(df, tie_break="target") == _run(df) == [("loss", 99.0)]


class TestDriverPieces:
    def test_costs_read_from_config_not_restated(self, tmp_path: Path) -> None:
        cfg = tmp_path / "p.toml"
        cfg.write_text(
            "fee_pct = 0.0009\nmin_sl_pct = 0.004\n[backtest]\nslippage_bps = 3.0\n",
            encoding="utf-8",
        )
        c = h.load_costs(cfg)
        assert (c.fee_pct, c.min_sl_pct) == pytest.approx((0.0009, 0.004))
        assert c.slippage_pct == pytest.approx(0.0003)

    def test_cut_and_closed_guard(self) -> None:
        cut = h.cut_ms(date(2026, 10, 8))
        assert cut == _ms(2026, 10, 9)
        g = pd.DataFrame({"open_time": [cut - BAR, cut]})
        panel = dict.fromkeys(h.SYMBOLS, g)
        out = h.require_closed_and_cut(panel, date(2026, 10, 8))
        assert all(v["open_time"].tolist() == [cut - BAR] for v in out.values())
        stale = dict.fromkeys(h.SYMBOLS, g.iloc[:1])
        with pytest.raises(RuntimeError, match="no bar after"):
            h.require_closed_and_cut(stale, date(2026, 10, 8))

    def test_session_close_lookup(self) -> None:
        f = h.session_close_lookup(date(2026, 3, 6), date(2026, 3, 9))
        assert f(_ms(2026, 3, 9, 15)) == _ms(2026, 3, 9, 20)
        assert f(_ms(2026, 3, 6, 20, 45)) == _ms(2026, 3, 6, 21)
        assert f(_ms(2026, 3, 7, 15)) is None  # Saturday
        assert f(_ms(2026, 3, 9, 20)) is None  # at the close

    def test_book_days_and_ambiguity(self) -> None:
        a = pd.DataFrame(
            {
                "symbol": ["A", "B", "A"],
                "signal_time": [1, 1, 2],
                "direction": ["long"] * 3,
                "outcome": ["loss", "win", "time"],
                "net_r": [-1.0, 1.5, 0.2],
                "day": [date(2026, 1, 2), date(2026, 1, 2), date(2026, 1, 5)],
            }
        )
        t = a.assign(outcome=["win", "win", "time"], net_r=[1.5, 1.5, 0.2])
        bd = h.book_days(a)
        assert bd.tolist() == pytest.approx([0.25, 0.2])
        assert h.ambiguous_count(a, t) == 1

    def test_calendar_matrix_fills_idle_days_with_zero(self) -> None:
        x = pd.Series([1.0, 2.0], index=[date(2026, 1, 2), date(2026, 1, 5)])
        y = pd.Series([3.0, 4.0], index=[date(2026, 1, 1), date(2026, 1, 4)])
        m = h.calendar_matrix([x, y])
        assert m.shape == (3, 2)  # 01-02 .. 01-04, the span both cover
        assert m[:, 0].tolist() == [1.0, 0.0, 0.0]
        assert m[:, 1].tolist() == [0.0, 0.0, 4.0]

    def test_k1_is_priced_without_n_eff(self) -> None:
        argv = h.distil_power_argv(1700, 1.0, 0.005)
        assert "--n-eff" not in argv and argv[argv.index("--n-trials") + 1] == "3"
        need = h.k1_required_effect(1700, 1.0, 0.005)
        assert 0.0 < need < h.K1_MAX_EFFECT_R

    def test_verdict_needs_both_tie_breaks(self) -> None:
        def g(p: bool) -> h.GateResult:
            return h.GateResult("x", 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, False, p)

        assert h.verdict([g(True), g(True)]) == "PASS"
        assert h.verdict([g(False), g(False)]) == "FAIL"
        assert h.verdict([g(True), g(False)]) == "INDETERMINATE"

    def test_measured_sr_variance_is_ddof1(self) -> None:
        rng = np.random.default_rng(0)
        a, b = rng.normal(0.1, 1, 200), rng.normal(-0.1, 1, 200)
        sa, sb = a.mean() / a.std(ddof=1), b.mean() / b.std(ddof=1)
        assert h.measured_sr_variance(a, b) == pytest.approx(
            np.var([sa, sb], ddof=1), rel=1e-6
        )


def test_bare_invocation_works() -> None:
    proc = subprocess.run(
        [sys.executable, "tools/h13_us_open_orb.py", "--help"],
        cwd=REPO,
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "--k3-only" in proc.stdout

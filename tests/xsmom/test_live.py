from __future__ import annotations

import json

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import run_xs_backtest, xs_demeaned_forecasts, xs_leverage
from analytics.xsmom.live import (
    build_target_book,
    next_period_governor,
    next_period_leverage,
    position_deltas,
    reconcile,
    target_book_from_dict,
    target_book_to_dict,
)

_SYMS = ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"]


def _make_closes(n: int = 400) -> dict[str, pd.Series]:
    idx = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    rng = np.random.default_rng(7)
    out: dict[str, pd.Series] = {}
    for i, sym in enumerate(_SYMS):
        steps = rng.normal(0.0005 * (i - 1.5), 0.02, n)
        out[sym] = pd.Series(100.0 * np.exp(np.cumsum(steps)), index=idx)
    return out


def test_reconcile_matches_book_next_bar() -> None:
    closes = _make_closes()
    cfg = ForecastConfig()
    union = xs_leverage(closes, cfg).index
    for cutoff in (union[-30], union[-15], union[-2]):
        assert reconcile(closes, cfg, cutoff) < 1e-9


def test_future_bar_does_not_leak_into_target() -> None:
    closes = _make_closes()
    cfg = ForecastConfig()
    cutoff = xs_leverage(closes, cfg).index[-10]
    base = reconcile(closes, cfg, cutoff)
    perturbed = {s: c.copy() for s, c in closes.items()}
    perturbed["AAAUSDT"].iloc[-1] *= 1.5  # a bar strictly after the cutoff
    after = reconcile(perturbed, cfg, cutoff)
    assert base < 1e-9
    assert after < 1e-9


def test_demeaned_latest_sums_to_zero() -> None:
    closes = _make_closes()
    latest = xs_demeaned_forecasts(closes, ForecastConfig()).iloc[-1]
    assert abs(float(latest.dropna().sum())) < 1e-9


def _make_fundings(closes: dict[str, pd.Series]) -> dict[str, pd.Series]:
    return {sym: pd.Series(0.0, index=s.index) for sym, s in closes.items()}


def test_governor_matches_book_next_bar() -> None:
    closes = _make_closes()
    cfg = ForecastConfig()
    res = run_xs_backtest(closes, _make_fundings(closes), cfg)
    pre = pd.Series(res.pre_governor_return, index=res.daily_index)
    g_full = pd.Series(res.governor, index=res.daily_index)
    k = len(pre) - 2  # cutoff index; next bar = k+1 = last
    g_live = next_period_governor(pre.iloc[: k + 1], cfg)
    assert abs(g_live - float(g_full.iloc[k + 1])) < 1e-9


def test_governor_cold_start_is_neutral() -> None:
    pre = pd.Series([0.01, -0.02, 0.0])  # fewer than gov_window points
    assert next_period_governor(pre, ForecastConfig()) == 1.0


def test_build_target_book_positions() -> None:
    closes = _make_closes()
    fundings = _make_fundings(closes)
    cfg = ForecastConfig()
    book = build_target_book(closes, fundings, cfg, 10_000.0)

    assert book.active_count == len(book.positions)
    assert book.active_count > 0

    res = run_xs_backtest(closes, fundings, cfg)
    pre = pd.Series(res.pre_governor_return, index=res.daily_index)
    g = next_period_governor(pre, cfg)
    lev = next_period_leverage(closes, cfg)
    assert book.governor == g

    for p in book.positions:
        assert abs(p.leverage - g * float(lev[p.symbol])) < 1e-12
        assert abs(p.notional_usd - p.leverage * 10_000.0) < 1e-9
        expected_side = (
            "long" if p.leverage > 0 else "short" if p.leverage < 0 else "flat"
        )
        assert p.side == expected_side

    assert (
        abs(book.gross_leverage - sum(abs(p.leverage) for p in book.positions)) < 1e-12
    )
    assert abs(book.net_leverage - sum(p.leverage for p in book.positions)) < 1e-12

    last = pd.Timestamp(res.daily_index[-1])
    assert book.as_of_date == last.date().isoformat()
    assert book.next_period_date == (last + pd.Timedelta(days=1)).date().isoformat()


def test_snapshot_round_trip() -> None:
    closes = _make_closes()
    book = build_target_book(closes, _make_fundings(closes), ForecastConfig(), 10_000.0)
    d = target_book_to_dict(book)
    assert json.loads(json.dumps(d)) == d  # JSON-serializable, stable
    book2 = target_book_from_dict(d)
    assert target_book_to_dict(book2) == d


def test_position_deltas() -> None:
    closes = _make_closes()
    book = build_target_book(closes, _make_fundings(closes), ForecastConfig(), 10_000.0)
    same = target_book_to_dict(book)
    deltas = position_deltas(book, same)
    assert all(abs(v) < 1e-9 for v in deltas.values())
    assert position_deltas(book, None) == {
        p.symbol: p.notional_usd for p in book.positions
    }

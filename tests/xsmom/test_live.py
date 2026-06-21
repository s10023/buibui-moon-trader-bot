from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import xs_demeaned_forecasts, xs_leverage
from analytics.xsmom.live import reconcile

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

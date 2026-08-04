from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import xs_demeaned_forecasts, xs_forecasts


def _closes() -> dict[str, pd.Series]:
    idx = pd.date_range("2021-01-01", periods=500, freq="D")
    # STRONG/WEAK are monotone ramps that saturate the EWMAC cap (±20).
    # FLAT is a seeded random walk with tiny positive drift so its forecast is
    # defined (non-NaN) and sub-cap, sitting between STRONG and WEAK.
    rng = np.random.default_rng(42)
    log_returns = rng.normal(0.0005, 0.01, 500)
    flat_rw = 200.0 * np.exp(np.cumsum(log_returns))
    return {
        "STRONG": pd.Series(np.linspace(100.0, 400.0, 500), index=idx),
        "FLAT": pd.Series(flat_rw, index=idx),
        "WEAK": pd.Series(np.linspace(400.0, 100.0, 500), index=idx),
    }


def test_xs_forecasts_aligned_to_union_index() -> None:
    closes = _closes()
    f = xs_forecasts(closes, ForecastConfig())
    assert list(f.columns) == ["STRONG", "FLAT", "WEAK"]
    assert len(f) == 500
    assert f.iloc[-1].notna().all()


def test_demeaned_forecast_rows_sum_to_zero_over_active() -> None:
    closes = _closes()
    g = xs_demeaned_forecasts(closes, ForecastConfig())
    warm = g.dropna(how="any")
    assert len(warm) > 0
    np.testing.assert_allclose(warm.sum(axis=1).to_numpy(), 0.0, atol=1e-9)
    last = g.iloc[-1]
    assert last["STRONG"] > last["FLAT"] > last["WEAK"]


def test_demean_over_active_set_with_staggered_history() -> None:
    # Heterogeneous histories: the late instrument is absent/warming on early union
    # days. The demean over the *present* (warmed) instruments must still sum to ~0,
    # and the late instrument must stay NaN — not pulled into the mean as 0. This is
    # exactly what a `fillna(0.0)` on the forecast would have broken.
    idx_full = pd.date_range("2021-01-01", periods=500, freq="D")
    idx_late = pd.date_range("2021-06-01", periods=400, freq="D")
    closes = {
        "A": pd.Series(np.linspace(100.0, 400.0, 500), index=idx_full),
        "B": pd.Series(np.linspace(400.0, 100.0, 500), index=idx_full),
        "C": pd.Series(np.linspace(100.0, 300.0, 400), index=idx_late),
    }
    g = xs_demeaned_forecasts(closes, ForecastConfig())
    # days where A & B are warmed but C is not yet defined (absent or still warming)
    early = g.loc[g["C"].isna() & g[["A", "B"]].notna().all(axis=1)]
    assert len(early) > 0
    np.testing.assert_allclose(early[["A", "B"]].sum(axis=1).to_numpy(), 0.0, atol=1e-9)
    assert early["C"].isna().all()


def test_xs_leverage_sign_long_strong_short_weak() -> None:
    from analytics.xsmom.book import xs_leverage

    lev = xs_leverage(_closes(), ForecastConfig())
    last = lev.iloc[-1]
    # strong uptrend held long, weak downtrend held short
    assert last["STRONG"] > 0.0
    assert last["WEAK"] < 0.0


def _assert_leverage_is_causal(cfg: ForecastConfig) -> None:
    """Perturb one middle bar and prove `leverage[:k+1]` cannot see it.

    **The perturbed instrument MUST be FLAT.** STRONG/WEAK are the monotone ramps
    that saturate the EWMAC +-20 cap (see `_closes`), so bumping their close moves
    the same-day forecast by EXACTLY 0.0 — row `k` then cannot discriminate, and
    the frame assertion below holds *even with the causal `.shift(1)` in
    `xs_leverage` deleted*. That is a guard which cannot fail for its stated
    reason, and it is what this test used to be: measured at `k=250`, a 1.5x bump
    gave same-day deltas of 0.000000 for STRONG versus 7.413995 for FLAT. FLAT is
    sub-cap, so its perturbation genuinely reaches row `k` and removing the shift
    turns this test RED — which the design doc requires of it.
    """
    from analytics.xsmom.book import xs_leverage

    closes = _closes()
    base = xs_leverage(closes, cfg)

    k = 250
    bumped = {s: c.copy() for s, c in closes.items()}
    bumped["FLAT"].iloc[k] *= 1.5
    after = xs_leverage(bumped, cfg)

    # Leverage at k is sized from demeaned forecasts through k-1, so close[k] must
    # not affect leverage[:k+1] for ANY column (the demean couples instruments).
    pd.testing.assert_frame_equal(
        base.iloc[: k + 1], after.iloc[: k + 1], check_names=False
    )

    # Positive control — the perturbation must actually be live, and must land on
    # the very next row. Without this the assertion above could pass simply
    # because the bump changed nothing anywhere, which is precisely how this
    # guard was vacuous. NaN would satisfy a bare `!=`, so require finite first.
    delta = np.abs(after.iloc[k + 1].to_numpy() - base.iloc[k + 1].to_numpy())
    assert np.isfinite(delta).all(), "row k+1 must be warmed up for the control"
    assert delta.max() > 1e-9, (
        "perturbation never propagated to k+1 — the causality assertion above "
        "is vacuous and would pass with the causal shift removed"
    )


def test_xs_leverage_is_causal_no_lookahead() -> None:
    _assert_leverage_is_causal(ForecastConfig())


def _fundings(closes: dict[str, pd.Series]) -> dict[str, pd.Series]:
    return {s: pd.Series(0.0, index=c.index) for s, c in closes.items()}


def test_run_xs_backtest_long_winner_short_loser_nets_positive() -> None:
    from analytics.xsmom.book import equity_curve, run_xs_backtest

    closes = _closes()
    res = run_xs_backtest(closes, _fundings(closes), ForecastConfig())
    assert res.portfolio_return.shape[0] == 500
    assert not np.isnan(res.portfolio_return).any()
    # long the strong / short the weak over a clean cross-section compounds up
    assert equity_curve(res).iloc[-1] > 1.0
    assert res.active_count.max() == 3


def test_run_xs_backtest_short_leg_receives_funding() -> None:
    from analytics.xsmom.book import run_xs_backtest

    closes = _closes()
    pos_fund = {s: pd.Series(0.001, index=c.index) for s, c in closes.items()}
    res = run_xs_backtest(closes, pos_fund, ForecastConfig())
    # WEAK is held short; positive funding on a short is a CREDIT -> net funding
    # cost on that leg is negative over the warmed-up tail.
    weak_net = res.per_instrument_net["WEAK"].dropna()
    no_fund = run_xs_backtest(closes, _fundings(closes), ForecastConfig())
    weak_net_nf = no_fund.per_instrument_net["WEAK"].dropna()
    # with positive funding the short leg nets HIGHER than with zero funding
    assert weak_net.sum() > weak_net_nf.sum()


def test_xs_leverage_dollar_neutral_rows_sum_to_zero() -> None:
    from analytics.xsmom.book import xs_leverage

    cfg = ForecastConfig(xs_dollar_neutral=True)
    lev = xs_leverage(_closes(), cfg)
    warm = lev.dropna(how="any")
    assert len(warm) > 0
    # Each active day's positions net to zero (truly dollar-neutral).
    np.testing.assert_allclose(warm.sum(axis=1).to_numpy(), 0.0, atol=1e-9)


def test_xs_leverage_dollar_neutral_changes_net_exposure() -> None:
    from analytics.xsmom.book import xs_leverage

    closes = _closes()
    off = xs_leverage(closes, ForecastConfig())
    on = xs_leverage(closes, ForecastConfig(xs_dollar_neutral=True))
    # Off path keeps a residual net exposure; the flag removes it.
    off_net = off.dropna(how="any").sum(axis=1).abs().max()
    on_net = on.dropna(how="any").sum(axis=1).abs().max()
    assert off_net > 1e-6
    assert on_net < 1e-9


def test_xs_leverage_dollar_neutral_active_set_with_staggered_history() -> None:
    from analytics.xsmom.book import xs_leverage

    idx_full = pd.date_range("2021-01-01", periods=500, freq="D")
    idx_late = pd.date_range("2021-06-01", periods=400, freq="D")
    closes = {
        "A": pd.Series(np.linspace(100.0, 400.0, 500), index=idx_full),
        "B": pd.Series(np.linspace(400.0, 100.0, 500), index=idx_full),
        "C": pd.Series(np.linspace(100.0, 300.0, 400), index=idx_late),
    }
    lev = xs_leverage(closes, ForecastConfig(xs_dollar_neutral=True))
    early = lev.loc[lev["C"].isna() & lev[["A", "B"]].notna().all(axis=1)]
    assert len(early) > 0
    # Re-center over the active set only; the absent instrument stays NaN.
    np.testing.assert_allclose(early[["A", "B"]].sum(axis=1).to_numpy(), 0.0, atol=1e-9)
    assert early["C"].isna().all()


def test_xs_leverage_dollar_neutral_is_causal_no_lookahead() -> None:
    # Same guard on the re-centered path: the dollar-neutral subtraction is a
    # same-day op on already-shifted leverage, so it must not reintroduce
    # look-ahead. Row k itself is included (`: k + 1`).
    _assert_leverage_is_causal(ForecastConfig(xs_dollar_neutral=True))


def test_injected_forecast_equals_internal_path() -> None:
    # Injecting the SAME forecast the internal EWMAC path computes must be
    # byte-identical to the default path — proves the wiring + default are intact.
    from analytics.xsmom.book import run_xs_backtest, xs_forecasts

    closes = _closes()
    f = xs_forecasts(closes, ForecastConfig())
    base = run_xs_backtest(closes, _fundings(closes), ForecastConfig())
    inj = run_xs_backtest(closes, _fundings(closes), ForecastConfig(), forecasts=f)
    np.testing.assert_array_equal(base.portfolio_return, inj.portfolio_return)


def test_injected_forecast_overrides_signal() -> None:
    # A negated forecast must flip the leverage sign vs the default path.
    from analytics.xsmom.book import xs_forecasts, xs_leverage

    closes = _closes()
    f = xs_forecasts(closes, ForecastConfig())
    base = xs_leverage(closes, ForecastConfig())
    neg = xs_leverage(closes, ForecastConfig(), forecasts=-f)
    assert base.iloc[-1]["STRONG"] > 0.0
    assert neg.iloc[-1]["STRONG"] < 0.0


def test_run_xs_backtest_forwards_forecasts_into_leverage() -> None:
    # Guards run_xs_backtest's one-line `forecasts=` forward into xs_leverage.
    # Injecting a negated forecast must change the book vs the default path; if
    # the forward were dropped, both would fall through to the same internal
    # EWMAC path and produce identical returns — the "wired but ignored kwarg"
    # gap that test_injected_forecast_equals_internal_path cannot see.
    from analytics.xsmom.book import run_xs_backtest, xs_forecasts

    closes = _closes()
    f = xs_forecasts(closes, ForecastConfig())
    base = run_xs_backtest(closes, _fundings(closes), ForecastConfig())
    neg = run_xs_backtest(closes, _fundings(closes), ForecastConfig(), forecasts=-f)
    assert not np.array_equal(base.portfolio_return, neg.portfolio_return)

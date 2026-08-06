"""Per-regime attribution for the EWMAC trend sleeve (P2 §6).

Pre-registered in three places in the P2 spec and built in zero of them until
2026-08-06; see ``docs/audits/2026-08-06-spec-reconcile-p2-ewmac-p3-combine.md``
Finding 2. Answers exactly one question: **does the trend sleeve's Sharpe
concentrate in trend regimes, as theory predicts?**

Pure over a ``ForecastBookResult`` plus per-symbol regime labels. Loading the
bars those labels come from is ``replay.load_daily_bars``'s job — this module
never touches the database.

Causality
---------
``analytics.regime.classify_series`` is itself causal: an EWM slope, a Wilder
ATR, and a *trailing* rolling quantile — no forward window and no full-sample
statistic. But the label for bar ``t`` is computed **from bar ``t``'s own
high/low/close**, so attributing ``return_t`` to ``regime_t`` sorts returns by a
label that partly knows the return (a big move both *is* the return and pushes
``atr_pct`` over its 80th percentile).

So the headline attribution uses ``lag=1``: the label as of the prior close,
knowable *before* the return is earned, matching the ``.shift(1)`` convention
``book.py`` already applies to forecast and vol. ``lag=0`` is kept deliberately
so the contamination can be **measured** rather than asserted — the gap between
the two is the size of the circularity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from analytics.forecast.book import ForecastBookResult
from analytics.forecast.config import ForecastConfig
from analytics.regime import classify_series

# §6 priority, high to low. Also the deterministic tie-break for the book-day
# dominant label, so a tied day never depends on dict or column ordering.
_REGIME_PRIORITY: tuple[str, ...] = ("high_vol", "trend", "range", "unknown")


@dataclass(frozen=True)
class RegimeCell:
    """One regime's slice of the sleeve's returns.

    ``sharpe_annual`` annualises the cell's per-observation Sharpe by
    ``sqrt(annualization_days)``. For an instrument-day table an observation is
    one symbol-day, not one book-day, so that figure is comparable **across
    cells** but not against the book's headline Sharpe. ``n_obs`` and ``t_stat``
    are printed beside it precisely so a thin cell cannot carry a verdict.
    """

    regime: str
    n_obs: int
    share: float
    mean_return: float
    sharpe_annual: float
    t_stat: float
    t_stat_naive: float


def effective_independent_series(
    per_instrument_net: dict[str, pd.Series],
) -> tuple[float, float]:
    """Return ``(n_eff, t_deflator)`` for a correlated cross-section.

    Pooling symbol-days treats 25 crypto perps as 25 independent bets. They are
    not: under an equicorrelation approximation with mean pairwise correlation
    ``rho``, ``k`` series carry the noise reduction of only
    ``n_eff = k / (1 + (k-1) * rho)`` independent ones, so a naive t-stat over
    pooled symbol-days is inflated by ``sqrt(k / n_eff)``.

    This is an approximation — it assumes constant pairwise correlation and
    ignores autocorrelation — but it is the difference between reading the
    trend cell as significant and reading it as noise, so the corrected figure
    is what :class:`RegimeCell` reports as ``t_stat``. The uncorrected value is
    kept as ``t_stat_naive`` so the adjustment is auditable rather than hidden.
    """
    k = len(per_instrument_net)
    if k < 2:
        return float(max(k, 1)), 1.0
    corr = pd.DataFrame(per_instrument_net).corr().to_numpy()
    off = corr[~np.eye(k, dtype=bool)]
    off = off[~np.isnan(off)]
    if len(off) == 0:
        return float(k), 1.0
    rho = float(np.mean(off))
    denom = 1.0 + (k - 1) * rho
    if denom <= 0:
        return float(k), 1.0
    n_eff = k / denom
    if n_eff <= 0:
        return float(k), 1.0
    # Clamped at 1.0 on purpose. A negative mean correlation would make
    # n_eff > k and the "deflator" would INFLATE the t-stat — the fail-open
    # direction for something whose whole job is to stop a cell reading as
    # significant when it is not. It never binds on this book (rho ~ +0.3),
    # but a guard should be fail-safe by construction, not by luck.
    return n_eff, max(1.0, math.sqrt(k / n_eff))


def regime_labels(
    bars: dict[str, pd.DataFrame],
    *,
    timeframe: str = "1d",
    lag: int = 1,
) -> dict[str, pd.Series]:
    """Regime label per (symbol, bar), lagged by ``lag`` bars.

    ``lag=1`` (the default) makes each label knowable at the prior close, so it
    cannot encode the return it is used to explain. ``lag=0`` is contemporaneous
    and **descriptive only** — see this module's docstring.
    """
    if lag < 0:
        raise ValueError(f"lag must be >= 0, got {lag}")
    return {
        sym: classify_series(df, timeframe).shift(lag)
        for sym, df in bars.items()
        if not df.empty
    }


def _cells(
    returns: pd.Series,
    labels: pd.Series,
    cfg: ForecastConfig,
    t_deflator: float = 1.0,
) -> list[RegimeCell]:
    """Group aligned (return, label) pairs into one cell per regime.

    ``t_deflator`` divides the naive t-stat to account for a correlated
    cross-section; 1.0 leaves it untouched (correct for already-aggregated
    book-day returns).
    """
    paired = pd.DataFrame({"r": returns, "regime": labels}).dropna()
    total = len(paired)
    if total == 0:
        return []

    ann = math.sqrt(cfg.annualization_days)
    out: list[RegimeCell] = []
    for regime, grp in paired.groupby("regime", sort=False):
        r = grp["r"].to_numpy(dtype=float)
        n = len(r)
        mean = float(np.mean(r))
        sd = float(np.std(r, ddof=1)) if n > 1 else 0.0
        sharpe = (mean / sd * ann) if sd > 1e-12 else 0.0
        t_naive = (mean / (sd / math.sqrt(n))) if sd > 1e-12 and n > 1 else 0.0
        out.append(
            RegimeCell(
                regime=str(regime),
                n_obs=n,
                share=n / total,
                mean_return=mean,
                sharpe_annual=sharpe,
                t_stat=t_naive / t_deflator if t_deflator > 0 else t_naive,
                t_stat_naive=t_naive,
            )
        )
    order = {name: i for i, name in enumerate(_REGIME_PRIORITY)}
    return sorted(out, key=lambda c: order.get(c.regime, len(order)))


def instrument_day_attribution(
    result: ForecastBookResult,
    labels: dict[str, pd.Series],
    cfg: ForecastConfig,
) -> list[RegimeCell]:
    """Pool each instrument's net return by **that instrument's own** regime.

    The direct test of the P2 §6 theory question: a trend regime is a property
    of an instrument, so the honest unit is the symbol-day. Symbol-days where
    the sleeve held nothing (net is NaN — warm-up or missing bars) are dropped,
    as are days whose label is missing.

    ``t_stat`` is deflated by :func:`effective_independent_series` — pooled
    symbol-days across a correlated cross-section are nowhere near independent,
    and the uncorrected figure reads as significant when it is not.
    """
    r_parts: list[pd.Series] = []
    l_parts: list[pd.Series] = []
    for sym, net in result.per_instrument_net.items():
        lab = labels.get(sym)
        if lab is None:
            continue
        r_parts.append(net.reset_index(drop=True))
        l_parts.append(lab.reindex(net.index).reset_index(drop=True))
    if not r_parts:
        return []
    _, deflator = effective_independent_series(result.per_instrument_net)
    return _cells(
        pd.concat(r_parts, ignore_index=True),
        pd.concat(l_parts, ignore_index=True),
        cfg,
        t_deflator=deflator,
    )


def dominant_regime(
    result: ForecastBookResult,
    labels: dict[str, pd.Series],
) -> pd.Series:
    """The most common regime across the instruments the book held that day.

    Only instruments the sleeve was actually holding count (net not NaN), so a
    symbol still in warm-up cannot vote. Ties break by ``_REGIME_PRIORITY``,
    which makes the result independent of symbol ordering.
    """
    idx = result.daily_index
    counts = pd.DataFrame(0, index=idx, columns=list(_REGIME_PRIORITY), dtype=int)
    for sym, net in result.per_instrument_net.items():
        lab = labels.get(sym)
        if lab is None:
            continue
        aligned = lab.reindex(idx).where(net.reindex(idx).notna())
        for name in _REGIME_PRIORITY:
            counts[name] = counts[name].add((aligned == name).astype(int), fill_value=0)

    held = counts.sum(axis=1)
    winner = counts.idxmax(axis=1)  # ties -> leftmost column = highest priority
    return winner.where(held > 0)


def book_day_attribution(
    result: ForecastBookResult,
    labels: dict[str, pd.Series],
    cfg: ForecastConfig,
) -> list[RegimeCell]:
    """Attribute the book's daily return to that day's dominant regime.

    The book-level companion to :func:`instrument_day_attribution`. This is the
    table the shelving decision speaks to — EWMAC is shelved as a *book*, not
    per instrument — but it answers a coarser question, since one label stands
    in for 25 instruments.
    """
    port = pd.Series(result.portfolio_return, index=result.daily_index)
    return _cells(port, dominant_regime(result, labels), cfg)


def attribution_frame(cells: list[RegimeCell]) -> pd.DataFrame:
    """Render cells as a DataFrame for terminal display."""
    if not cells:
        return pd.DataFrame(
            columns=[
                "regime",
                "n_obs",
                "share",
                "mean_return",
                "sharpe_annual",
                "t_stat",
                "t_stat_naive",
            ]
        )
    return pd.DataFrame([vars(c) for c in cells])

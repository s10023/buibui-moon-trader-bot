"""Assemble the D1 CVD verdict: headline metrics + the three-leg gate.

Mirrors ``analytics/xsmom/report.py`` and swaps its ``corr_to_trend`` stamp for
``corr_to_xsmom`` — for this sleeve the decisive comparison is against the
deploy core, not the shelved trend sleeve. A CVD book correlating ~0.9 with
XS-momentum is a restatement of the first edge, not a second one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.research_guards import (
    block_bootstrap_ci,
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
    passes_gate,
)
from portfolio import metrics


@dataclass(frozen=True)
class CVDReport:
    """Headline metrics + guards + the decorrelation read for the D1 verdict."""

    sharpe_annual: float
    max_dd: float
    annual_return: float
    annual_vol: float
    n_obs: int
    dsr: float
    pbo: float
    boot_lo: float
    boot_hi: float
    min_trl: float
    corr_to_xsmom: float
    xsmom_sharpe: float
    inverted: bool


def _per_period_sharpe(r: npt.NDArray[np.float64]) -> float:
    if len(r) < 2:
        return 0.0
    sd = float(np.std(r, ddof=1))
    if sd < 1e-12:
        return 0.0
    return float(np.mean(r) / sd)


def _ann_sharpe(r: npt.NDArray[np.float64], ann: float) -> float:
    return _per_period_sharpe(r) * ann


def _aligned_corr(
    a: npt.NDArray[np.float64],
    a_index: pd.DatetimeIndex,
    b: npt.NDArray[np.float64],
    b_index: pd.DatetimeIndex,
) -> float:
    """Correlate two return series on their SHARED DATES, never their positions.

    The CVD cross-sectional book adopts the forecast matrix's index (spot ∩
    perp dates — always a subset); the XS-momentum benchmark adopts the
    closes' union. Whenever the spot leg's history ends earlier than the perp
    leg's (e.g. a Monday backfill, a Tuesday re-run), the two arrays are the
    same length by coincidence but represent different calendar windows — a
    positional ``[-n:]`` tail slice then compares day *d* of one book against
    day *d+k* of the other, and the error runs toward manufacturing a
    decorrelation reading exactly where the study is decisive (the
    ``corr_to_xsmom`` restatement-vs-second-edge stamp). Building a
    ``pd.Series`` on each and inner-joining fixes this: a legitimately short
    spot history still yields a usable stamp over whatever days really
    overlap, rather than raising.
    """
    sa = pd.Series(a, index=a_index)
    sb = pd.Series(b, index=b_index)
    sa, sb = sa.align(sb, join="inner")
    if len(sa) < 2:
        return float("nan")
    x = sa.to_numpy(dtype=np.float64)
    y = sb.to_numpy(dtype=np.float64)
    if float(np.std(x)) == 0.0 or float(np.std(y)) == 0.0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def evaluate_cvd(
    portfolio_return: npt.NDArray[np.float64],
    cfg: ForecastConfig,
    trial_returns: dict[str, npt.NDArray[np.float64]],
    xsmom_returns: npt.NDArray[np.float64],
    *,
    portfolio_index: pd.DatetimeIndex,
    xsmom_index: pd.DatetimeIndex,
    invert: bool = False,
) -> CVDReport:
    """All D1 metrics + research-guard stamps + the decorrelation read.

    ``trial_returns`` is the declared multiple-testing family (per-span sleeves
    + combined). ``portfolio_index``/``xsmom_index`` are the two books' daily
    indices (``XSBookResult.daily_index`` / ``ForecastBookResult.daily_index``)
    — required so ``corr_to_xsmom`` aligns the two return series by DATE, not
    by array position (see ``_aligned_corr``).

    ``invert`` implements the spec's negative-direction rule (§8): an honest
    finding that runs opposite to the pre-registered sign is a SIGN FLIP, not
    a magnitude fold. Negating ``portfolio_return`` and every trial's returns
    up front — before any metric is derived — moves DSR, PBO, and boot_lo
    coherently, because every one of them is then computed from the same
    mirrored series. The predecessor implementation folded only the Sharpes
    fed to DSR (`abs(sr_d)`, `abs(trial_srs)`) while `boot_lo` kept coming
    from the raw, unfolded returns — so DSR could cross the gate while
    `boot_lo` stayed negative, making a negative verdict structurally
    unreachable through that leg instead of the one the rule was written to
    fix. `abs()` per-trial also shrinks trial dispersion in a mixed-sign
    family for no principled reason; negation does not, because it is not a
    fold — it is evaluating the mirror-image book.
    """
    sign = -1.0 if invert else 1.0
    r = sign * np.asarray(portfolio_return, dtype=np.float64)
    trial_returns = {
        k: sign * np.asarray(v, dtype=np.float64) for k, v in trial_returns.items()
    }
    curve = (1.0 + pd.Series(r)).cumprod()
    ann = math.sqrt(cfg.annualization_days)

    sr_d = _per_period_sharpe(r)
    trial_srs = [_per_period_sharpe(v) for v in trial_returns.values()]

    min_len = min((len(v) for v in trial_returns.values()), default=0)
    if min_len >= 28 and len(trial_returns) >= 2:
        mat = np.column_stack([v[-min_len:] for v in trial_returns.values()])
        pbo = cscv_pbo(mat).pbo
    else:
        pbo = float("nan")

    if sr_d != 0.0:

        def _stat_fn(x: npt.NDArray[np.float64]) -> float:
            return _ann_sharpe(x, ann)

        boot = block_bootstrap_ci(r, stat_fn=_stat_fn, seed=7)
        boot_lo, boot_hi = boot.lo, boot.hi
        dsr = deflated_sharpe_ratio(sr_d, len(r), trial_srs=trial_srs)
        min_trl = min_track_record_length(sr_d, target_sr=1.0 / ann, confidence=0.95)
    else:
        boot_lo = boot_hi = dsr = 0.0
        min_trl = float("inf")

    xsm = np.asarray(xsmom_returns, dtype=np.float64)
    xsm_curve = (1.0 + pd.Series(xsm)).cumprod()

    return CVDReport(
        sharpe_annual=metrics.sharpe(curve),
        max_dd=metrics.max_drawdown(curve),
        annual_return=metrics.annual_return(curve),
        annual_vol=metrics.annual_vol(curve),
        n_obs=len(r),
        dsr=dsr,
        pbo=pbo,
        boot_lo=boot_lo,
        boot_hi=boot_hi,
        min_trl=min_trl,
        corr_to_xsmom=_aligned_corr(r, portfolio_index, xsm, xsmom_index),
        xsmom_sharpe=metrics.sharpe(xsm_curve) if len(xsm) >= 2 else 0.0,
        inverted=invert,
    )


def cvd_gate_verdict(report: CVDReport) -> bool:
    """The headline gate: DSR >= 0.95 AND PBO <= 0.5 AND boot_lo > 0.

    Delegates to :func:`analytics.research_guards.passes_gate`; the thresholds
    live only there. ``min_trl`` and ``corr_to_xsmom`` are reported stamps, NOT
    legs — ``corr_to_xsmom`` enters the verdict as a judgement under the spec's
    section 8, with the number printed.
    """
    return passes_gate(report.dsr, report.pbo, report.boot_lo)

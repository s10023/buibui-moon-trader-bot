"""Price the power of the Stream B era study BEFORE designing it.

WHAT THIS ANSWERS
    ST17's remaining half is "re-cut filed results by era and measure how much
    moves". The instrumentation shipped 2026-08-14d (``analytics/eras.py``); this
    tool prices whether the study it enables can resolve anything, at the real n,
    on the real era spans, across a declared trial family.

    It is deliberately a *tool*, not a scratch driver. ST28 — the sixth
    powered-null site — sat in gitignored scratch code where no gate, grep or
    review surface could reach it, and its spec and its driver disagreed on the
    detection threshold. The criterion here is in the code, and the code is
    tracked, linted and type-checked.

THE THREE THINGS THAT DECIDE THE ANSWER, IN ORDER OF SIZE
    1. **Trial count, not sample size.** A per-era scan over 44 occupied eras is a
       44-trial search. Filed measurement: a 21x range of n moves the DSR bar ~10%
       while 1 -> 320 trials moves it 21x. So the scan shape is priced separately
       from the one-pre-registered-comparison shape, and the gap between them IS
       the design decision.
    2. **Alert rows are not independent observations.** Alerts fire across symbols
       within the same hours on correlated instruments. The naive per-alert n
       overstates the information by ``sqrt(k / n_eff)`` in t-units, so this tool
       deflates n by that factor squared before inverting anything. Skipping this
       is the pooling defect that reads significance into noise.
    3. **A null needs CI CONTAINMENT, never ``|delta| < MDE``.** The powered-null
       family has reached six sites, each spelling the arithmetic differently.
       This study's natural conclusion ("we re-cut by era and nothing moved") is
       exactly the shape that produces a seventh, so the containment half-width is
       priced here, against a bar declared up front rather than derived from the
       noise it is supposed to survive.

ERA KEY
    ``fired_at_ms``. A ledger sample is keyed on FIRE time, where market time and
    wall-clock time coincide. Never ``entry_time`` — that is simulated market time
    on a backtest row and comparing it against code-change dates is a category
    error (see ``analytics.eras``).

UNITS
    Every Sharpe here is PER ALERT, not annualised, and every effect size is
    R per alert. The two are one multiplication apart and quoting one as the other
    is the ``bar``-units trap.
"""

from __future__ import annotations

import argparse
import math
import statistics as st
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd

from analytics.audit_guard import powered_null
from analytics.eras import EraBoundary, load_boundaries, split_by_era
from analytics.forecast.attribution import effective_independent_series
from analytics.research_guards import GATE_DSR as GATE_DSR
from analytics.research_guards import required_sharpe

#: Economic bars for the containment test, in R per alert. 0.05R is the filed
#: precedent from the H14/H15 cross-asset panels; 0.10R is carried beside it
#: because a bar that small may not be worth acting on even if resolvable, and
#: showing both stops the reader inferring one from the other.
CONTAINMENT_BARS_R = (0.05, 0.10)

#: Two-sided 95% normal quantile, for the containment half-width.
Z_95 = 1.959963984540054


@dataclass(frozen=True)
class Sample:
    """The ledger sample, already deflated for cross-sectional correlation."""

    r: list[float]
    fired_ms: list[int]
    symbols: list[str]
    n_raw: int
    n_eff: float
    t_deflator: float
    sd: float
    skew: float
    kurtosis: float

    @property
    def n_deflated(self) -> int:
        """Naive n scaled down by the squared t-deflator.

        ``t`` scales with ``sqrt(n)``, so an inflation factor of ``d`` in t-units
        is an overstatement of ``d**2`` in n-units. An approximation — it assumes
        constant pairwise correlation and ignores autocorrelation — but it is the
        difference between reading a cell as resolvable and reading it as noise.
        """
        return max(1, int(self.n_raw / (self.t_deflator**2)))


def load_sample(db: Path) -> Sample:
    """Read the resolved live outcome ledger and measure its effective size."""
    conn = duckdb.connect(str(db), read_only=True)
    try:
        rows = conn.execute(
            "select fired_at_ms, symbol, outcome_r "
            "from signal_alert_outcomes "
            "where outcome_r is not null and fired_at_ms is not null "
            "order by fired_at_ms"
        ).fetchall()
    finally:
        conn.close()
    if len(rows) < 2:
        raise RuntimeError(
            f"{db} holds {len(rows)} resolved outcome rows — nothing to price. "
            "An empty ledger must not silently render as a clean answer."
        )

    fired = [int(r[0]) for r in rows]
    symbols = [str(r[1]) for r in rows]
    r_vals = [float(r[2]) for r in rows]

    frame = pd.DataFrame(
        {
            "day": pd.to_datetime(fired, unit="ms", utc=True).floor("D"),
            "symbol": symbols,
            "r": r_vals,
        }
    )
    daily = frame.groupby(["symbol", "day"])["r"].mean().unstack(0)
    per_instrument = {
        str(col): daily[col].dropna()
        for col in daily.columns
        if daily[col].notna().sum() >= 2
    }
    n_eff, deflator = effective_independent_series(per_instrument)

    sd = st.pstdev(r_vals)
    mean = st.mean(r_vals)
    n = float(len(r_vals))
    skew = sum(((x - mean) / sd) ** 3 for x in r_vals) / n if sd > 0 else 0.0
    kurt = sum(((x - mean) / sd) ** 4 for x in r_vals) / n if sd > 0 else 3.0
    return Sample(
        r=r_vals,
        fired_ms=fired,
        symbols=symbols,
        n_raw=len(r_vals),
        n_eff=n_eff,
        t_deflator=deflator,
        sd=sd,
        skew=skew,
        kurtosis=kurt,
    )


def era_sharpes(
    boundaries: Sequence[EraBoundary], sample: Sample, *, min_n: int
) -> list[float]:
    """Per-alert Sharpe of every era holding at least ``min_n`` alerts.

    This is the honest empirical trial family: if the study scans eras, these are
    the trials it searches over, so their dispersion is what deflates the winner.
    """
    ordered = sorted(boundaries)
    buckets: dict[int, list[float]] = {}
    for ts, r in zip(sample.fired_ms, sample.r, strict=True):
        idx = -1
        for i, b in enumerate(ordered):
            if b.ts_ms <= ts:
                idx = i
            else:
                break
        buckets.setdefault(idx, []).append(r)
    out: list[float] = []
    for vals in buckets.values():
        if len(vals) < max(min_n, 2):
            continue
        sd = st.pstdev(vals)
        if sd <= 0.0:
            continue
        out.append(st.mean(vals) / sd)
    return out


def containment_half_width(n_obs: int, sd: float) -> float:
    """95% CI half-width on a mean R, in R per alert.

    This is standard-error arithmetic and **decides nothing on its own** — the
    verdict comes from :func:`analytics.audit_guard.powered_null`, which owns the
    containment criterion. Re-deriving the comparison inline here is exactly how
    six powered-null sites came to spell the same rule six different ways, so the
    predicate is called, never restated.
    """
    return Z_95 * sd / math.sqrt(n_obs)


def null_is_licensable(n_obs: int, sd: float, *, bar: float) -> bool:
    """Could a null be licensed at this ``n``, in the most generous case?

    Assumes a point estimate of exactly zero — the best case a real era cut could
    produce — and asks the owning predicate whether that CI is contained. If even
    this fails, no observed delta at this ``n`` can license "nothing moved"; the
    honest verdict there is INSUFFICIENT, not powered.
    """
    hw = containment_half_width(n_obs, sd)
    return powered_null(-hw, hw, bar=bar)


def build_report(sample: Sample, boundaries: Sequence[EraBoundary]) -> list[str]:
    """Render the pricing. Pure — returns lines, prints nothing."""
    spans = split_by_era(boundaries, sample.fired_ms)
    sizes = sorted((s.n_obs for s in spans), reverse=True)
    trial_srs = era_sharpes(boundaries, sample, min_n=30)
    sr_var = st.variance(trial_srs) if len(trial_srs) >= 2 else 0.0

    lines = [
        "=" * 78,
        "ERA STUDY - POWER PRICING (ledger scope, per-alert units)",
        "=" * 78,
        "",
        f"Sample        n={sample.n_raw:,} resolved alerts, "
        f"mean={st.mean(sample.r):+.4f}R  sd={sample.sd:.4f}R",
        f"              skew={sample.skew:+.3f}  kurtosis={sample.kurtosis:.3f} "
        "(both fed to the PSR - a fat left tail is not free)",
        f"Cross-section n_eff={sample.n_eff:.2f} independent series, "
        f"t-deflator={sample.t_deflator:.3f}x",
        f"              => effective n={sample.n_deflated:,}, not {sample.n_raw:,}. "
        "Pooled alert rows are NOT independent observations.",
        "              ⚠ This corrects CROSS-SYMBOL correlation only. Alerts also "
        "cluster in time within a symbol",
        "                (multiple timeframes and strategies firing on one move), "
        "which this does not deflate, so",
        "                every effective n below is an UPPER bound and every bar an "
        "under-estimate of the true one.",
        "",
        f"Eras          {len(spans)} occupied of {len(boundaries)} boundaries; "
        f"largest={sizes[0]:,} ({sizes[0] / sample.n_raw:.0%})  "
        f"median={st.median(sizes):.0f}",
        f"              n>=100: {sum(1 for x in sizes if x >= 100)}   "
        f"n>=30: {sum(1 for x in sizes if x >= 30)}",
        f"Trial family  {len(trial_srs)} era Sharpes at n>=30, "
        f"dispersion var={sr_var:.5f} (empirical, not assumed)",
        "",
        "-" * 78,
        "LEG A - the GATE bar: minimum per-alert Sharpe for DSR >= 0.95,",
        "        and the same bar restated in R per alert (Sharpe x sd).",
        "-" * 78,
        "",
        f"{'era n (raw)':>12} {'n eff':>7} | "
        + " ".join(f"{f'k={k}':>16}" for k in (1, 12, 27, 44)),
    ]

    for raw_n in (sizes[0], 500, 250, 100, 50):
        if raw_n > sizes[0]:
            continue
        eff = max(1, int(raw_n / (sample.t_deflator**2)))
        cells = []
        for k in (1, 12, 27, 44):
            sr = required_sharpe(
                eff,
                n_trials=k,
                sr_variance=sr_var,
                skew=sample.skew,
                kurtosis=sample.kurtosis,
            )
            cells.append(f"{sr:.3f}sr/{sr * sample.sd:+.3f}R".rjust(16))
        lines.append(f"{raw_n:>12,} {eff:>7,} | " + " ".join(cells))

    lines += [
        "",
        "  k=1  is the assumption-free floor: one pre-registered comparison, no",
        "       deflation term at all. A target that misses it cannot be rescued.",
        "  k=44 is the honest cost of SCANNING every occupied era.",
        "",
        "-" * 78,
        "LEG B - the NULL bar: 95% CI half-width on an era's mean R.",
        "        A 'nothing moved' verdict is only powered if this fits INSIDE",
        "        the economic bar. |delta| < MDE is NOT the test.",
        "-" * 78,
        "",
        f"{'era n (raw)':>12} {'n eff':>7} {'CI half-width':>15}  "
        + "  ".join(f"vs {b:.2f}R bar" for b in CONTAINMENT_BARS_R),
    ]
    for raw_n in (sizes[0], 500, 250, 100, 50):
        if raw_n > sizes[0]:
            continue
        eff = max(1, int(raw_n / (sample.t_deflator**2)))
        hw = containment_half_width(eff, sample.sd)
        verdicts = "  ".join(
            f"{'CONTAINED' if null_is_licensable(eff, sample.sd, bar=b) else f'{hw / b:.1f}x TOO WIDE':>14}"
            for b in CONTAINMENT_BARS_R
        )
        lines.append(f"{raw_n:>12,} {eff:>7,} {hw:>14.4f}R  {verdicts}")

    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("analytics.db"))
    args = parser.parse_args(argv)

    boundaries = load_boundaries(scopes=("ledger",))
    sample = load_sample(args.db)
    print("\n".join(build_report(sample, boundaries)))
    return 0


if __name__ == "__main__":
    from utils.stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())

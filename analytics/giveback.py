"""Give-back / heat-and-run — does the live book hand back open profit before it exits?

Pre-registered 2026-08-17
(``docs/superpowers/specs/2026-08-17-giveback-heat-and-run-prereg.md``), computed here.

**DESCRIPTIVE.** It measures a property of the existing record, claims no edge and
searches no construction, so it carries no trial count and no DSR/PBO/boot_lo gate.
The gate binds the moment anyone proposes a stop or trail rule FROM this output —
that is a new construction fitted to the same data and needs its own
pre-registration.

Four things this module is careful about, each a filed trap:

1. **The excursion window mirrors the resolver exactly** — ``open_time >
   candle_ts_ms`` and ``open_time <= outcome_filled_at_ms``, the same
   strictly-post-signal scan as
   :func:`analytics.signal.outcome_backfill._scan_forward`, whose entry fills at
   the OPEN of the first post-signal bar. Counting the signal candle's own bar
   would admit excursion that happened before entry existed — the look-ahead
   class that voided the structural-touch BUILD — and would leave ``MFE_R`` and
   ``outcome_r`` reading two different bar populations, which is incoherent under
   ``giveback_R = MFE_R - outcome_r``.
2. **``outcome_r`` changes BASIS mid-ledger** at e5d92bb. ``MFE_R`` is a raw price
   quantity, so subtracting a mixed-basis number from it is a category error;
   rows are restated through
   :func:`portfolio.replay.restate_on_resolution_clock`, which splits on the
   resolution clock rather than the candle clock.
3. **R-units inherit stop width by construction**, and modelled drag scales
   inversely with stop width, so no pooled cross-detector average is a headline —
   :func:`summarise_by` stratifies.
4. **A row whose bars are missing is counted, never dropped.** The join is a LEFT
   join and unmeasurable rows surface as ``Summary.unmeasurable``; silently
   dropping them would report the sample as cleaner than it is.

⚠ ``MFE_R`` is read from BAR EXTREMES, so it is the excursion an omniscient exit
would have caught — an upper bound on what any real rule could capture, never
quotable as forgone profit. Intrabar path is unknown, so a bar that touches both
the stop and the favourable extreme cannot be ordered; those rows are counted in
``Summary.intrabar_ambiguous`` rather than silently resolved.

⚠ The spec's ``giveback_R = MFE_R - outcome_r`` and its stated invariant ("losers
that never went green have ``giveback_R = 0`` by construction") are consistent
only if ``MFE_R`` is the UNFLOORED favourable extreme; flooring it at zero breaks
the invariant. The unfloored form is used here. It bites only the
whole-population line — the primary stat is conditional on ``MFE_R >= X``, where
every row went green by at least X.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import duckdb
import numpy as np
import numpy.typing as npt

from analytics.research_guards.bootstrap import block_bootstrap_ci
from portfolio.replay import restate_on_resolution_clock

# Pre-registered before any number was computed. Chosen from doctrine, not data:
# 1R is where "move to breakeven" is the standard operator action and the level
# the harvested Stream B mechanics speak to. Changing it is a re-registration.
DEFAULT_X_R = 1.0

_ROWS_SQL = """
WITH led AS (
    SELECT signal_id, symbol, tf, strategy, direction, candle_ts_ms,
           outcome_filled_at_ms, entry_price, sl_price, outcome, outcome_r
    FROM signal_alert_outcomes
    WHERE outcome IN ('win', 'loss', 'expired')
      AND outcome_r IS NOT NULL
      AND candle_ts_ms IS NOT NULL
      AND outcome_filled_at_ms IS NOT NULL
      AND entry_price IS NOT NULL
      AND sl_price IS NOT NULL
)
SELECT l.signal_id, l.symbol, l.tf, l.strategy, l.direction, l.candle_ts_ms,
       l.outcome_filled_at_ms, l.entry_price, l.sl_price, l.outcome, l.outcome_r,
       max(b.high) AS max_high,
       min(b.low) AS min_low,
       count(b.open_time) AS bars,
       max(CASE WHEN b.open_time = l.outcome_filled_at_ms THEN b.high END) AS exit_high,
       min(CASE WHEN b.open_time = l.outcome_filled_at_ms THEN b.low END) AS exit_low
FROM led l
LEFT JOIN ohlcv b
       ON b.symbol = l.symbol
      AND b.timeframe = l.tf
      AND b.open_time > l.candle_ts_ms
      AND b.open_time <= l.outcome_filled_at_ms
GROUP BY l.signal_id, l.symbol, l.tf, l.strategy, l.direction, l.candle_ts_ms,
         l.outcome_filled_at_ms, l.entry_price, l.sl_price, l.outcome, l.outcome_r
ORDER BY l.candle_ts_ms
"""


@dataclass(frozen=True)
class GivebackRow:
    """One resolved ledger row with its excursion measured against its own stop."""

    signal_id: str
    symbol: str
    tf: str
    strategy: str
    direction: str
    candle_ts_ms: int
    outcome_filled_at_ms: int
    entry_price: float
    sl_price: float
    outcome: str
    outcome_r: float
    """Restated onto the post-parity net basis unless ``restate_cost_basis=False``."""
    r_unit: float
    """``abs(entry_price - sl_price)`` — stop width in price, kept explicit so it
    never hides inside a pooled average."""
    sl_pct: float
    """Stop width as a fraction of entry price. The stratification axis."""
    bars: int
    mfe_r: float | None
    """Unfloored favourable excursion in R. ``None`` when no bars covered the hold."""
    giveback_r: float | None
    intrabar_ambiguous: bool
    """The exit bar both reached the stop and posted a favourable extreme >= X, so
    its path order is unknowable from bar data."""

    @property
    def measurable(self) -> bool:
        return self.mfe_r is not None


def load_giveback_rows(
    conn: duckdb.DuckDBPyConnection,
    *,
    x_r: float = DEFAULT_X_R,
    restate_cost_basis: bool = True,
) -> list[GivebackRow]:
    """Measure MFE per resolved ledger row against that row's own stop width.

    ``restate_cost_basis=False`` reproduces the pre-2026-08-14 mixture and is not
    a like-for-like series; it exists to reproduce an old figure, nothing else.
    """
    out: list[GivebackRow] = []
    for r in conn.execute(_ROWS_SQL).fetchall():
        entry = float(r[7])
        sl = float(r[8])
        r_unit = abs(entry - sl)
        if r_unit <= 0.0 or entry <= 0.0:
            # A zero-risk row has undefined R — the same convention the resolver
            # and restate_gross_r use. Excluded rather than counted as 0.0.
            continue
        filled_at = int(r[6])
        outcome_r = float(r[10])
        if restate_cost_basis:
            outcome_r = restate_on_resolution_clock(outcome_r, entry, sl, filled_at)
        direction = str(r[4])
        max_high = r[11]
        min_low = r[12]
        exit_high = r[14]
        exit_low = r[15]

        mfe_r: float | None = None
        ambiguous = False
        if direction == "long" and max_high is not None:
            mfe_r = (float(max_high) - entry) / r_unit
            ambiguous = (
                exit_low is not None
                and float(exit_low) <= sl
                and exit_high is not None
                and (float(exit_high) - entry) / r_unit >= x_r
            )
        elif direction == "short" and min_low is not None:
            mfe_r = (entry - float(min_low)) / r_unit
            ambiguous = (
                exit_high is not None
                and float(exit_high) >= sl
                and exit_low is not None
                and (entry - float(exit_low)) / r_unit >= x_r
            )

        out.append(
            GivebackRow(
                signal_id=str(r[0]),
                symbol=str(r[1]),
                tf=str(r[2]),
                strategy=str(r[3]),
                direction=direction,
                candle_ts_ms=int(r[5]),
                outcome_filled_at_ms=filled_at,
                entry_price=entry,
                sl_price=sl,
                outcome=str(r[9]),
                outcome_r=outcome_r,
                r_unit=r_unit,
                sl_pct=r_unit / entry,
                bars=int(r[13]),
                mfe_r=mfe_r,
                giveback_r=None if mfe_r is None else mfe_r - outcome_r,
                intrabar_ambiguous=ambiguous,
            )
        )
    return out


@dataclass(frozen=True)
class Summary:
    """The pre-registered reported quantities for one population."""

    label: str
    n: int
    """Rows in the population before the measurability filter."""
    unmeasurable: int
    """Rows whose hold window covered no OHLCV bars — a data gap, not a zero."""
    n_measured: int
    share_non_positive: float | None
    """Share of the population finishing at ``outcome_r <= 0``."""
    median_giveback_r: float | None
    iqr_giveback_r: tuple[float, float] | None
    median_capture_ratio: float | None
    """Median ``outcome_r / MFE_R``. Undefined rows (``MFE_R`` at or below 0) are
    dropped from this one statistic and counted in ``capture_dropped``."""
    capture_dropped: int
    intrabar_ambiguous: int
    ci_lo: float | None = None
    ci_hi: float | None = None
    """Bootstrap CI on ``median_giveback_r``. Present only when requested."""

    @property
    def min_licensable_bar(self) -> float | None:
        """Smallest ``bar`` against which a null claim could be licensed.

        A negative claim needs the CI to sit strictly inside ``±bar``
        (:func:`analytics.audit_guard.powered_null`), so no bar at or below
        ``max(|ci_lo|, |ci_hi|)`` can license one. Reported instead of picking a
        bar here: the spec pre-registers no bar for this descriptive study, and
        choosing one after seeing the interval is the move that produced six
        badly-filed nulls in this repo.
        """
        if self.ci_lo is None or self.ci_hi is None:
            return None
        if not (math.isfinite(self.ci_lo) and math.isfinite(self.ci_hi)):
            return None
        return max(abs(self.ci_lo), abs(self.ci_hi))


def _median(values: Sequence[float]) -> float | None:
    return float(np.median(values)) if values else None


def summarise(
    rows: Sequence[GivebackRow],
    *,
    label: str,
    x_r: float | None = DEFAULT_X_R,
    ci: bool = False,
    n_boot: int = 10_000,
    seed: int | None = 20260817,
) -> Summary:
    """Reduce ``rows`` to the pre-registered quantities.

    ``x_r`` conditions the population on ``MFE_R >= x_r`` (the primary stat);
    pass ``None`` for the whole-population line, which is reported beside the
    primary one and never as the headline.
    """
    measurable = [r for r in rows if r.mfe_r is not None]
    unmeasurable = len(rows) - len(measurable)
    pop = (
        measurable
        if x_r is None
        else [r for r in measurable if r.mfe_r is not None and r.mfe_r >= x_r]
    )
    if not pop:
        return Summary(
            label=label,
            n=len(rows),
            unmeasurable=unmeasurable,
            n_measured=0,
            share_non_positive=None,
            median_giveback_r=None,
            iqr_giveback_r=None,
            median_capture_ratio=None,
            capture_dropped=0,
            intrabar_ambiguous=0,
        )

    givebacks = [r.giveback_r for r in pop if r.giveback_r is not None]
    captures = [
        r.outcome_r / r.mfe_r for r in pop if r.mfe_r is not None and r.mfe_r > 0.0
    ]
    lo = hi = None
    if ci and len(givebacks) >= 2:
        interval = block_bootstrap_ci(
            np.asarray(givebacks, dtype=np.float64),
            _median_stat,
            n_boot=n_boot,
            seed=seed,
        )
        lo, hi = interval.lo, interval.hi

    return Summary(
        label=label,
        n=len(rows),
        unmeasurable=unmeasurable,
        n_measured=len(pop),
        share_non_positive=sum(1 for r in pop if r.outcome_r <= 0.0) / len(pop),
        median_giveback_r=_median(givebacks),
        iqr_giveback_r=(
            float(np.quantile(givebacks, 0.25)),
            float(np.quantile(givebacks, 0.75)),
        )
        if givebacks
        else None,
        median_capture_ratio=_median(captures),
        capture_dropped=len(pop) - len(captures),
        intrabar_ambiguous=sum(1 for r in pop if r.intrabar_ambiguous),
        ci_lo=lo,
        ci_hi=hi,
    )


def _median_stat(arr: npt.NDArray[np.float64]) -> float:
    return float(np.median(arr))


def summarise_by(
    rows: Sequence[GivebackRow],
    key: Callable[[GivebackRow], str],
    *,
    x_r: float | None = DEFAULT_X_R,
    min_n: int = 20,
) -> list[Summary]:
    """Stratified summaries, one per ``key`` value, largest population first.

    Stratification is not presentational. R-units inherit stop width by
    construction and modelled drag scales inversely with it, so a pooled
    cross-detector give-back average carries a bias rather than a level shift.
    Cells below ``min_n`` are still returned — dropping them would hide the
    thinness that makes them unreadable.
    """
    buckets: dict[str, list[GivebackRow]] = {}
    for row in rows:
        buckets.setdefault(key(row), []).append(row)
    out = [summarise(v, label=k, x_r=x_r) for k, v in buckets.items()]
    return sorted(out, key=lambda s: (-s.n_measured, s.label))


def sl_pct_bucket(row: GivebackRow) -> str:
    """Stop-width bucket. 78% of the ledger runs the hardcoded flat 2% SL, so the
    boundaries are placed to isolate that mass rather than to split it evenly."""
    pct = row.sl_pct * 100.0
    if pct < 1.0:
        return "<1%"
    if pct < 1.9:
        return "1.0-1.9%"
    if pct <= 2.1:
        return "~2% (flat-SL detectors)"
    if pct <= 4.0:
        return "2.1-4%"
    return ">4%"

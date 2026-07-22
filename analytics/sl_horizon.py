"""ST9 / H11 SL-horizon audit — pure library.

Six candle detectors (`doji`, `engulfing`, `hammer_hanging_man`, `inside_bar`,
`morning_evening_star`, `pin_bar`) hard-code ``sl_pct = 0.02`` at every
timeframe, while every other active detector derives a TF-adaptive structural
SL. This module re-resolves the same signals under an ATR-scaled stop grid so a
pre-committed verdict can say whether the family's graveyard is an artifact of a
dimensionally wrong stop.

Pure: no DB, no IO, no network. The DB front door is
``tools/sl_horizon_audit.py``. Design:
``docs/superpowers/specs/2026-07-21-st9-sl-horizon-audit-design.md``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.audit_guard import AuditCell, CellVerdict, evaluate_audit_cells
from analytics.backtest.engine import _compute_atr14
from analytics.exits.policies import fixed as fixed_policy
from analytics.exits.replay import replay_exits
from analytics.research_guards import cscv_pbo, deflated_sharpe_ratio
from analytics.signal.outcome_backfill import DEFAULT_MAX_HOLD_BARS

# A-priori and fixed. Brackets the current effective ratio at 1h (~3.6), 4h
# (~1.7) and 1d (~0.6); sits entirely below 15m (~7.5), where every arm is a
# tightening. Never re-centred in response to results.
DEFAULT_MULTIPLIERS: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0, 3.0)

#: Column/arm name for the unmodified flat-2% arm.
BASELINE_ARM = "flat_2pct"

#: The detectors this audit covers.
FAMILY: tuple[str, ...] = (
    "doji",
    "engulfing",
    "hammer_hanging_man",
    "inside_bar",
    "morning_evening_star",
    "pin_bar",
)


def arm_label(k: float) -> str:
    """Stable column name for the ``k × ATR14`` arm."""
    return f"atr_{k:g}"


@dataclass(frozen=True)
class SLGridConfig:
    """A-priori parameters for one audit run. Frozen; never tuned mid-run."""

    multipliers: tuple[float, ...] = DEFAULT_MULTIPLIERS
    baseline_pct: float = 0.02
    max_hold_bars_by_tf: Mapping[str, int] = field(
        default_factory=lambda: dict(DEFAULT_MAX_HOLD_BARS)
    )
    fee_pct: float = 0.0005
    slippage_bps: float = 2.0
    bar: float = 0.05
    alpha: float = 0.05
    min_n: int = 30
    n_boot: int = 2000
    seed: int = 12345

    def __post_init__(self) -> None:
        if not self.multipliers:
            raise ValueError("multipliers must be non-empty")
        if any(k <= 0.0 for k in self.multipliers):
            raise ValueError(f"multipliers must all be > 0, got {self.multipliers}")
        if self.baseline_pct <= 0.0:
            raise ValueError(f"baseline_pct must be > 0, got {self.baseline_pct}")
        if self.min_n < 2:
            raise ValueError(f"min_n must be >= 2, got {self.min_n}")

    @property
    def round_trip_cost_pct(self) -> float:
        """Round-trip cost as a fraction of notional (both legs, fee + slippage)."""
        return 2.0 * self.fee_pct + 2.0 * (self.slippage_bps / 10_000.0)


def levels_from_sl_dist(
    entry: float, direction: str, *, sl_dist: float, tp_r: float
) -> tuple[float, float]:
    """Return ``(sl_price, tp_price)`` for a stop ``sl_dist`` away from ``entry``."""
    if sl_dist <= 0.0:
        raise ValueError(f"sl_dist must be > 0, got {sl_dist}")
    if tp_r <= 0.0:
        raise ValueError(f"tp_r must be > 0, got {tp_r}")
    if direction == "long":
        return entry - sl_dist, entry + tp_r * sl_dist
    if direction == "short":
        return entry + sl_dist, entry - tp_r * sl_dist
    raise ValueError(f"unknown direction: {direction!r}")


def baseline_levels(
    entry: float, direction: str, *, baseline_pct: float, tp_r: float
) -> tuple[float, float]:
    """The unmodified flat-percentage arm the six detectors ship today."""
    return levels_from_sl_dist(
        entry, direction, sl_dist=entry * baseline_pct, tp_r=tp_r
    )


def counterfactual_levels(
    entry: float, direction: str, *, atr: float, k: float, tp_r: float
) -> tuple[float, float]:
    """The ``k × ATR14`` arm. ``tp_r`` is pinned by the caller, never swept."""
    return levels_from_sl_dist(entry, direction, sl_dist=k * atr, tp_r=tp_r)


def atr_by_open_time(
    ohlcv: pd.DataFrame, open_times: Iterable[int]
) -> dict[int, float | None]:
    """ATR14 at each requested signal bar, keyed by that bar's ``open_time``.

    Delegates to the engine's ``_compute_atr14`` so the audit and the live path
    cannot disagree on what ATR14 means. Returns ``None`` for an ``open_time``
    absent from ``ohlcv`` and for the first bar (no prior close for a true
    range) — callers drop those signals rather than substituting a value.
    """
    wanted = [int(t) for t in open_times]
    if ohlcv is None or ohlcv.empty:
        return dict.fromkeys(wanted)

    highs = ohlcv["high"].to_numpy(dtype=np.float64)
    lows = ohlcv["low"].to_numpy(dtype=np.float64)
    closes = ohlcv["close"].to_numpy(dtype=np.float64)
    position = {int(t): i for i, t in enumerate(ohlcv["open_time"].to_numpy())}

    out: dict[int, float | None] = {}
    for t in wanted:
        idx = position.get(t)
        out[t] = None if idx is None else _compute_atr14(highs, lows, closes, idx)
    return out


EntryConvention = Literal["engine", "live"]

#: Entry/window conventions. These differ between substrates and the difference
#: is load-bearing — see the fidelity checks in `tools/sl_horizon_audit.py`.
ENTRY_CONVENTIONS: tuple[str, ...] = ("engine", "live")


def window_for_signal(
    ohlcv: pd.DataFrame,
    *,
    sig_idx: int,
    convention: str,
    max_hold_bars: int,
) -> tuple[
    float,
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
]:
    """Return ``(entry_price, highs, lows, closes)`` for one signal's forward window.

    ``convention``:

    * ``"engine"`` — mirrors ``analytics/backtest/engine.py``: entry is the OPEN
      of bar ``sig_idx + 1`` and the scan window starts at that same bar, so a
      trade can stop out on its own entry bar.
    * ``"live"`` — mirrors ``analytics/signal/outcome_backfill.py``: entry is the
      CLOSE of the signal bar and the window is the bars strictly after it.

    The window is truncated to ``max_hold_bars``; expiry then falls out of window
    exhaustion, so no explicit time-stop policy is needed.
    """
    if convention not in ENTRY_CONVENTIONS:
        raise ValueError(
            f"unknown convention {convention!r}; expected one of {ENTRY_CONVENTIONS}"
        )

    highs = ohlcv["high"].to_numpy(dtype=np.float64)
    lows = ohlcv["low"].to_numpy(dtype=np.float64)
    closes = ohlcv["close"].to_numpy(dtype=np.float64)
    opens = ohlcv["open"].to_numpy(dtype=np.float64)

    start = sig_idx + 1
    if convention == "engine":
        entry = float(opens[start]) if start < len(opens) else float("nan")
    else:
        entry = float(closes[sig_idx])

    stop = start + max_hold_bars
    return entry, highs[start:stop], lows[start:stop], closes[start:stop]


@dataclass(frozen=True)
class ArmResult:
    """One signal resolved under one arm, net of costs."""

    outcome: str
    realized_r: float
    exit_bar: int
    sl_dist_pct: float
    cost_r: float
    funding_r: float
    net_r: float


def resolve_arm(
    highs: npt.NDArray[np.float64],
    lows: npt.NDArray[np.float64],
    closes: npt.NDArray[np.float64],
    *,
    direction: str,
    entry: float,
    sl_price: float,
    tp_r: float,
    max_hold_bars: int,
    round_trip_cost_pct: float,
    funding_r: float,
) -> ArmResult | None:
    """Resolve one signal under one arm and net out costs.

    Returns ``None`` when the signal is unresolvable (empty forward window or
    zero risk) — the caller drops it from **every** arm so the paired comparison
    stays row-aligned.

    ``net_r = realized_r − cost_r − funding_r``, matching the P0b honest-cost
    convention in ``analytics/signal/outcome_backfill.py``. ``cost_r`` converts a
    cash cost into R by dividing by the risk, so a tighter stop is charged more
    R for the same trade — which is exactly the effect this audit must not hide.
    """
    risk = abs(entry - sl_price)
    if risk <= 0.0 or len(highs) == 0 or not np.isfinite(entry):
        return None

    try:
        outcome = replay_exits(
            highs,
            lows,
            closes,
            direction=direction,
            entry=entry,
            sl_price=sl_price,
            policy=fixed_policy(tp_r=tp_r, max_hold_bars=max_hold_bars),
        )
    except ValueError:
        return None

    cost_r = round_trip_cost_pct * entry / risk
    return ArmResult(
        outcome=outcome.outcome,
        realized_r=outcome.realized_r,
        exit_bar=outcome.exit_bar,
        sl_dist_pct=risk / entry,
        cost_r=cost_r,
        funding_r=funding_r,
        net_r=outcome.realized_r - cost_r - funding_r,
    )


#: Identity of one signal. Every arm resolves the same set of these.
SIGNAL_KEY: list[str] = ["symbol", "tf", "strategy", "direction", "open_time"]


def build_paired_table(arm_rows: pd.DataFrame, *, arms: Sequence[str]) -> pd.DataFrame:
    """Pivot long arm rows to one row per signal with one ``net_r`` column per arm.

    A signal that failed to resolve under **any** arm is dropped from **all**
    arms. Zero-filling instead would silently credit the missing arm with a
    flat outcome and bias the paired difference.
    """
    if arm_rows.empty:
        return pd.DataFrame(columns=[*SIGNAL_KEY, *arms])

    wide = arm_rows.pivot_table(
        index=SIGNAL_KEY, columns="arm", values="net_r", aggfunc="first"
    )
    missing = [a for a in arms if a not in wide.columns]
    for arm in missing:
        wide[arm] = np.nan
    wide = wide[list(arms)].dropna(how="any")
    return wide.reset_index()


def describe_horizon(
    arm_rows: pd.DataFrame, *, arm: str = BASELINE_ARM
) -> pd.DataFrame:
    """Per (strategy, tf) descriptive horizon table for one arm.

    Columns: ``strategy``, ``tf``, ``n``, ``avg_r``, ``median_bars``,
    ``expiry_rate``, ``median_sl_pct``, and ``median_sl_atr`` when the caller
    supplied an ``atr_pct`` column (ATR14 as a fraction of entry price).
    """
    subset = arm_rows[arm_rows["arm"] == arm]
    if subset.empty:
        return pd.DataFrame(
            columns=[
                "strategy",
                "tf",
                "n",
                "avg_r",
                "median_bars",
                "expiry_rate",
                "median_sl_pct",
                "median_sl_atr",
            ]
        )

    records: list[dict[str, object]] = []
    for (strategy, tf), grp in subset.groupby(["strategy", "tf"], sort=True):
        sl_atr = float("nan")
        if "atr_pct" in grp.columns:
            ratio = grp["sl_dist_pct"] / grp["atr_pct"]
            sl_atr = float(ratio.median())
        records.append(
            {
                "strategy": strategy,
                "tf": tf,
                "n": int(len(grp)),
                "avg_r": float(grp["net_r"].mean()),
                "median_bars": float(grp["exit_bar"].median()),
                "expiry_rate": float((grp["outcome"] == "expired").mean()),
                "median_sl_pct": float(grp["sl_dist_pct"].median()),
                "median_sl_atr": sl_atr,
            }
        )
    return pd.DataFrame(records)


DECISION_SUSPECT = "SUSPECT"
DECISION_CONFIRMED_BAD = "CONFIRMED-BAD"
DECISION_NO_DIFFERENCE = "NO-DIFFERENCE"
DECISION_INSUFFICIENT = "INSUFFICIENT"

_DSR_FLOOR = 0.95
_PBO_CEILING = 0.5


@dataclass(frozen=True)
class SLVerdict:
    """Pre-committed verdict for one (strategy × TF) cell."""

    strategy: str
    tf: str
    decision: str
    n: int
    baseline_avg_r: float
    best_k: float | None
    best_lift: float | None
    ci_lo: float | None
    ci_hi: float | None
    adj_pvalue: float | None
    dsr: float | None
    pbo: float | None
    reasons: list[str]


def _k_from_arm(arm: str) -> float:
    """Inverse of :func:`arm_label`."""
    return float(arm.removeprefix("atr_"))


def _sharpe(arr: npt.NDArray[np.float64]) -> float:
    if arr.size < 2:
        return 0.0
    sd = float(np.std(arr, ddof=1))
    return 0.0 if sd == 0.0 else float(np.mean(arr)) / sd


def evaluate_sl_grid(
    paired: pd.DataFrame, *, arms: Sequence[str], cfg: SLGridConfig
) -> list[SLVerdict]:
    """Pre-committed SUSPECT / CONFIRMED-BAD / NO-DIFFERENCE / INSUFFICIENT verdicts.

    The statistic is the **paired** per-signal lift ``net_r[arm] − net_r[baseline]``.
    Because both arms ran on the identical signal set, this is far better powered
    than a two-sample comparison and is immune to the signal population's own
    quality. Feeding the difference series to ``evaluate_audit_cells`` as
    ``supp_r`` turns its ±bar + Holm machinery into a paired test with no change
    to ``analytics/audit_guard.py``.

    One ``evaluate_audit_cells`` call covers every (strategy × TF × k) cell in
    the run, so the Holm family is shared across the whole substrate — one family
    per substrate, never pooled across substrates.
    """
    if paired.empty:
        return []

    groups = list(paired.groupby(["strategy", "tf"], sort=True))

    # Build one AuditCell per (strategy, tf, arm); the whole list is one family.
    cells: list[AuditCell] = []
    index: list[tuple[int, str]] = []  # (group position, arm)
    for gi, (_, grp) in enumerate(groups):
        for arm in arms:
            diff = (grp[arm] - grp[BASELINE_ARM]).to_numpy(dtype=np.float64)
            cells.append(AuditCell(label=f"g{gi}|{arm}", supp_r=diff.tolist()))
            index.append((gi, arm))

    verdicts_flat = evaluate_audit_cells(
        cells,
        bar=cfg.bar,
        alpha=cfg.alpha,
        min_n=cfg.min_n,
        n_boot=cfg.n_boot,
        seed=cfg.seed,
        enable_concentrate=False,
    )

    by_group: dict[int, list[tuple[str, CellVerdict]]] = {}
    for (gi, arm), cv in zip(index, verdicts_flat, strict=True):
        by_group.setdefault(gi, []).append((arm, cv))

    out: list[SLVerdict] = []
    for gi, ((strategy, tf), grp) in enumerate(groups):
        n = int(len(grp))
        baseline_avg = float(grp[BASELINE_ARM].mean())
        reasons: list[str] = []

        if n < cfg.min_n:
            out.append(
                SLVerdict(
                    strategy=str(strategy),
                    tf=str(tf),
                    decision=DECISION_INSUFFICIENT,
                    n=n,
                    baseline_avg_r=baseline_avg,
                    best_k=None,
                    best_lift=None,
                    ci_lo=None,
                    ci_hi=None,
                    adj_pvalue=None,
                    dsr=None,
                    pbo=None,
                    reasons=[f"n={n} < min_n={cfg.min_n}"],
                )
            )
            continue

        # Candidates: arms whose paired-lift CI cleared +bar. In audit_guard's
        # (counterintuitive) vocabulary a reliably POSITIVE slice returns
        # "DISABLE" (ci_lo >= +bar); "ENABLE" is the reliably-NEGATIVE branch
        # (ci_hi <= -bar). We want arms that BEAT baseline, i.e. positive lift,
        # so we filter on "DISABLE". enable_concentrate=False guarantees a
        # positive cell never resolves to CONCENTRATE, so DISABLE is unambiguous.
        candidates = [(arm, cv) for arm, cv in by_group[gi] if cv.decision == "DISABLE"]

        if not candidates:
            decision = (
                DECISION_CONFIRMED_BAD
                if baseline_avg <= 0.0
                else DECISION_NO_DIFFERENCE
            )
            reasons.append("no arm cleared the +bar CI test")
            reasons.append(f"baseline avg_r={baseline_avg:.4f}")
            out.append(
                SLVerdict(
                    strategy=str(strategy),
                    tf=str(tf),
                    decision=decision,
                    n=n,
                    baseline_avg_r=baseline_avg,
                    best_k=None,
                    best_lift=None,
                    ci_lo=None,
                    ci_hi=None,
                    adj_pvalue=None,
                    dsr=None,
                    pbo=None,
                    reasons=reasons,
                )
            )
            continue

        # Winning k: largest mean lift; ties break toward the LARGER k.
        best_arm, best_cv = max(
            candidates,
            key=lambda pair: (float(pair[1].supp_avg or 0.0), _k_from_arm(pair[0])),
        )

        # DSR / PBO over the k-grid family for this cell.
        diffs = {
            arm: (grp[arm] - grp[BASELINE_ARM]).to_numpy(dtype=np.float64)
            for arm in arms
        }
        trial_srs = [_sharpe(v) for v in diffs.values()]
        dsr = deflated_sharpe_ratio(_sharpe(diffs[best_arm]), n, trial_srs=trial_srs)
        pbo: float | None
        if len(arms) < 2:
            pbo = None
            reasons.append("PBO skipped — needs >= 2 arms")
        else:
            matrix = np.column_stack([diffs[a] for a in arms])
            pbo = float(cscv_pbo(matrix).pbo)

        gates_ok = dsr >= _DSR_FLOOR and (pbo is None or pbo <= _PBO_CEILING)
        decision = DECISION_SUSPECT if gates_ok else DECISION_NO_DIFFERENCE
        if not gates_ok:
            reasons.append(
                f"lift cleared the bar but overfit gates failed "
                f"(dsr={dsr:.3f} < {_DSR_FLOOR} or pbo={pbo} > {_PBO_CEILING})"
            )

        out.append(
            SLVerdict(
                strategy=str(strategy),
                tf=str(tf),
                decision=decision,
                n=n,
                baseline_avg_r=baseline_avg,
                best_k=_k_from_arm(best_arm),
                best_lift=float(best_cv.supp_avg or 0.0),
                ci_lo=best_cv.ci_lo,
                ci_hi=best_cv.ci_hi,
                adj_pvalue=best_cv.adj_pvalue,
                dsr=dsr,
                pbo=pbo,
                reasons=reasons,
            )
        )
    return out

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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.backtest.engine import _compute_atr14
from analytics.exits.policies import fixed as fixed_policy
from analytics.exits.replay import replay_exits
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

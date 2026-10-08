"""Pluggable exit-replay engine (exit spec §4).

Generalizes the fixed SL/TP/time-expiry walk (`engine.py` / `_scan_forward`)
into a single evaluator parameterized by an `ExitPolicyConfig`. Given the OHLCV
window strictly after entry and the alert's original entry + `sl_price`, it walks
bars and returns the re-resolved `(outcome, realized_r, exit_bar)` under the
policy. Both policy #0 (fixed) and the composite run through this one function,
so the A/B is apples-to-apples (same entries, same SL).

Conventions (anti-bias, exit spec §4):

  - **adverse-first** on any same-bar ambiguity: the stop is checked before the
    profit targets, so a bar that spans both SL and TP resolves as a stop-out.
    Pass `fine_bars` to resolve such a bar from finer bars instead (#924); a
    tie that survives at the finer resolution is still resolved adverse-first.
  - **no look-ahead in the trail/BE level:** arming breakeven at bar *i* moves
    the stop to entry only from bar *i+1*; the bar that arms it cannot also be
    stopped at the new (BE) level. Inside a fine-bar walk the same rule applies
    at the fine resolution.
  - **partials** accumulate position-weighted R: Σ legᵢ_frac × legᵢ_R.

R is measured in units of the original risk |entry − sl|, so the SL sits at
R = −1 and breakeven at R = 0 by construction. Excursions are gross of costs
(price-path geometry); cost-netting is a downstream concern.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from analytics.exits.policies import ExitPolicyConfig

_EPS = 1e-12

# Bar index -> that bar's (highs, lows, closes) at a finer resolution, or None
# when the finer bars are not held (the tie then stays adverse-first).
FineBars = Callable[[int], tuple[np.ndarray, np.ndarray, np.ndarray] | None]


@dataclass(frozen=True)
class ExitOutcome:
    """Re-resolved exit for one alert under a policy.

    `outcome` is the mechanism that closed the *remaining* position:
    "win" (tp_r), "loss" (SL at −1R), "breakeven" (BE stop at 0R), or
    "expired" (time-stop mark-to-market). `realized_r` is position-weighted
    across the partial + remaining legs. `exit_bar` is the 0-based index into
    the window of the closing bar.

    Tie counters: `ambiguous_bars` is how many walked bars touched both the stop
    in force and an unconsumed favourable level; `resolved_bars` how many of
    those were re-walked on fine bars; `residual_ties` how many fine bars inside
    those walks were themselves ambiguous and fell back to adverse-first.
    """

    outcome: str
    realized_r: float
    exit_bar: int
    partial_taken: bool
    ambiguous_bars: int = 0
    resolved_bars: int = 0
    residual_ties: int = 0


@dataclass
class _State:
    stop_r: float = -1.0
    be_armed: bool = False
    partial_taken: bool = False
    remaining: float = 1.0
    realized: float = 0.0


def _excursions(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    direction: str,
    entry: float,
    risk: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if direction == "long":
        return (highs - entry) / risk, (lows - entry) / risk, (closes - entry) / risk
    return (entry - lows) / risk, (entry - highs) / risk, (entry - closes) / risk


def _is_tie(fav: float, adv: float, st: _State, policy: ExitPolicyConfig) -> bool:
    """True when the bar touches the stop in force AND an unconsumed favourable level."""
    if adv > st.stop_r + _EPS:
        return False
    if fav >= policy.tp_r - _EPS:
        return True
    if policy.has_partial and not st.partial_taken and fav >= policy.partial_r - _EPS:
        return True
    arm_r = policy.breakeven_arm_r
    return arm_r is not None and not st.be_armed and fav >= arm_r - _EPS


def _step(
    fav: float, adv: float, st: _State, policy: ExitPolicyConfig
) -> tuple[str | None, bool]:
    """Apply one bar: stop, partial, take-profit. Returns (exit outcome, BE pending)."""
    # 1. stop first (adverse-first)
    if adv <= st.stop_r + _EPS:
        st.realized += st.remaining * st.stop_r
        return ("loss" if st.stop_r <= -1.0 + 1e-9 else "breakeven"), False

    # 2. partial scale-out
    if policy.has_partial and not st.partial_taken and fav >= policy.partial_r - _EPS:
        st.realized += policy.partial_frac * policy.partial_r
        st.remaining -= policy.partial_frac
        st.partial_taken = True

    # 3. full take-profit on the remainder
    if fav >= policy.tp_r - _EPS:
        st.realized += st.remaining * policy.tp_r
        return "win", False

    # 4. arm breakeven (effective NEXT bar — no same-bar look-ahead)
    arm_r = policy.breakeven_arm_r
    return None, arm_r is not None and not st.be_armed and fav >= arm_r - _EPS


def _arm(st: _State) -> None:
    st.be_armed = True
    st.stop_r = max(st.stop_r, 0.0)


def replay_exits(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    *,
    direction: str,
    entry: float,
    sl_price: float,
    policy: ExitPolicyConfig,
    fine_bars: FineBars | None = None,
) -> ExitOutcome:
    """Re-resolve one alert's exit over its forward window under `policy`.

    `highs`/`lows`/`closes` are the bars strictly after the signal candle, in
    time order. Raises ValueError on zero risk or an empty window (the caller
    filters these, mirroring `PaperBook`'s zero-risk skip).

    `fine_bars`, when given, is consulted only on an ambiguous bar; the bar is
    then walked on its finer bars with the same state machine, and the time-stop
    still counts in the window's own bars.
    """
    risk = abs(entry - sl_price)
    if risk <= 0.0:
        raise ValueError("risk (|entry - sl_price|) must be > 0")
    n = len(highs)
    if n == 0:
        raise ValueError("empty window")

    fav, adv, close_r = _excursions(highs, lows, closes, direction, entry, risk)
    ts = policy.effective_time_stop_bars
    st = _State()
    n_amb = n_res = n_resid = 0

    def done(outcome: str, i: int) -> ExitOutcome:
        return ExitOutcome(
            outcome, st.realized, i, st.partial_taken, n_amb, n_res, n_resid
        )

    for i in range(n):
        tie = _is_tie(float(fav[i]), float(adv[i]), st, policy)
        sub = fine_bars(i) if tie and fine_bars is not None else None
        n_amb += tie
        if sub is not None and len(sub[0]) > 0:
            n_res += 1
            f_fav, f_adv, _ = _excursions(*sub, direction, entry, risk)
            outcome: str | None = None
            be_pending = False
            for j in range(len(f_fav)):
                n_resid += _is_tie(float(f_fav[j]), float(f_adv[j]), st, policy)
                outcome, pending = _step(float(f_fav[j]), float(f_adv[j]), st, policy)
                if outcome is not None:
                    break
                if pending:
                    _arm(st)
        else:
            outcome, be_pending = _step(float(fav[i]), float(adv[i]), st, policy)
        if outcome is not None:
            return done(outcome, i)

        # 5. time-stop -> mark remaining to this bar's close
        if (i + 1) >= ts:
            st.realized += st.remaining * float(close_r[i])
            return done("expired", i)

        if be_pending:
            _arm(st)

    # window exhausted before any exit -> mark to the last close (expired)
    last = n - 1
    st.realized += st.remaining * float(close_r[last])
    return done("expired", last)

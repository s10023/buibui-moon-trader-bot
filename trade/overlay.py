"""Pure fail-closed risk overlay for the XS-solo executor.

Each guardrail is an independent check; any breach blocks the entire order plan
(not per-order). No I/O — every check is a one-line unit test. The capital
basis for the fractional caps is the book's own `capital` (live equity).
"""

from __future__ import annotations

from dataclasses import dataclass

from analytics.xsmom.live import TargetBook
from trade.routing import OrderPlan


@dataclass(frozen=True)
class RiskLimits:
    max_gross_leverage: float
    max_position_notional_frac: float
    max_drawdown_frac: float
    max_run_turnover_frac: float
    max_data_staleness_hours: float
    min_active_positions: int = 0


@dataclass(frozen=True)
class AccountState:
    equity: float
    peak_equity: float
    kill_switch: bool


@dataclass(frozen=True)
class OverlayVerdict:
    allowed: bool
    aborts: list[str]


def evaluate_overlay(
    plan: OrderPlan,
    book: TargetBook,
    account: AccountState,
    limits: RiskLimits,
    data_age_hours: float,
    *,
    current_gross_notional: float | None = None,
) -> OverlayVerdict:
    aborts: list[str] = []

    if book.active_count < limits.min_active_positions:
        aborts.append(
            f"thin book: active_count {book.active_count} < "
            f"min {limits.min_active_positions}"
        )

    if account.kill_switch:
        aborts.append("kill-switch engaged")

    floor = account.peak_equity * (1.0 - limits.max_drawdown_frac)
    if account.equity < floor:
        aborts.append(
            f"drawdown halt: equity {account.equity:.2f} < floor {floor:.2f} "
            f"(peak {account.peak_equity:.2f})"
        )

    if plan.target_gross_leverage > limits.max_gross_leverage:
        aborts.append(
            f"gross leverage {plan.target_gross_leverage:.2f} > "
            f"cap {limits.max_gross_leverage:.2f}"
        )

    notional_cap = limits.max_position_notional_frac * book.capital
    for p in book.positions:
        if abs(p.notional_usd) > notional_cap:
            aborts.append(
                f"per-instrument notional {p.symbol} {abs(p.notional_usd):.2f} "
                f"> cap {notional_cap:.2f}"
            )

    # "Establishing" = current gross < half the target gross. This is the
    # cold-start case (flat book), but also any mid-life event that halves gross
    # (a large adverse move or partial liquidation) — intended: both want to
    # rebuild to target, and the looser cap is still bounded by the gross guard.
    target_gross_notional = plan.target_gross_leverage * book.capital
    establishing = (
        current_gross_notional is not None
        and current_gross_notional < 0.5 * target_gross_notional
    )
    turnover = sum(abs(o.delta_notional) for o in plan.intents)
    turnover_cap = (
        limits.max_gross_leverage if establishing else limits.max_run_turnover_frac
    ) * book.capital
    if turnover > turnover_cap:
        aborts.append(f"run turnover {turnover:.2f} > cap {turnover_cap:.2f}")

    if data_age_hours > limits.max_data_staleness_hours:
        aborts.append(
            f"stale data: {data_age_hours:.1f}h > "
            f"{limits.max_data_staleness_hours:.1f}h"
        )

    return OverlayVerdict(allowed=not aborts, aborts=aborts)

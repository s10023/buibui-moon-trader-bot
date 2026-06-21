"""Pure order-plan construction for the XS-solo executor.

Converts a `TargetBook` plus current exchange positions, mark prices, and
per-symbol exchange filters into a market-order plan. No I/O, no Binance
imports — fully unit-testable. Reduce-only is set on trims/closes (never on
intended flips); a no-trade band and the exchange min-notional/min-qty filters
suppress negligible or unsubmittable orders.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from analytics.xsmom.live import TargetBook


@dataclass(frozen=True)
class ExchangeFilters:
    symbol: str
    qty_step: float
    min_qty: float
    min_notional: float


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str  # "BUY" | "SELL"
    qty: float
    reduce_only: bool
    delta_notional: float
    reason: str  # "open" | "rebalance" | "close" | "skip:<why>"


@dataclass(frozen=True)
class OrderPlan:
    intents: list[OrderIntent]
    skipped: list[OrderIntent]
    target_gross_leverage: float
    target_net_leverage: float


def _round_down_to_step(qty: float, step: float) -> float:
    if step <= 0:
        return qty
    return math.floor(abs(qty) / step) * step


def _signed_target_qty(notional: float, mark: float, step: float) -> float:
    mag = _round_down_to_step(abs(notional) / mark, step)
    return math.copysign(mag, notional)


def build_order_plan(
    book: TargetBook,
    current_positions: dict[str, float],
    marks: dict[str, float],
    filters: dict[str, ExchangeFilters],
    *,
    no_trade_band_frac: float,
    capital: float,
) -> OrderPlan:
    """Per-symbol market orders to move current positions to the target book."""
    band = no_trade_band_frac * capital
    target_notional = {p.symbol: p.notional_usd for p in book.positions}
    symbols = set(target_notional) | set(current_positions)

    intents: list[OrderIntent] = []
    skipped: list[OrderIntent] = []

    for sym in sorted(symbols):
        current = float(current_positions.get(sym, 0.0))
        is_close = sym not in target_notional
        notional = target_notional.get(sym, 0.0)
        mark = marks.get(sym)
        filt = filters.get(sym)

        if mark is None or mark <= 0:
            skipped.append(
                OrderIntent(
                    sym,
                    "SELL" if current > 0 else "BUY",
                    0.0,
                    False,
                    0.0,
                    "skip:no_mark",
                )
            )
            continue
        if filt is None:
            skipped.append(
                OrderIntent(
                    sym,
                    "SELL" if current > 0 else "BUY",
                    0.0,
                    False,
                    0.0,
                    "skip:no_filters",
                )
            )
            continue

        target_qty = (
            0.0 if is_close else _signed_target_qty(notional, mark, filt.qty_step)
        )
        delta_qty = _round_down_to_step(target_qty - current, filt.qty_step)
        delta_qty = math.copysign(delta_qty, target_qty - current)
        delta_notional = delta_qty * mark
        side = "BUY" if delta_qty > 0 else "SELL"
        order_qty = abs(delta_qty)

        same_side_trim = (
            current != 0.0
            and target_qty != 0.0
            and (target_qty > 0) == (current > 0)
            and abs(target_qty) < abs(current)
        )
        reduce_only = is_close or same_side_trim
        reason = "close" if is_close else ("rebalance" if current != 0.0 else "open")

        if order_qty == 0.0:
            skipped.append(
                OrderIntent(sym, side, 0.0, reduce_only, delta_notional, "skip:noop")
            )
            continue
        if order_qty < filt.min_qty:
            skipped.append(
                OrderIntent(
                    sym, side, order_qty, reduce_only, delta_notional, "skip:min_qty"
                )
            )
            continue
        if abs(delta_notional) < filt.min_notional:
            skipped.append(
                OrderIntent(
                    sym,
                    side,
                    order_qty,
                    reduce_only,
                    delta_notional,
                    "skip:min_notional",
                )
            )
            continue
        if not is_close and abs(delta_notional) < band:
            skipped.append(
                OrderIntent(
                    sym, side, order_qty, reduce_only, delta_notional, "skip:band"
                )
            )
            continue

        intents.append(
            OrderIntent(sym, side, order_qty, reduce_only, delta_notional, reason)
        )

    return OrderPlan(
        intents=intents,
        skipped=skipped,
        target_gross_leverage=book.gross_leverage,
        target_net_leverage=book.net_leverage,
    )

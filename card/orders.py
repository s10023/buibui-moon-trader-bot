"""Post-card GTX order helper: scan, guards, placement, instrumentation.

Spec: docs/superpowers/specs/2026-08-28-st37-card-gtx-order-helper-design.md
Places picked TRADE cards as post-only (GTX) limit entries and records every
placement + terminal state in an append-only jsonl ledger, joined back to the
card by (symbol, generated_at_ms).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from card.card import _parse_iso_ms
from card.ledger import _append_line
from portfolio.sizing import risk_per_unit, round_down_to_step, round_to_tick
from trade.binance_futures import APIError, BinanceFuturesAdapter
from trade.routing import ExchangeFilters, OrderIntent

DEFAULT_ORDERS_PATH = "docs/plans/card-orders.jsonl"
# The XS executor writes execution_state_{mode}.json per mode; a live-mode run
# leaves this marker. Testnet orders live on a different venue and cannot
# collide with mainnet card orders, so only "live" gates.
XS_LIVE_MARKER = Path("docs/plans/xsmom_targets/execution_state_live.json")


@dataclass(frozen=True)
class CardCandidate:
    symbol: str
    direction: str  # "long" | "short"
    entry: float
    sl: float
    qty: float
    risk_usd: float | None
    risk_frac: float | None
    valid_until_ms: int
    generated_at_ms: int
    state_digest: str


@dataclass(frozen=True)
class AggregateRisk:
    total_risk_usd: float
    total_risk_frac: float | None  # None when equity is unknown
    by_bet: list[tuple[str, str, float]]  # (symbol, direction, summed risk_usd)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a jsonl file, tolerating a torn tail line (append-only ledgers)."""
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def scan_candidates(
    card_rows: list[dict[str, Any]],
    order_rows: list[dict[str, Any]],
    now_ms: int,
) -> list[CardCandidate]:
    """Unexpired TRADE cards not already placed.

    Anti-join on (symbol, generated_at_ms): a gtx_rejected placement row also
    suppresses re-presentation on purpose — a rejection means price is already
    through the level, so the setup is stale by definition.
    """
    placed = {
        (r.get("symbol"), r.get("card_generated_at_ms"))
        for r in order_rows
        if r.get("kind") == "placement"
    }
    out: list[CardCandidate] = []
    for row in card_rows:
        if row.get("verdict") != "TRADE":
            continue
        card = row.get("card") or {}
        expiry = _parse_iso_ms(str(card.get("valid_until_utc") or ""))
        if expiry is None or expiry <= now_ms:
            continue
        if (row.get("symbol"), row.get("generated_at_ms")) in placed:
            continue
        direction = card.get("direction")
        entry = card.get("entry")
        sl = card.get("sl")
        qty = row.get("size_units")
        if direction not in ("long", "short") or not entry or not sl or not qty:
            continue
        risk_usd = row.get("risk_usd")
        risk_frac = row.get("risk_frac")
        out.append(
            CardCandidate(
                symbol=str(row["symbol"]),
                direction=str(direction),
                entry=float(entry),
                sl=float(sl),
                qty=float(qty),
                risk_usd=float(risk_usd) if risk_usd is not None else None,
                risk_frac=float(risk_frac) if risk_frac is not None else None,
                valid_until_ms=expiry,
                generated_at_ms=int(row["generated_at_ms"]),
                state_digest=str(row.get("state_digest") or ""),
            )
        )
    return out


def parse_selection(text: str, n: int) -> list[int] | None:
    """'' -> [] (default none, never pre-selected); invalid input -> None."""
    text = text.strip()
    if not text:
        return []
    picks: list[int] = []
    for tok in text.replace(",", " ").split():
        if not tok.isdigit() or not (1 <= int(tok) <= n):
            return None
        idx = int(tok) - 1
        if idx not in picks:
            picks.append(idx)
    return picks


def aggregate_risk(
    selected: list[CardCandidate], equity: float | None
) -> AggregateRisk:
    """Total + per-(symbol, direction) stacking — the view a flat list hides."""
    total = sum(c.risk_usd or 0.0 for c in selected)
    bets: dict[tuple[str, str], float] = {}
    for c in selected:
        key = (c.symbol, c.direction)
        bets[key] = bets.get(key, 0.0) + (c.risk_usd or 0.0)
    frac = (total / equity) if equity and equity > 0.0 else None
    return AggregateRisk(
        total_risk_usd=total,
        total_risk_frac=frac,
        by_bet=[(s, d, r) for (s, d), r in sorted(bets.items())],
    )


@dataclass(frozen=True)
class PlacementDecision:
    candidate: CardCandidate
    qty: float
    price: float
    risk_usd: float | None
    risk_frac: float | None
    vetoes: list[str]
    warnings: list[str]


def check_placement(
    cand: CardCandidate,
    filt: ExchangeFilters | None,
    *,
    managed: bool,
    xs_live_marker: bool,
    now_ms: int,
) -> PlacementDecision:
    """Every hard rule in code, veto-style, each naming its numbers."""
    vetoes: list[str] = []
    warnings: list[str] = []

    # (a) expiry at the PLACEMENT instant — the card veto only ever compared
    # against generated_at_ms, and a card can expire while the operator thinks.
    if cand.valid_until_ms <= now_ms:
        vetoes.append(
            f"card expired at placement ({(now_ms - cand.valid_until_ms) / 1000:.0f}s past valid_until)"
        )

    # (b) XS collision: cancel_open_orders is symbol-WIDE, so a live XS run
    # cancels card orders on any managed symbol. Veto on managed AND live
    # marker; always warn on managed so the hazard is heard before go-live.
    if managed and xs_live_marker:
        vetoes.append(
            f"{cand.symbol} is in the XS managed set and a live XS execution "
            "state exists - a live XS run cancels ALL open orders on this symbol"
        )
    elif managed:
        warnings.append(
            f"{cand.symbol} is in the XS managed set; a future live XS run "
            "would cancel this order"
        )

    # (d) exchange rounding, restated risk (the card's own sizing rule applied
    # at placement time)
    qty, price = cand.qty, cand.entry
    risk_usd, risk_frac = cand.risk_usd, cand.risk_frac
    if filt is None:
        warnings.append("exchange filters unavailable - placing unrounded numbers")
    else:
        side = "BUY" if cand.direction == "long" else "SELL"
        qty = round_down_to_step(cand.qty, filt.qty_step)
        price = round_to_tick(cand.entry, filt.price_tick, side)
        if qty <= 0.0 or qty < filt.min_qty:
            vetoes.append(
                f"sub-lot after rounding: {cand.qty} -> {qty} against step {filt.qty_step}"
            )
        elif qty * price < filt.min_notional:
            notional = qty * price
            vetoes.append(
                f"below min notional: {qty} @ {price} = {notional:.2f} "
                f"< {filt.min_notional}"
            )
        else:
            risk_usd = round(qty * risk_per_unit(price, cand.sl), 8)
            # restate risk_frac proportionally — equivalent to re-dividing by
            # capital without reconstructing it; None when there is nothing to
            # scale (no risk_frac on the card, or the pre-rounding risk_usd is
            # missing/zero).
            if (
                cand.risk_frac is not None
                and cand.risk_usd is not None
                and cand.risk_usd != 0.0
            ):
                risk_frac = cand.risk_frac * (risk_usd / cand.risk_usd)
            else:
                risk_frac = None
            if qty != cand.qty or price != cand.entry:
                warnings.append(
                    f"rounded qty {cand.qty} -> {qty}, price {cand.entry} -> {price}; "
                    f"risk restated to {risk_usd}"
                )
    return PlacementDecision(
        candidate=cand,
        qty=qty,
        price=price,
        risk_usd=risk_usd,
        risk_frac=risk_frac,
        vetoes=vetoes,
        warnings=warnings,
    )


# Binance rejects a GTX order that would cross as an error rather than
# resting it: "Due to the order could not be executed as maker, the Post
# Only order will be rejected." Verified in Task 5 Step 1 (per Ruling R2)
# against the installed python-binance 1.0.37, where trade/binance_futures.py
# binds APIError to the real binance.exceptions.BinanceAPIException, which
# sets .code from the parsed response body -- adjust here if a future
# rebinding surfaces the rejection differently.
_POST_ONLY_REJECT = -5022


def place_orders(
    adapter: BinanceFuturesAdapter,
    decisions: list[PlacementDecision],
    *,
    ledger_path: Path,
    dual_side: bool,
    marks: dict[str, float],
    books: dict[str, tuple[float, float]],
    positions: dict[str, float],
    equity: float | None,
    now_ms: int,
) -> list[dict[str, Any]]:
    """Submit each un-vetoed decision as a GTX limit; append placement rows.

    A GTX rejection is INFORMATION, not an error: price is already through the
    structural level, so the setup is stale rather than the order broken. It
    is recorded with order_id None + terminal_reason "gtx_rejected", which
    also suppresses re-presentation via the scan's anti-join.
    Dry-run placements return [] and write nothing.
    """
    written: list[dict[str, Any]] = []
    for d in decisions:
        if d.vetoes:
            continue
        cand = d.candidate
        side = "BUY" if cand.direction == "long" else "SELL"
        position_side = (
            ("LONG" if cand.direction == "long" else "SHORT") if dual_side else None
        )
        intent = OrderIntent(
            cand.symbol,
            side,
            d.qty,
            False,
            0.0,
            "card",
            "LIMIT",
            position_side=position_side,
        )
        order_id: int | None = None
        terminal_reason: str | None = None
        try:
            resp = adapter.submit(intent, price=d.price)
        except APIError as exc:
            if getattr(exc, "code", None) != _POST_ONLY_REJECT:
                raise
            terminal_reason = "gtx_rejected"
        else:
            if resp.get("dryRun"):
                continue  # never pollute the ledger from a dry run
            order_id = int(resp["orderId"])
        top = books.get(cand.symbol)
        bid = top[0] if top else None
        ask = top[1] if top else None
        row: dict[str, Any] = {
            "kind": "placement",
            "order_id": order_id,
            "symbol": cand.symbol,
            "direction": cand.direction,
            "card_generated_at_ms": cand.generated_at_ms,
            "state_digest": cand.state_digest,
            "placed_at_ms": now_ms,
            "limit_price": d.price,
            "qty": d.qty,
            "risk_usd": d.risk_usd,
            "risk_frac": d.risk_frac,
            "mark_at_placement": marks.get(cand.symbol),
            "bid_at_placement": bid,
            "ask_at_placement": ask,
            "position_at_placement": positions.get(cand.symbol, 0.0),
            "equity_at_placement": equity,
            "position_mode": "hedge" if dual_side else "one_way",
            "warnings": d.warnings,
        }
        if terminal_reason is not None:
            row["terminal_reason"] = terminal_reason
            row["terminal_at_ms"] = now_ms
        _append_line(ledger_path, row)
        written.append(row)
    return written

"""Post-card GTX order helper: scan, guards, placement, instrumentation.

Spec: docs/superpowers/specs/2026-08-28-st37-card-gtx-order-helper-design.md
Places picked TRADE cards as post-only (GTX) limit entries and records every
placement + terminal state in an append-only jsonl ledger, joined back to the
card by (symbol, generated_at_ms).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from card.card import _parse_iso_ms
from card.ledger import _append_line
from portfolio.sizing import (
    _tick_decimals,
    risk_per_unit,
    round_down_to_step,
    round_to_tick,
)
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
    # Rows whose risk_usd is None and so contribute 0.0 to the total above.
    # The per-row view prints "?" for them, but the AGGREGATE line is what the
    # y/N answers, and a total that silently omits them reads as complete.
    unknown_risk_count: int = 0


def read_jsonl_counted(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Parse a jsonl file, returning its rows AND the count of dropped lines.

    A dropped line is not cosmetic here. A torn append (a crash mid-write)
    leaves the file without a trailing newline, so the NEXT append
    concatenates onto it and BOTH rows become one unparseable line -- losing a
    complete placement row. The scan's anti-join on
    (symbol, card_generated_at_ms) is the only thing stopping a card being
    placed twice, so a silently dropped placement row re-presents that card in
    the picklist and the operator places a DUPLICATE LIVE ORDER believing it
    is new. The count exists so `run_place` can say so out loud.

    Tolerating the line (rather than raising) is still right -- an unreadable
    tail must not make the whole ledger unusable -- but the drop has to be
    visible. Preventing the tear at the write end belongs in
    `card.ledger._append_line`, which is shared with the ai-cards writer and
    is tracked as a follow-up.
    """
    if not path.exists():
        return [], 0
    out: list[dict[str, Any]] = []
    dropped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            dropped += 1
            continue
    return out, dropped


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Rows only, for callers with nothing to do about a dropped line."""
    return read_jsonl_counted(path)[0]


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
        if not (tok.isascii() and tok.isdigit()) or not (1 <= int(tok) <= n):
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
        unknown_risk_count=sum(1 for c in selected if c.risk_usd is None),
    )


_ONE_DRAW_LINE = (
    "A card verdict is ONE DRAW - identical inputs have returned opposite "
    "directions. Nothing is pre-selected; empty input places nothing."
)


def pick_interactive(
    candidates: list[CardCandidate],
    equity: float | None,
    *,
    input_fn: Callable[[str], str],
    print_fn: Callable[[str], None],
    now_ms: int | None = None,
) -> list[CardCandidate]:
    """Numbered table -> selection -> aggregate echo -> y/N confirm.

    `now_ms` mirrors the injection pattern every other function in this
    module uses (`scan_candidates`, `check_placement`, `place_orders`,
    `refresh_orders`) so the "expires" countdown -- the one field here the
    operator reads before committing real money -- can be tested
    deterministically. `None` (the CLI's call site) defaults to wall clock.
    """
    print_fn(_ONE_DRAW_LINE)
    print_fn(
        f"{'#':>2}  {'symbol':<10} {'dir':<5} {'entry':>12} {'sl':>12} "
        f"{'qty':>10} {'risk_usd':>9}  expires"
    )
    if now_ms is None:
        now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    for i, c in enumerate(candidates, 1):
        mins = (c.valid_until_ms - now_ms) / 60_000
        risk = f"{c.risk_usd:.2f}" if c.risk_usd is not None else "?"
        print_fn(
            f"{i:>2}  {c.symbol:<10} {c.direction:<5} {c.entry:>12} "
            f"{c.sl:>12} {c.qty:>10} {risk:>9}  {mins:.0f}m"
        )
    while True:
        picks = parse_selection(
            input_fn("place which? (numbers, empty = none): "), len(candidates)
        )
        if picks is not None:
            break
        print_fn("unrecognised - numbers from the table, space or comma separated")
    if not picks:
        return []
    selected = [candidates[i] for i in picks]
    agg = aggregate_risk(selected, equity)
    frac = (
        f" = {agg.total_risk_frac * 100:.2f}% of equity"
        if agg.total_risk_frac is not None
        else ""
    )
    # A row with no risk_usd contributes 0.0 to the total, so the aggregate
    # UNDERSTATES whenever one is selected. The per-row "?" is not enough --
    # the y/N is answering this line, not that column.
    unknown = (
        f" ({agg.unknown_risk_count} of {len(selected)} with UNKNOWN risk_usd - "
        "total understates)"
        if agg.unknown_risk_count
        else ""
    )
    print_fn(
        f"AGGREGATE: {len(selected)} orders, total risk "
        f"${agg.total_risk_usd:.2f}{frac}{unknown}"
    )
    for symbol, direction, risk_usd in agg.by_bet:
        print_fn(f"  {symbol} {direction}: ${risk_usd:.2f}")
    if input_fn("confirm placement? [y/N]: ").strip().lower() != "y":
        return []
    return selected


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
        # Quantise to the step's own decimal precision, exactly as
        # `round_to_tick` does for price and for the same reason:
        # `round_down_to_step` returns `floor(quotient) * step`, which carries
        # float error (`round_down_to_step(0.009, 0.001)` is
        # 0.009000000000000001, and 0.8175 at that step is 0.8170000000000001).
        # python-binance urlencodes params with a bare `str()`, so those 18
        # decimals reach the wire and Binance rejects the order -1111,
        # "precision is over the maximum defined for this asset". The price
        # half was fixed inside `round_to_tick`; the quantity half is fixed
        # HERE rather than inside `round_down_to_step`, which is shared with
        # `trade/routing.py`'s XS router and whose wire shape this branch
        # pinned byte-identical. A non-positive step means "unknown filter"
        # and must pass through unrounded -- `_tick_decimals(0.0)` is 0, which
        # would otherwise round a fractional quantity to a whole one.
        qty = round_down_to_step(cand.qty, filt.qty_step)
        if filt.qty_step > 0.0:
            qty = round(qty, _tick_decimals(filt.qty_step))
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

    A submit that fails with anything OTHER than an APIError -- a `requests`
    timeout or connection reset, which python-binance raises as
    BinanceRequestException / requests.Timeout rather than APIError -- may
    still have been ACCEPTED by the exchange: the request reached it and the
    response was lost. That order would be invisible to this ledger forever,
    because `card-orders --refresh` only polls order ids it already holds. So
    a row is written with order_id None + terminal_reason "submit_unknown",
    the operator is told to check open orders by hand, and the error is then
    re-raised unchanged. That row carries the anti-join key too, so the card
    does not re-present while its true state is unknown -- resolving it is a
    manual step, not a second automatic placement. A 2xx response with no
    `orderId` (`UnconfirmedOrderError`, #829) takes the same path.
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
        submit_error: BaseException | None = None
        try:
            resp = adapter.submit(intent, price=d.price)
        except APIError as exc:
            # A non-5022 APIError is a real exchange refusal: the order does
            # NOT exist, so it re-raises with the ledger untouched. Raising
            # from inside this handler also keeps it away from the broad
            # handler below, which must never record a refusal as "unknown".
            if getattr(exc, "code", None) != _POST_ONLY_REJECT:
                raise
            terminal_reason = "gtx_rejected"
        except Exception as exc:
            terminal_reason = "submit_unknown"
            submit_error = exc
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
        if submit_error is not None:
            # An UnconfirmedOrderError's text carries the raw response body,
            # the one piece of evidence #829 lacked; keep it beside the row.
            row["submit_error"] = repr(submit_error)
        _append_line(ledger_path, row)
        written.append(row)
        if submit_error is not None:
            print(
                f"! {cand.symbol}: submit outcome UNKNOWN ({submit_error!r}) - the "
                "exchange may have ACCEPTED this order. Recorded as "
                "submit_unknown; check open orders on the exchange MANUALLY "
                "before re-running card-place."
            )
            raise submit_error
    return written


# Every OTHER status is treated as terminal (R13): a status Binance adds or
# renames later must not silently fall into "still working" and get
# re-polled forever -- the placement<->terminal join on order_id is this
# feature's whole deliverable, and a hole in it is worse than a row filed
# under reason "other". _WORKING_STATUSES is deliberately the SMALL,
# enumerated set; _STATUS_REASON only prettifies the known cases into the
# spec's vocabulary and is never consulted to decide terminal-ness.
_WORKING_STATUSES = {"NEW", "PARTIALLY_FILLED", "PENDING_CANCEL"}
# Binance's "Order does not exist": returned FOREVER for an order that was
# cancelled or expired without filling and is now older than 7 days.
_ORDER_NOT_FOUND = -2013
_STATUS_REASON = {
    "FILLED": "filled",
    "CANCELED": "cancelled",
    "EXPIRED": "expired",
    # self-trade prevention's status: this account rests a card order and an
    # XS order on the same symbol, which is the exact collision
    # check_placement's XS guard exists for, arriving by a different route.
    "EXPIRED_IN_MATCH": "expired",
    "REJECTED": "rejected",
    # SYNTHETIC, not a Binance status: written by this module when the poll
    # comes back -2013. The order is gone and never filled, so the row is a
    # fact rather than a guess -- but it is spelled out here so a reader
    # never looks for "NOT_FOUND" in Binance's own status list.
    "NOT_FOUND": "not_found",
}


def refresh_orders(
    client: Any,
    ledger_path: Path,
    *,
    marks: dict[str, float],
    now_ms: int,
) -> list[dict[str, Any]]:
    """Poll each open placement once; append a terminal row when it closed.

    Age-at-terminal and price-drift-at-cancel are derivable by joining the two
    row kinds on order_id - the whole of the max_order_age /
    hanging_orders_cancel_pct / order_refresh_tolerance claims (ST33).

    Any status outside _WORKING_STATUSES is written as terminal even when
    unrecognised (reason "other", raw status kept on the row) -- an unknown
    status recorded early is visible and recoverable, where silently
    retrying it forever is a permanent hole in the join above.

    The poll is wrapped PER ORDER, because a batch-level abort is not
    self-healing: Binance stops answering Query Order for an order that is
    CANCELED or EXPIRED, was never filled, and is more than 7 days old -- the
    exact shape of an unfilled GTX entry from a manually-run command -- and
    then returns -2013 forever. Iterating in file order, one such row would
    permanently block every LATER order from ever getting a terminal row,
    and the placement<->terminal join is this feature's whole deliverable.

    -2013 is therefore written as terminal with reason "not_found": the order
    is provably gone and provably never filled, so that row is honest rather
    than a guess. Any other poll failure (a timeout, a rate limit, a
    credentials problem) warns and moves to the next order -- it says nothing
    about that order's state, so it stays open and is retried on the next
    invocation, which is where the self-healing actually lives.
    """
    rows = read_jsonl(ledger_path)
    terminal_ids = {r.get("order_id") for r in rows if r.get("kind") == "terminal"}
    written: list[dict[str, Any]] = []
    for p in rows:
        if p.get("kind") != "placement" or not p.get("order_id"):
            continue
        if p["order_id"] in terminal_ids:
            continue
        try:
            order = client.futures_get_order(symbol=p["symbol"], orderId=p["order_id"])
        except Exception as exc:
            if getattr(exc, "code", None) != _ORDER_NOT_FOUND:
                print(
                    f"! {p['symbol']} order {p['order_id']}: poll failed "
                    f"({exc!r}) - left open, retried on the next --refresh"
                )
                continue
            order = {
                "status": "NOT_FOUND",
                "updateTime": now_ms,
                "avgPrice": 0.0,
                "executedQty": 0.0,
            }
        status = str(order.get("status"))
        if status in _WORKING_STATUSES:
            continue
        row: dict[str, Any] = {
            "kind": "terminal",
            "order_id": p["order_id"],
            "symbol": p["symbol"],
            "terminal_at_ms": int(order.get("updateTime") or now_ms),
            "status": status,
            "reason": _STATUS_REASON.get(status, "other"),
            "avg_price": float(order.get("avgPrice") or 0.0),
            "executed_qty": float(order.get("executedQty") or 0.0),
            "mark_at_terminal": marks.get(str(p["symbol"])),
        }
        _append_line(ledger_path, row)
        written.append(row)
        terminal_ids.add(p["order_id"])  # R14 minor 1: keep it live within this call
    return written

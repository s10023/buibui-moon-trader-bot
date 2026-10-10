"""Binance USDT-M Futures I/O adapter for the XS-solo executor.

Thin, injectable wrapper over a `python-binance` Client. Read methods always
hit the API — this includes `dry_run`, so a dry run sees real exchange state;
write methods (`ensure_account_config`, `submit`, `cancel_order`) are
no-op-and-log when `mode == "dry_run"`. The client is constructed by the CLI
(mainnet for dry_run/live, testnet client for testnet) and injected here, so
this class is unit-testable with a MagicMock.
"""

from __future__ import annotations

import re
from typing import Any

from trade.routing import ExchangeFilters, OrderIntent

try:
    # python-binance >=1.0 exposes `BinanceAPIException`, not `APIError` — the
    # name has already varied by version, so bind to whichever one the
    # installed client provides rather than pinning a single spelling.
    from binance.exceptions import BinanceAPIException as APIError
except ImportError:  # pragma: no cover - older/alternate client shape
    try:
        from binance.exceptions import APIError
    except ImportError:  # pragma: no cover - library absent entirely

        class APIError(Exception):  # type: ignore[no-redef]
            code = 0


_MARGIN_TYPE_UNCHANGED = -4046
# A GTX order that would cross is refused rather than rested (card.orders and
# trade.exit_manager both read it as a stale level, not a broken order).
POST_ONLY_REJECT = -5022
# "Invalid API-key, IP, or permissions": the key is allowlisted to a dynamic
# residential IP, so an ISP change returns this on EVERY signed call (#981).
KEY_OR_IP_REJECTED = -2015
# "Order does not exist". For a client order id it is the answer to "was this
# ever placed?", and Binance also returns it forever for an order cancelled or
# expired unfilled more than 7 days ago (`card.orders.refresh_orders`). That
# the algo route answers an unknown `clientAlgoId` with the same code is
# UNPROBED (#1023's Local session prompt); any other code raises, and every
# caller then falls back to standing down, so a wrong guess here fails safe.
ORDER_NOT_FOUND = -2013
# One lookup can miss an order the exchange has not finished recording, so an
# id counts as never placed only after this many misses, across polls
# (hummingbot `client_order_tracker.py:221-249` uses the same shape).
NEVER_PLACED_AFTER_MISSES = 3
_TRADES_MAX_INTERVAL_MS = 7 * 86_400_000  # userTrades' per-call window cap
_TRADES_PAGE_LIMIT = 1000

# python-binance 1.0.37's `futures_create_order` silently routes these types to
# `POST /fapi/v1/algoOrder` (Binance moved conditionals to the Algo service on
# 2025-12-09). That endpoint acknowledges with `algoId`, never `orderId`, and
# the order lists under `openAlgoOrders`, not `openOrders` -- so a stop that
# WAS placed reads, through classic-order code, as a KeyError plus "no order
# exists" (#829, measured 2026-08-29 on TRXUSDT). Mirrors the library's list.
_CONDITIONAL_TYPES = frozenset(
    {"STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET", "TRAILING_STOP_MARKET"}
)


# Binance's rule for newClientOrderId and clientAlgoId.
_CLIENT_ORDER_ID_RE = re.compile(r"^[.A-Z:/a-z0-9_-]{1,36}$")


def make_client_order_id(prefix: str, *parts: object) -> str:
    """`<prefix>-<part>-<part>...`, checked against Binance's id rule.

    Deterministic on purpose (#1023): the id is written into the caller's
    intent row BEFORE the submit, so a run that dies mid-submit leaves the one
    key that finds the order again. Raises ValueError rather than letting the
    exchange refuse the order after the intent row is already written.
    """
    cid = "-".join([prefix, *(str(p) for p in parts)])
    if not _CLIENT_ORDER_ID_RE.match(cid):
        raise ValueError(f"client order id {cid!r} breaks Binance's 36-char rule")
    return cid


class UnconfirmedOrderError(Exception):
    """A 2xx order response that does not carry the id its route acknowledges with.

    python-binance raises only on a non-2xx status and hands any 2xx body back
    as a plain dict, so an error payload on HTTP 200 would otherwise read as an
    accepted order. Deliberately NOT an `APIError`: an APIError means the
    exchange refused and nothing exists, while here the order's existence is
    UNKNOWN. Callers must treat it as unknown, never as a refusal or a success.
    """

    def __init__(self, symbol: str, expected_key: str, body: Any) -> None:
        self.symbol = symbol
        self.expected_key = expected_key
        self.body = body
        super().__init__(
            f"{symbol}: order response carries no {expected_key!r}; "
            f"exchange state UNKNOWN. raw body: {body!r}"
        )


def ack_key(order_type: str) -> str:
    """The id field Binance acknowledges an order of `order_type` with."""
    return "algoId" if order_type.upper() in _CONDITIONAL_TYPES else "orderId"


def require_ack(symbol: str, order_type: str, resp: Any) -> dict[str, Any]:
    """Return `resp` if it acknowledges the order, else raise `UnconfirmedOrderError`."""
    key = ack_key(order_type)
    if not isinstance(resp, dict) or resp.get(key) is None:
        raise UnconfirmedOrderError(symbol, key, resp)
    return resp


def find_order_by_client_id(
    client: Any, symbol: str, client_order_id: str, *, conditional: bool
) -> dict[str, Any] | None:
    """The order we sent under `client_order_id`, or None if Binance has none.

    Classic orders are queried by `origClientOrderId`, conditionals by
    `clientAlgoId` (python-binance routes that to `GET /fapi/v1/algoOrder`).
    None means -2013, "Order does not exist"; any other error raises. A found
    order that does not carry its route's id raises `UnconfirmedOrderError`,
    because a body that names no order proves nothing either way.
    """
    key = "clientAlgoId" if conditional else "origClientOrderId"
    try:
        resp = client.futures_get_order(symbol=symbol, **{key: client_order_id})
    except APIError as exc:
        if getattr(exc, "code", None) == ORDER_NOT_FOUND:
            return None
        raise
    order_type = "STOP_MARKET" if conditional else "LIMIT"
    return dict(require_ack(symbol, order_type, resp))


class BinanceFuturesAdapter:
    def __init__(self, client: Any, mode: str) -> None:
        if mode not in ("dry_run", "testnet", "live"):
            raise ValueError(f"unknown mode {mode!r}")
        self.client = client
        self.mode = mode

    # ----- reads -----
    def get_positions(self) -> dict[str, float]:
        rows = self.client.futures_position_information()
        out: dict[str, float] = {}
        for r in rows:
            amt = float(r["positionAmt"])
            if amt != 0.0:
                out[r["symbol"]] = amt
        return out

    def get_net_positions(self) -> dict[str, float]:
        """Net `positionAmt` per symbol, SUMMED across position sides.

        `get_positions` above keeps whichever row for a symbol arrives last,
        which is correct on a one-way account (one row per symbol) and wrong
        on a DUAL-SIDE one, where `futures_position_information` returns a
        LONG row and a SHORT row per symbol: with both legs open it records
        one leg as the whole position. This account has been hedge-mode for
        its entire history, so the card order ledger's `position_at_placement`
        reads from here instead.

        Added beside `get_positions` rather than replacing it: the XS executor
        depends on that method's exact shape, and additive cannot break it.
        Summing means a fully hedged symbol reads 0.0 -- which is its true net
        exposure, the number this field claims to record. Legs are not broken
        out because the ledger field is a single float; a per-side record
        would be a schema change, not a fix.
        """
        rows = self.client.futures_position_information()
        out: dict[str, float] = {}
        for r in rows:
            amt = float(r["positionAmt"])
            if amt != 0.0:
                out[r["symbol"]] = out.get(r["symbol"], 0.0) + amt
        return out

    def get_equity(self) -> float:
        return float(self.client.futures_account()["totalMarginBalance"])

    def get_filters(self, symbols: list[str]) -> dict[str, ExchangeFilters]:
        info = self.client.futures_exchange_info()
        wanted = set(symbols)
        out: dict[str, ExchangeFilters] = {}
        for s in info["symbols"]:
            if s["symbol"] not in wanted:
                continue
            step = min_qty = min_notional = price_tick = 0.0
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    step = float(f["stepSize"])
                    min_qty = float(f["minQty"])
                elif f["filterType"] == "MIN_NOTIONAL":
                    min_notional = float(f["notional"])
                elif f["filterType"] == "PRICE_FILTER":
                    price_tick = float(f["tickSize"])
            out[s["symbol"]] = ExchangeFilters(
                symbol=s["symbol"],
                qty_step=step,
                min_qty=min_qty,
                min_notional=min_notional,
                price_tick=price_tick,
            )
        return out

    def get_marks(self, symbols: list[str]) -> dict[str, float]:
        rows = self.client.futures_mark_price()
        wanted = set(symbols)
        return {
            r["symbol"]: float(r["markPrice"]) for r in rows if r["symbol"] in wanted
        }

    def get_book_tops(self, symbols: list[str]) -> dict[str, tuple[float, float]]:
        """Best bid/ask per symbol, for post-only limit placement.

        Mirrors `get_marks`: one batch call for the whole universe. A
        non-positive quote on either side is dropped rather than returned as
        zero — a limit priced off a zero would be rejected or, worse, filled
        somewhere absurd.
        """
        rows = self.client.futures_orderbook_ticker()
        wanted = set(symbols)
        out: dict[str, tuple[float, float]] = {}
        for r in rows:
            if r["symbol"] not in wanted:
                continue
            bid = float(r["bidPrice"])
            ask = float(r["askPrice"])
            if bid > 0.0 and ask > 0.0:
                out[r["symbol"]] = (bid, ask)
        return out

    def get_open_orders(self) -> list[dict[str, Any]]:
        """Every resting classic order on the account, each with its `clientOrderId`.

        Read with no symbol argument so it covers symbols that have since left
        the target book — the case with no position to reveal it. Not
        dry-run-guarded: it is a plain read, and a dry run needs to see real
        resting orders to surface the stale-order condition the XS executor
        cancels by id (#1023).
        """
        return [dict(r) for r in self.client.futures_get_open_orders()]

    def is_dual_side(self) -> bool:
        """True on a hedge-mode (dualSidePosition) account."""
        return bool(self.client.futures_get_position_mode().get("dualSidePosition"))

    def get_side_position(
        self, symbol: str, side: str, *, dual_side: bool
    ) -> tuple[float, float]:
        """(absolute qty, entry price) of one direction's position; (0.0, 0.0) if flat.

        Hedge mode reads the row whose `positionSide` is `side`. One-way mode
        has a single `BOTH` row whose sign is the direction, so a LONG read of
        a short position is flat, never a negative quantity.
        """
        if side not in ("LONG", "SHORT"):
            raise ValueError(f"side must be LONG or SHORT, got {side!r}")
        for r in self.client.futures_position_information(symbol=symbol):
            if r.get("symbol", symbol) != symbol:
                continue
            amt = float(r["positionAmt"])
            if dual_side:
                if r.get("positionSide") != side:
                    continue
            elif (amt > 0.0) != (side == "LONG"):
                continue
            if amt != 0.0:
                return abs(amt), float(r.get("entryPrice") or 0.0)
        return 0.0, 0.0

    def get_open_algo_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Resting conditional orders (`openAlgoOrders`); `openOrders` never lists a stop."""
        rows = self.client.futures_get_open_orders(symbol=symbol, conditional=True)
        if isinstance(rows, dict):  # the algo route has wrapped lists before
            rows = rows.get("orders", [])
        return list(rows or [])

    def get_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        """One classic order by id, whatever its status."""
        resp = self.client.futures_get_order(symbol=symbol, orderId=order_id)
        return dict(resp)

    def find_order(
        self, symbol: str, client_order_id: str, *, conditional: bool
    ) -> dict[str, Any] | None:
        """See `find_order_by_client_id`. A read, so a dry run hits the API too."""
        return find_order_by_client_id(
            self.client, symbol, client_order_id, conditional=conditional
        )

    def get_account_trades(
        self, symbol: str, start_ms: int, end_ms: int
    ) -> list[dict[str, Any]]:
        """Every fill on `symbol` in [start_ms, end_ms], oldest first.

        `userTrades` serves at most 7 days per call, and a `startTime` with no
        `endTime` is silently clamped to the first 7 days of the window (see
        `tools/journal_fetch.py`), so the window is tiled with an explicit end.
        A tile that returns its `limit` is paged forward from its last fill.
        """
        out: dict[Any, dict[str, Any]] = {}
        start = start_ms
        while start <= end_ms:
            end = min(start + _TRADES_MAX_INTERVAL_MS, end_ms)
            cursor = start
            while True:
                rows = self.client.futures_account_trades(
                    symbol=symbol,
                    startTime=cursor,
                    endTime=end,
                    limit=_TRADES_PAGE_LIMIT,
                )
                for r in rows:
                    out[r.get("id")] = r
                if len(rows) < _TRADES_PAGE_LIMIT:
                    break
                last = max(int(r["time"]) for r in rows)
                if last <= cursor:  # a full page inside one millisecond
                    break
                cursor = last
            if end >= end_ms:
                break
            start = end + 1
        return sorted(out.values(), key=lambda r: (int(r["time"]), r.get("id") or 0))

    # ----- writes -----
    def ensure_account_config(self, symbols: list[str], *, leverage: int) -> None:
        if self.mode == "dry_run":
            return
        if self.client.futures_get_position_mode().get("dualSidePosition"):
            raise RuntimeError(
                "account is in hedge mode; XS executor requires one-way mode"
            )
        for sym in symbols:
            try:
                self.client.futures_change_margin_type(symbol=sym, marginType="CROSSED")
            except APIError as exc:  # already CROSSED is fine
                if getattr(exc, "code", None) != _MARGIN_TYPE_UNCHANGED:
                    raise
            self.client.futures_change_leverage(symbol=sym, leverage=leverage)

    def submit(self, intent: OrderIntent, price: float | None = None) -> dict[str, Any]:
        """Submit one order. LIMIT orders are post-only (GTX).

        GTX makes the exchange REJECT an order that would cross instead of
        letting it take. That is the point — a crossed "maker" order is just a
        taker fill with extra steps — but it means a wrong-side tick rounding
        fails as a rejection, not a bad fill. See `round_to_tick`.

        A live response is returned only once `require_ack` has seen its id;
        anything else raises `UnconfirmedOrderError` (#829).
        """
        if intent.order_type == "LIMIT" and price is None:
            raise ValueError(f"LIMIT order requires a price: {intent.symbol}")
        conditional = intent.order_type.upper() in _CONDITIONAL_TYPES
        if conditional and intent.stop_price is None:
            raise ValueError(
                f"{intent.order_type} order requires a stop_price: {intent.symbol}"
            )
        if intent.close_position and not conditional:
            # Binance accepts closePosition only on STOP_MARKET/TAKE_PROFIT_MARKET.
            raise ValueError(
                f"close_position needs a conditional type, got {intent.order_type}"
            )
        if intent.client_order_id is not None and not _CLIENT_ORDER_ID_RE.match(
            intent.client_order_id
        ):
            raise ValueError(f"bad client order id {intent.client_order_id!r}")
        if self.mode == "dry_run":
            return {
                "dryRun": True,
                "clientOrderId": intent.client_order_id,
                "symbol": intent.symbol,
                "side": intent.side,
                "qty": intent.qty,
                "reduceOnly": intent.reduce_only,
                "orderType": intent.order_type,
                "price": price,
                "positionSide": intent.position_side,
                "stopPrice": intent.stop_price,
                "closePosition": intent.close_position,
            }
        params: dict[str, Any] = {
            "symbol": intent.symbol,
            "side": intent.side,
            "type": intent.order_type,
        }
        if intent.close_position:
            # closePosition closes the whole side at trigger and the exchange
            # rejects it beside quantity or reduceOnly, in either position mode.
            params["closePosition"] = "true"
        else:
            params["quantity"] = intent.qty
        if intent.position_side is None:
            if not intent.close_position:
                params["reduceOnly"] = intent.reduce_only
        else:
            # Hedge-mode: positionSide carries the direction and the account
            # REJECTS reduceOnly as a parameter (the side implies it).
            params["positionSide"] = intent.position_side
        if intent.order_type == "LIMIT":
            params["price"] = price
            params["timeInForce"] = "GTX"
        if conditional:
            # python-binance renames stopPrice to the algo route's triggerPrice.
            # MARK_PRICE so a last-price wick does not fire the stop.
            params["stopPrice"] = intent.stop_price
            params["workingType"] = "MARK_PRICE"
        if intent.client_order_id is not None:
            # python-binance 1.0.37 DROPS newClientOrderId on the algo route and
            # invents a random clientAlgoId, so a conditional must name its id
            # under the algo route's own key or the id is silently lost.
            key = "clientAlgoId" if conditional else "newClientOrderId"
            params[key] = intent.client_order_id
        resp = self.client.futures_create_order(**params)
        return require_ack(intent.symbol, intent.order_type, resp)

    def cancel_order(
        self, symbol: str, *, order_id: int | None = None, algo_id: int | None = None
    ) -> None:
        """Cancel ONE order by id: `algo_id` for a conditional, `order_id` otherwise.

        By id rather than symbol-wide, so it can never touch an order the
        caller did not place.
        """
        if (order_id is None) == (algo_id is None):
            raise ValueError("pass exactly one of order_id / algo_id")
        if self.mode == "dry_run":
            return
        if algo_id is not None:
            self.client.futures_cancel_order(symbol=symbol, algoId=algo_id)
        else:
            self.client.futures_cancel_order(symbol=symbol, orderId=order_id)

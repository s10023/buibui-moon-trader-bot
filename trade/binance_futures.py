"""Binance USDT-M Futures I/O adapter for the XS-solo executor.

Thin, injectable wrapper over a `python-binance` Client. Read methods always
hit the API; write methods (`ensure_account_config`, `submit`) are
no-op-and-log when `mode == "dry_run"`. The client is constructed by the CLI
(mainnet for dry_run/live, testnet client for testnet) and injected here, so
this class is unit-testable with a MagicMock.
"""

from __future__ import annotations

from typing import Any

from trade.routing import ExchangeFilters, OrderIntent

try:  # pragma: no cover - import shape depends on python-binance version
    from binance.exceptions import APIError
except Exception:  # pragma: no cover

    class APIError(Exception):  # type: ignore[no-redef]
        code = 0


_MARGIN_TYPE_UNCHANGED = -4046


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
        """
        if intent.order_type == "LIMIT" and price is None:
            raise ValueError(f"LIMIT order requires a price: {intent.symbol}")
        if self.mode == "dry_run":
            return {
                "dryRun": True,
                "symbol": intent.symbol,
                "side": intent.side,
                "qty": intent.qty,
                "reduceOnly": intent.reduce_only,
                "orderType": intent.order_type,
                "price": price,
            }
        params: dict[str, Any] = {
            "symbol": intent.symbol,
            "side": intent.side,
            "type": intent.order_type,
            "quantity": intent.qty,
            "reduceOnly": intent.reduce_only,
        }
        if intent.order_type == "LIMIT":
            params["price"] = price
            params["timeInForce"] = "GTX"
        return self.client.futures_create_order(**params)  # type: ignore[no-any-return]

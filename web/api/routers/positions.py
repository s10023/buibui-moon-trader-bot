"""Positions router — GET /api/positions + write actions."""

import logging
import re
from typing import Any

from binance.client import Client
from fastapi import APIRouter, Depends, HTTPException, status

from monitor.position_lib import fetch_open_positions
from utils.binance_client import load_coins_config
from web.api.deps import get_client, require_token
from web.api.models.positions import (
    ActionResponse,
    ClosePositionRequest,
    ModifySlRequest,
    ModifyTpRequest,
    PositionRow,
    PositionsResponse,
)

router = APIRouter(dependencies=[Depends(require_token)])

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: Any) -> str:
    """Remove ANSI escape codes from a string."""
    if not isinstance(s, str):
        return str(s)
    return _ANSI_RE.sub("", s)


def _parse_float_or_none(val: Any) -> float | None:
    """Parse a value as float, returning None for '-' or unparseable.

    Strips ANSI codes and formatting characters that colorize() adds:
    thousand-separator commas, trailing '%', and '$' signs.
    """
    s = _strip_ansi(val).replace(",", "").replace("%", "").replace("$", "").strip()
    if s in ("-", "+", "", "None"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _parse_int_or_none(val: Any) -> int | None:
    """Parse a value as int, returning None for '-' or unparseable."""
    s = _strip_ansi(val)
    if s in ("-", "", "None"):
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


@router.get("/positions", response_model=PositionsResponse)
def get_positions(client: Client = Depends(get_client)) -> PositionsResponse:
    """Fetch and return open futures positions."""
    try:
        coins = load_coins_config()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to load coins config: {exc}",
        ) from exc

    coin_order = list(coins.keys())

    try:
        rows, total_risk_usd, wallet_balance, unrealized_pnl, available_balance = (
            fetch_open_positions(client, coins, coin_order, hide_empty=True)
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    positions: list[PositionRow] = []
    for row in rows:
        # Row layout (13 elements, ANSI-colored strings):
        # 0: symbol, 1: side, 2: leverage, 3: entry, 4: mark, 5: margin,
        # 6: notional, 7: pnl ($), 8: pnl_pct (%), 9: risk_pct, 10: sl_price,
        # 11: sl_size, 12: sl_usd
        sl_price_raw = _strip_ansi(row[10])
        sl_price: float | None = None
        if sl_price_raw not in ("-", "", "None"):
            try:
                sl_price = float(sl_price_raw)
            except (ValueError, TypeError):
                sl_price = None

        positions.append(
            PositionRow(
                symbol=str(row[0]),
                side=_strip_ansi(row[1]),
                leverage=_parse_int_or_none(row[2]),
                entry_price=_parse_float_or_none(row[3]),
                mark_price=_parse_float_or_none(row[4]),
                margin=_parse_float_or_none(row[5]),
                notional=_parse_float_or_none(row[6]),
                pnl=_parse_float_or_none(row[7]),
                pnl_pct=_parse_float_or_none(row[8]),
                risk_pct=_strip_ansi(row[9]) if row[9] != "-" else None,
                sl_price=sl_price,
                sl_size=_strip_ansi(row[11]) if row[11] != "-" else None,
                sl_usd=_strip_ansi(row[12]) if row[12] != "-" else None,
            )
        )

    return PositionsResponse(
        positions=positions,
        wallet_balance=wallet_balance,
        unrealized_pnl=unrealized_pnl,
        available_balance=available_balance,
        total_risk_usd=total_risk_usd,
    )


# ── Write actions ─────────────────────────────────────────────────────────────


def _binance_side(position_side: str) -> str:
    """Return the order side required to close a position.

    LONG positions are closed by a SELL; SHORT positions by a BUY.
    BOTH (one-way mode) is closed by inspecting positionAmt sign — but
    since we already know side from the UI, default to SELL for BOTH as
    the caller should supply the correct direction in one-way mode.
    """
    return "BUY" if position_side.upper() == "SHORT" else "SELL"


@router.post("/positions/close", response_model=ActionResponse)
def close_position(
    req: ClosePositionRequest,
    client: Client = Depends(get_client),
) -> ActionResponse:
    """Market-close an open futures position (reduce-only)."""
    try:
        positions = client.futures_position_information(symbol=req.symbol)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to fetch position: {exc}",
        ) from exc

    # Find the matching position and read its current quantity.
    amt: float = 0.0
    for pos in positions:
        ps = pos.get("positionSide", "BOTH")
        if ps == req.position_side or (req.position_side == "BOTH" and ps == "BOTH"):
            amt = abs(float(pos.get("positionAmt", 0)))
            break

    if amt == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No open {req.position_side} position for {req.symbol}",
        )

    side = _binance_side(req.position_side)
    try:
        client.futures_create_order(
            symbol=req.symbol,
            side=side,
            type="MARKET",
            quantity=amt,
            reduceOnly=True,
            positionSide=req.position_side,
        )
    except Exception as exc:
        logging.error("close_position failed for %s: %s", req.symbol, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return ActionResponse(ok=True, detail=f"Closed {req.position_side} {req.symbol}")


def _cancel_existing_orders(
    client: Client, symbol: str, position_side: str, order_types: list[str]
) -> None:
    """Cancel all open orders of the given types for symbol + position_side."""
    try:
        open_orders: list[dict[str, Any]] = client.futures_get_open_orders(
            symbol=symbol
        )
    except Exception as exc:
        logging.warning("Could not fetch open orders for %s: %s", symbol, exc)
        return

    for o in open_orders:
        if o.get("type") not in order_types:
            continue
        ps = o.get("positionSide", "BOTH")
        if ps not in (position_side, "BOTH"):
            continue
        try:
            client.futures_cancel_order(symbol=symbol, orderId=o["orderId"])
        except Exception as exc:
            logging.warning(
                "Could not cancel order %s for %s: %s", o["orderId"], symbol, exc
            )


@router.post("/positions/sl", response_model=ActionResponse)
def modify_sl(
    req: ModifySlRequest,
    client: Client = Depends(get_client),
) -> ActionResponse:
    """Place (or replace) a STOP_MARKET stop-loss order for an open position."""
    # Cancel any existing SL order first so we don't leave orphans.
    _cancel_existing_orders(
        client, req.symbol, req.position_side, ["STOP_MARKET", "STOP"]
    )

    side = _binance_side(req.position_side)
    try:
        client.futures_create_order(
            symbol=req.symbol,
            side=side,
            type="STOP_MARKET",
            stopPrice=req.stop_price,
            closePosition=True,
            positionSide=req.position_side,
            workingType="MARK_PRICE",
        )
    except Exception as exc:
        logging.error("modify_sl failed for %s: %s", req.symbol, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return ActionResponse(
        ok=True,
        detail=f"SL set to {req.stop_price} for {req.position_side} {req.symbol}",
    )


@router.post("/positions/tp", response_model=ActionResponse)
def modify_tp(
    req: ModifyTpRequest,
    client: Client = Depends(get_client),
) -> ActionResponse:
    """Place (or replace) a TAKE_PROFIT_MARKET take-profit order for an open position."""
    # Cancel any existing TP order first.
    _cancel_existing_orders(
        client,
        req.symbol,
        req.position_side,
        ["TAKE_PROFIT_MARKET", "TAKE_PROFIT"],
    )

    side = _binance_side(req.position_side)
    try:
        client.futures_create_order(
            symbol=req.symbol,
            side=side,
            type="TAKE_PROFIT_MARKET",
            stopPrice=req.stop_price,
            closePosition=True,
            positionSide=req.position_side,
            workingType="MARK_PRICE",
        )
    except Exception as exc:
        logging.error("modify_tp failed for %s: %s", req.symbol, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return ActionResponse(
        ok=True,
        detail=f"TP set to {req.stop_price} for {req.position_side} {req.symbol}",
    )


@router.delete("/orders/{order_id}", response_model=ActionResponse)
def cancel_order(
    order_id: int,
    symbol: str,
    client: Client = Depends(get_client),
) -> ActionResponse:
    """Cancel an open futures order by orderId."""
    try:
        client.futures_cancel_order(symbol=symbol, orderId=order_id)
    except Exception as exc:
        logging.error("cancel_order failed for orderId=%s: %s", order_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return ActionResponse(ok=True, detail=f"Order {order_id} cancelled")

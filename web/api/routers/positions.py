"""Positions router — GET /api/positions."""

import re
from typing import Any

from binance.client import Client
from fastapi import APIRouter, Depends, HTTPException, status

from monitor.position_lib import fetch_open_positions
from utils.binance_client import load_coins_config
from web.api.deps import get_client, require_token
from web.api.models.positions import PositionRow, PositionsResponse

router = APIRouter(dependencies=[Depends(require_token)])

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: Any) -> str:
    """Remove ANSI escape codes from a string."""
    if not isinstance(s, str):
        return str(s)
    return _ANSI_RE.sub("", s)


def _parse_float_or_none(val: Any) -> float | None:
    """Parse a value as float, returning None for '-' or unparseable."""
    s = _strip_ansi(val)
    if s in ("-", "", "None"):
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

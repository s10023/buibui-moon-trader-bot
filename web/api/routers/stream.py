"""SSE streaming router — GET /api/stream/prices, /api/stream/positions."""

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

from binance.client import Client
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from monitor.position_lib import fetch_open_positions
from monitor.price_lib import get_price_changes
from utils.binance_client import load_coins_config
from web.api.deps import get_client, require_token_sse
from web.api.models.positions import PositionRow, PositionsResponse
from web.api.routers.positions import (
    _parse_float_or_none,
    _parse_int_or_none,
    _strip_ansi,
)

router = APIRouter()


def _safe_load_symbols() -> tuple[list[str], dict[str, Any]]:
    """Load coins config, returning (symbols, coins_dict). Returns empty on error."""
    try:
        coins = load_coins_config()
        return list(coins.keys()), coins
    except Exception:
        return [], {}


async def _price_event_generator(client: Client) -> AsyncGenerator[str, None]:
    """Yield SSE price events every 5 seconds."""
    try:
        while True:
            symbols, _ = _safe_load_symbols()
            if symbols:
                table, _ = await asyncio.get_running_loop().run_in_executor(
                    None, get_price_changes, client, symbols, True
                )
                data = [
                    {
                        "symbol": str(row[0]),
                        "last_price": str(row[1]),
                        "change_15m": str(row[2]),
                        "change_1h": str(row[3]),
                        "change_4h": str(row[4]),
                        "change_asia": str(row[5]),
                        "change_24h": str(row[6]),
                    }
                    for row in table
                ]
                yield f"data: {json.dumps(data)}\n\n"
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        return


async def _positions_event_generator(client: Client) -> AsyncGenerator[str, None]:
    """Yield SSE position events every 10 seconds."""
    try:
        while True:
            symbols, coins = _safe_load_symbols()
            if coins:
                try:
                    (
                        rows,
                        total_risk_usd,
                        wallet,
                        unrealized,
                        available,
                    ) = await asyncio.get_running_loop().run_in_executor(
                        None,
                        fetch_open_positions,
                        client,
                        coins,
                        symbols,
                    )
                    positions = [
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
                            sl_price=_parse_float_or_none(row[10]),
                            sl_size=_strip_ansi(row[11]) if row[11] != "-" else None,
                            sl_usd=_strip_ansi(row[12]) if row[12] != "-" else None,
                        )
                        for row in rows
                    ]
                    data = PositionsResponse(
                        positions=positions,
                        wallet_balance=wallet,
                        unrealized_pnl=unrealized,
                        available_balance=available,
                        total_risk_usd=total_risk_usd,
                    ).model_dump()
                    yield f"data: {json.dumps(data)}\n\n"
                except Exception:
                    pass
            await asyncio.sleep(10)
    except asyncio.CancelledError:
        return


@router.get("/stream/prices", dependencies=[Depends(require_token_sse)])
def stream_prices(client: Client = Depends(get_client)) -> StreamingResponse:
    """Stream live price changes as Server-Sent Events (every 5s)."""
    return StreamingResponse(
        _price_event_generator(client),
        media_type="text/event-stream",
    )


@router.get("/stream/positions", dependencies=[Depends(require_token_sse)])
def stream_positions(client: Client = Depends(get_client)) -> StreamingResponse:
    """Stream live position data as Server-Sent Events (every 10s)."""
    return StreamingResponse(
        _positions_event_generator(client),
        media_type="text/event-stream",
    )

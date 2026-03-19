"""Prices router — GET /api/prices."""

from typing import Any

from binance.client import Client
from fastapi import APIRouter, Depends, HTTPException, status

from monitor.price_lib import get_price_changes
from utils.binance_client import load_coins_config
from web.api.deps import get_client, require_token
from web.api.models.prices import PriceRow, PricesResponse

router = APIRouter(dependencies=[Depends(require_token)])


def _cell_to_str(val: Any) -> str:
    """Convert a table cell (possibly colored string) to plain string."""
    return str(val)


@router.get("/prices", response_model=PricesResponse)
def get_prices(client: Client = Depends(get_client)) -> PricesResponse:
    """Return latest price changes for all configured symbols."""
    try:
        coins = load_coins_config()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to load coins config: {exc}",
        ) from exc

    symbols = list(coins.keys())
    # Use telegram=True so we get plain text (no ANSI color codes)
    table, _invalid = get_price_changes(client, symbols, telegram=True)

    # Table row layout: [symbol, last_price, 15m%, 1h%, 4h%, asia%, 24h%]
    rows = [
        PriceRow(
            symbol=str(row[0]),
            last_price=_cell_to_str(row[1]),
            change_15m=_cell_to_str(row[2]),
            change_1h=_cell_to_str(row[3]),
            change_4h=_cell_to_str(row[4]),
            change_asia=_cell_to_str(row[5]),
            change_24h=_cell_to_str(row[6]),
        )
        for row in table
    ]

    return PricesResponse(prices=rows)

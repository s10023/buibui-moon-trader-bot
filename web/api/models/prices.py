"""Pydantic models for prices endpoint."""

from pydantic import BaseModel


class PriceRow(BaseModel):
    symbol: str
    last_price: str
    change_15m: str
    change_1h: str
    change_4h: str
    change_asia: str
    change_24h: str


class PricesResponse(BaseModel):
    prices: list[PriceRow]

"""Pydantic models for signals endpoint."""

from pydantic import BaseModel


class SignalsRequest(BaseModel):
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    strategies: list[str]


class SignalRow(BaseModel):
    open_time: int
    direction: str
    strategy: str
    reason: str
    sl_price: float
    entry_price: float | None = None
    confidence: int
    context: str


class SignalsResponse(BaseModel):
    signals: list[SignalRow]

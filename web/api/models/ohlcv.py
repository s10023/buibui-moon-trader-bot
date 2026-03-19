"""Pydantic models for OHLCV endpoint."""

from pydantic import BaseModel, ConfigDict


class CandleRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float | None = None  # present only after CVD schema migration


class FundingRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    funding_time: int
    funding_rate: float


class OhlcvResponse(BaseModel):
    candles: list[CandleRow]
    funding: list[FundingRow] | None = None

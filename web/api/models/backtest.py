"""Pydantic models for backtest endpoint."""

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class BacktestRunSummary(BaseModel):
    run_id: str
    symbol: str
    timeframe: str
    strategy: str
    days: int
    sl_pct: float
    tp_r: float
    fee_pct: float
    day_filter: str
    closed_trades: int
    win_count: int
    loss_count: int
    win_rate: float
    avg_r: float
    total_r: float
    max_drawdown_r: float
    sweep_id: str | None
    run_at_ms: int

    @field_validator("sweep_id", mode="before")
    @classmethod
    def _nan_to_none(cls, v: Any) -> str | None:
        """Pandas returns NaN for NULL TEXT columns; coerce to None."""
        if isinstance(v, float) and math.isnan(v):
            return None
        return str(v) if v is not None else None


class BacktestRequest(BaseModel):
    symbol: str
    timeframe: str
    strategy: str
    days: int = 90
    sl_pct: float = 0.02
    tp_r: float = 2.0
    fee_pct: float = 0.0
    secondary_symbol: str | None = None


class TradeModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    signal_time: int
    entry_time: int
    entry_price: float
    direction: str
    sl_price: float
    tp_price: float
    exit_time: int | None
    exit_price: float | None
    outcome: str
    pnl_r: float | None


class BacktestResponse(BaseModel):
    symbol: str
    timeframe: str
    strategy: str
    total_trades: int
    closed_trades: int
    win_count: int
    loss_count: int
    win_rate: float
    avg_r: float
    total_r: float
    max_drawdown_r: float
    trades: list[TradeModel]

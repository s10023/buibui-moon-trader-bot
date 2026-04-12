"""Pydantic models for backtest endpoint."""

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


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
    recovery_factor: float | None = None
    sweep_id: str | None
    run_at_ms: int
    long_closed_trades: int | None = None
    long_win_count: int | None = None
    long_win_rate: float | None = None
    long_avg_r: float | None = None
    long_total_r: float | None = None
    short_closed_trades: int | None = None
    short_win_count: int | None = None
    short_win_rate: float | None = None
    short_avg_r: float | None = None
    short_total_r: float | None = None
    adr_suppress_threshold: float | None = None
    stars: int | None = None
    long_stars: int | None = None
    short_stars: int | None = None

    @field_validator("sweep_id", mode="before")
    @classmethod
    def _sweep_nan_to_none(cls, v: Any) -> str | None:
        """Pandas returns NaN for NULL TEXT columns; coerce to None."""
        if isinstance(v, float) and math.isnan(v):
            return None
        return str(v) if v is not None else None

    @model_validator(mode="before")
    @classmethod
    def _float_nan_to_none(cls, data: Any) -> Any:
        """Coerce pandas NaN to None for nullable float/int columns."""
        if isinstance(data, dict):
            nullable_cols = (
                "long_win_rate",
                "long_avg_r",
                "long_total_r",
                "short_win_rate",
                "short_avg_r",
                "short_total_r",
                "adr_suppress_threshold",
                "recovery_factor",
                "stars",
                "long_stars",
                "short_stars",
            )
            for key in nullable_cols:
                v = data.get(key)
                if isinstance(v, float) and math.isnan(v):
                    data[key] = None
        return data


class BacktestRequest(BaseModel):
    symbol: str
    timeframe: str
    strategy: str
    days: int = 90
    since: str | None = None
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
    recovery_factor: float
    long_closed_trades: int
    long_win_count: int
    long_win_rate: float | None
    long_avg_r: float | None
    long_total_r: float | None
    short_closed_trades: int
    short_win_count: int
    short_win_rate: float | None
    short_avg_r: float | None
    short_total_r: float | None
    trades: list[TradeModel]

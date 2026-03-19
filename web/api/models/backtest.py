"""Pydantic models for backtest endpoint."""

from pydantic import BaseModel, ConfigDict


class BacktestRequest(BaseModel):
    symbol: str
    timeframe: str
    strategy: str
    days: int = 90
    sl_pct: float = 0.02
    tp_r: float = 2.0
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

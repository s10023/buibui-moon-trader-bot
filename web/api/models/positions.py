"""Pydantic models for positions endpoint."""

from pydantic import BaseModel


class PositionRow(BaseModel):
    symbol: str
    side: str
    leverage: int | None
    entry_price: float | None
    mark_price: float | None
    margin: float | None
    notional: float | None
    pnl: float | None = None
    pnl_pct: float | None = None
    risk_pct: str | None = None
    sl_price: float | None = None
    sl_size: str | None = None
    sl_usd: str | None = None


class PositionsResponse(BaseModel):
    positions: list[PositionRow]
    wallet_balance: float
    unrealized_pnl: float
    available_balance: float
    total_risk_usd: float


# ── Write action request/response models ──────────────────────────────────────


class ClosePositionRequest(BaseModel):
    symbol: str
    position_side: str  # "LONG", "SHORT", or "BOTH"


class ModifySlRequest(BaseModel):
    symbol: str
    position_side: str  # "LONG", "SHORT", or "BOTH"
    stop_price: float


class ModifyTpRequest(BaseModel):
    symbol: str
    position_side: str  # "LONG", "SHORT", or "BOTH"
    stop_price: float


class ActionResponse(BaseModel):
    ok: bool
    detail: str

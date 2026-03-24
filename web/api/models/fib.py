"""Pydantic models for Fibonacci retracement endpoint."""

from pydantic import BaseModel


class FibLevel(BaseModel):
    """A single Fibonacci level with its price and metadata."""

    label: str
    price: float
    golden: bool


class FibResponse(BaseModel):
    """Response for GET /api/fib — swing points and full Fib grid."""

    swing_low: float
    swing_high: float
    swing_start_ms: int
    levels: list[FibLevel]

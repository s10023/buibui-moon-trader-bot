"""Pydantic models for structural zone overlay responses."""

from pydantic import BaseModel


class ZoneBox(BaseModel):
    """A price-range box zone (FVG, Order Block, Fib Golden Zone, OTE)."""

    zone_type: str  # "fvg" | "ob" | "fib_zone" | "ote"
    direction: str  # "bull" | "bear"
    zone_low: float
    zone_high: float
    start_ms: int  # Unix ms — when the zone formed
    active: bool  # False when filled / mitigated


class ZoneLine(BaseModel):
    """A horizontal price line zone (EQH, EQL, BOS structural level)."""

    zone_type: str  # "eqh" | "eql" | "bos"
    direction: str  # "bull" | "bear"
    price: float
    start_ms: int
    close_ms: int | None = None  # Unix ms when swept/broken; None if still active
    label: str  # short display label, e.g. "EQH", "S", "R"
    active: bool


class SwingPoint(BaseModel):
    """A single swing high or low pivot point."""

    swing_type: str  # "high" | "low"
    price: float
    time_ms: int


class ZonesResponse(BaseModel):
    boxes: list[ZoneBox]
    lines: list[ZoneLine]
    swings: list[SwingPoint]

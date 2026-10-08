"""Pydantic models for the Chart-tab location overlay (GET /api/location).

Display only, never a gate — see ``web/api/chart_location.py``.
"""

from pydantic import BaseModel


class VwapPoint(BaseModel):
    time_ms: int  # bar open_time, Unix ms
    anchor_ms: int  # window start; a change marks a reset (the UI breaks the line)
    value: float


class VwapSeries(BaseModel):
    anchor: str  # "day" | "week" | "month"
    points: list[VwapPoint]


class ProfileLevelsModel(BaseModel):
    """60-day volume-profile levels over completed 1h bars (the brief's window)."""

    poc: float
    vah: float
    val: float
    window_start_ms: int
    window_end_ms: int


class LocationResponse(BaseModel):
    vwap: list[VwapSeries]
    profile: ProfileLevelsModel | None
    note: str  # always the display-only marker

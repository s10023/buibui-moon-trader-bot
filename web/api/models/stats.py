"""Pydantic models for the stats router."""

from pydantic import BaseModel


class P1P2DOWRow(BaseModel):
    dow: str
    p1_low_pct: float
    sample_days: int


class P1P2Response(BaseModel):
    overall_p1_low_pct: float
    by_dow: list[P1P2DOWRow]
    sample_days: int
    p1_strong_pct: float = 0.0


class HourlyExtremeRow(BaseModel):
    hour_myt: int
    high_pct: float
    low_pct: float


class ADRResponse(BaseModel):
    adr_14: float
    adr_30: float
    today_range_pct: float | None
    today_consumed_pct: float | None


class DOWPatternRow(BaseModel):
    dow: str
    avg_range_pct: float
    bull_pct: float
    sample_days: int
    avg_return_pct: float
    strong_high_pct: float = 0.0
    strong_low_pct: float = 0.0


class SessionRow(BaseModel):
    session: str
    high_pct: float
    low_pct: float


class WeeklyP1P2Response(BaseModel):
    overall_p1_low_pct: float
    low_day: str
    high_day: str
    sample_weeks: int
    low_by_dow: dict[str, float]
    high_by_dow: dict[str, float]


class WeeklyP2TimingResponse(BaseModel):
    low_still_ahead_by_dow: dict[str, float]
    high_still_ahead_by_dow: dict[str, float]
    low_flip_risk_by_dow: dict[str, float]
    high_flip_risk_by_dow: dict[str, float]


class WeeklyCurrentStateResponse(BaseModel):
    current_isodow: int
    current_dow: str
    weekly_open: float
    current_price: float
    move_pct: float
    move_bucket: str
    low_still_ahead_conditioned: float | None
    high_still_ahead_conditioned: float | None


class FlipRiskConditionedRow(BaseModel):
    p1_direction: str
    isodow: int
    dow_label: str
    flip_pct: float
    sample_count: int


class WeeklyFlipRiskConditionedResponse(BaseModel):
    rows: list[FlipRiskConditionedRow]


class DailyDistanceResponse(BaseModel):
    exceedance_pct: float
    p80_of_adr: float
    gap_to_p80: float | None
    sample_count: int


class WeeklyWickPercentileResponse(BaseModel):
    current_wick_of_adr: float | None
    exceedance_pct: float | None
    p1_direction: str | None
    sample_count: int


class StatsResponse(BaseModel):
    symbol: str
    days: int
    computed_at_ms: int
    p1p2: P1P2Response
    hourly_extremes: list[HourlyExtremeRow]
    adr: ADRResponse
    dow_patterns: list[DOWPatternRow]
    sessions: list[SessionRow]
    weekly_p1p2: WeeklyP1P2Response
    weekly_p2_timing: WeeklyP2TimingResponse
    weekly_current_state: WeeklyCurrentStateResponse | None = None
    weekly_flip_risk_conditioned: WeeklyFlipRiskConditionedResponse | None = None
    daily_distance: DailyDistanceResponse | None = None
    weekly_wick_percentile: WeeklyWickPercentileResponse | None = None

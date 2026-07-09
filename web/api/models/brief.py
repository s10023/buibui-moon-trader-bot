"""Pydantic models for the brief router (mirror of analytics.brief.types)."""

from pydantic import BaseModel


class LevelRowModel(BaseModel):
    name: str
    price: float
    dist_atr: float
    swept: bool


class ZoneRowModel(BaseModel):
    tf: str
    zone_type: str
    direction: str
    zone_low: float
    zone_high: float
    dist_atr: float
    inside: bool


class SeasonalityStripModel(BaseModel):
    dow: str
    bull_pct: float | None
    avg_range_pct: float | None
    sample_days: int | None
    high_session: str | None
    high_session_pct: float | None
    low_session: str | None
    low_session_pct: float | None
    weekly_low_still_ahead: float | None
    weekly_high_still_ahead: float | None
    typical_low_day: str | None
    typical_high_day: str | None


class EmaStateModel(BaseModel):
    above_20: bool | None
    above_50: bool | None
    above_200: bool | None
    stack: str | None
    slope_200: str | None


class RangeStateModel(BaseModel):
    label: str
    since_ms: int
    bars: int
    range_low: float | None
    range_high: float | None
    pos: float | None


class MondayStateModel(BaseModel):
    state: str
    pos: float | None


class CandleHitModel(BaseModel):
    pattern: str
    direction: str


class PaStateModel(BaseModel):
    label: str
    er: float
    speed_atr: float


class BbStateModel(BaseModel):
    pct_b: float
    bandwidth: float
    bw_pctile: float | None
    squeeze: bool | None


class VwapStateModel(BaseModel):
    weekly_price: float | None
    weekly_dist_atr: float | None
    monthly_price: float | None
    monthly_dist_atr: float | None


class ProfileStateModel(BaseModel):
    poc: float
    vah: float
    val: float
    vs_value: str
    poc_dist_atr: float


class IndicatorStateModel(BaseModel):
    ema: EmaStateModel | None
    range_state: RangeStateModel | None
    monday: MondayStateModel | None
    candles: list[CandleHitModel] | None
    pa: PaStateModel | None
    bb: BbStateModel | None
    vwap: VwapStateModel | None
    profile: ProfileStateModel | None


class SymbolPanelModel(BaseModel):
    symbol: str
    ref_close: float
    ref_close_ts_ms: int
    ref_price_source: str
    atr14: float
    adr_pct: float | None
    regime_1d: str
    regime_4h: str
    levels_above: list[LevelRowModel]
    levels_below: list[LevelRowModel]
    zones_above: list[ZoneRowModel]
    zones_below: list[ZoneRowModel]
    seasonality: SeasonalityStripModel | None
    indicators: IndicatorStateModel | None
    error: str | None


class PunditAuthorPriorModel(BaseModel):
    author: str
    n: int
    hit_rate: float | None
    avg_r: float | None
    avg_atr_r: float | None
    flagged: bool


class PunditFamilyPriorModel(BaseModel):
    family: str
    direction: str
    n: int
    hit_rate: float | None
    avg_r: float | None
    avg_atr_r: float | None
    flagged: bool


class PunditCallRowModel(BaseModel):
    author: str
    symbol: str
    direction: str
    entry: str
    target: str
    horizon: str
    age_days: int
    on_panel: bool
    prior: PunditAuthorPriorModel | None


class PunditBoardModel(BaseModel):
    priors_status: str
    priors_age_days: int | None
    min_n_marker: int | None
    ledger_status: str
    ledger_total: int
    ledger_skipped: int
    recent_calls: list[PunditCallRowModel]
    authors: list[PunditAuthorPriorModel]
    families: list[PunditFamilyPriorModel]


class HealthRowModel(BaseModel):
    symbol: str
    tf: str
    status: str
    bars_behind: int


class HealthReportModel(BaseModel):
    rows: list[HealthRowModel]
    notes: list[str]
    data_ok: bool


class BriefResponse(BaseModel):
    as_of_ms: int
    day_ahead: str
    panels: list[SymbolPanelModel]
    pundit: PunditBoardModel
    health: HealthReportModel

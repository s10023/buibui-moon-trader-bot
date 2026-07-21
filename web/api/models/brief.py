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


class SessionClockModel(BaseModel):
    label: str
    start_ms: int
    end_ms: int
    is_overlap: bool
    next_label: str
    next_start_ms: int


class SessionRecapRowModel(BaseModel):
    session: str
    start_ms: int
    end_ms: int
    open: float
    high: float
    low: float
    close: float
    net_pct: float
    net_atr: float | None
    range_atr: float | None
    n_bars: int
    expected_bars: int
    made_set_high: bool
    made_set_low: bool


class SessionTendencyRowModel(BaseModel):
    session: str
    high_pct: float
    low_pct: float


class SessionStateModel(BaseModel):
    recap: list[SessionRecapRowModel] | None
    tendency: list[SessionTendencyRowModel] | None


class ExternalClusterRowModel(BaseModel):
    price_lo: float
    price_hi: float
    kind: str
    intensity: str
    label: str
    dist_atr: float


class ExternalSnapshotModel(BaseModel):
    source: str
    venue: str | None
    panel: str
    window: str | None
    scope: str | None
    captured_at_ms: int
    age_hours: float
    spot_price_hint: float | None
    spot_hint_deviation: bool
    clusters_above: list[ExternalClusterRowModel]
    clusters_below: list[ExternalClusterRowModel]


class ExternalStateModel(BaseModel):
    snapshots: list[ExternalSnapshotModel]


class WeeklyStateModel(BaseModel):
    path_direction: str
    elapsed_h: int
    total_bars: int
    norm_now: float
    pct_conditional: float
    pct_unconditional: float
    n_conditional: int
    n_unconditional: int
    low_hour: int | None
    high_hour: int | None
    low_in_by_now: float
    conditional_is_fallback: bool


class MonthlyContextModel(BaseModel):
    mtd_return_pct: float
    mtd_elapsed_frac: float
    pct_of_months: float | None
    n_months: int
    range_position: float | None


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
    sessions: SessionStateModel | None
    error: str | None
    external: ExternalStateModel | None = None
    weekly: WeeklyStateModel | None = None
    monthly: MonthlyContextModel | None = None


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
    session_clock: SessionClockModel | None
    pundit: PunditBoardModel
    health: HealthReportModel

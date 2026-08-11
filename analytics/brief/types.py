"""Brief dataclasses + JSON-safe serialisation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class LevelRow:
    name: str
    price: float
    dist_atr: float
    swept: bool


@dataclass(frozen=True)
class ZoneRow:
    tf: str
    zone_type: str
    direction: str
    zone_low: float
    zone_high: float
    dist_atr: float
    inside: bool


@dataclass(frozen=True)
class SeasonalityStrip:
    dow: str
    bull_pct: float | None
    avg_range_pct: float | None
    median_range_pct: float | None
    sample_days: int | None
    high_session: str | None
    high_session_pct: float | None
    low_session: str | None
    low_session_pct: float | None
    weekly_low_still_ahead: float | None
    weekly_high_still_ahead: float | None
    typical_low_day: str | None
    typical_high_day: str | None


@dataclass(frozen=True)
class EmaState:
    above_20: bool | None
    above_50: bool | None
    above_200: bool | None
    stack: str | None  # "bullish" | "bearish" | "mixed"
    slope_200: str | None  # "rising" | "falling"


@dataclass(frozen=True)
class RangeState:
    label: str  # regime label of the current run
    since_ms: int  # open_time of the run's first bar
    bars: int
    range_low: float | None  # only when label == "range"
    range_high: float | None
    pos: float | None  # ref position in the range, clipped [0, 1]


@dataclass(frozen=True)
class MondayState:
    state: str  # "above" | "inside" | "below" | "forming"
    pos: float | None  # fraction inside MonL..MonH, only for "inside"


@dataclass(frozen=True)
class CandleHit:
    pattern: str  # detector name, e.g. "engulfing"
    direction: str  # "long" | "short"


@dataclass(frozen=True)
class PaState:
    label: str  # impulse_up | impulse_down | grind_up | grind_down | chop
    er: float
    speed_atr: float


@dataclass(frozen=True)
class BbState:
    pct_b: float
    bandwidth: float
    bw_pctile: float | None
    squeeze: bool | None


@dataclass(frozen=True)
class VwapState:
    weekly_price: float | None
    weekly_dist_atr: float | None  # (ref - vwap) / atr: + = price above
    monthly_price: float | None
    monthly_dist_atr: float | None


@dataclass(frozen=True)
class ProfileState:
    poc: float
    vah: float
    val: float
    vs_value: str  # "above" | "inside" | "below"
    poc_dist_atr: float  # (poc - ref) / atr: + = POC above price


@dataclass(frozen=True)
class IndicatorState:
    ema: EmaState | None
    range_state: RangeState | None
    monday: MondayState | None
    candles: list[CandleHit] | None  # [] = no patterns (valid); None = failed
    pa: PaState | None
    bb: BbState | None
    vwap: VwapState | None
    profile: ProfileState | None


@dataclass(frozen=True)
class SessionClock:
    label: str  # "Asia" | "London" | "NY" | "Off"
    start_ms: int
    end_ms: int
    is_overlap: bool  # London 20:00-21:59 MYT
    next_label: str
    next_start_ms: int


@dataclass(frozen=True)
class SessionRecapRow:
    session: str
    start_ms: int
    end_ms: int
    open: float
    high: float
    low: float
    close: float
    net_pct: float  # (close - open) / open * 100
    net_atr: float | None  # (close - open) / atr14; None when atr14 <= 0
    range_atr: float | None  # (high - low) / atr14; None when atr14 <= 0
    n_bars: int
    expected_bars: int  # window hours: Asia 6 / London 8 / NY 6
    made_set_high: bool  # highest high across the recap rows present
    made_set_low: bool


@dataclass(frozen=True)
class SessionTendencyRow:
    session: str
    high_pct: float  # fraction of days this session made the daily high
    low_pct: float


@dataclass(frozen=True)
class SessionState:
    recap: list[SessionRecapRow] | None  # None = no window had bars
    tendency: list[SessionTendencyRow] | None  # None = stats compute failed


@dataclass(frozen=True)
class ExternalClusterRow:
    price_lo: float
    price_hi: float
    kind: str  # "liq" | "book"
    intensity: str  # "high" | "med" | "low"
    label: str
    dist_atr: float  # (band midpoint - ref) / atr14: + = above price


@dataclass(frozen=True)
class ExternalSnapshot:
    source: str  # "coinglass" | "mmt" (config-extensible)
    venue: (
        str | None
    )  # exchange the panel shows (e.g. "binance", "hyperliquid"); None = unspecified
    panel: str  # "liq_heatmap" | "book_heatmap" | "liq_map"
    window: str | None  # from the visible timeframe selector, e.g. "24h"
    scope: str | None  # "pair" | "agg"
    captured_at_ms: int
    age_hours: float
    spot_price_hint: float | None
    spot_hint_deviation: bool  # |hint - ref| / ref > 0.10
    clusters_above: list[ExternalClusterRow]  # nearest-first
    clusters_below: list[ExternalClusterRow]  # nearest-first


@dataclass(frozen=True)
class ExternalState:
    snapshots: list[ExternalSnapshot]


@dataclass(frozen=True)
class WeeklyState:
    """Where the forming week sits inside the weekly cone (M5, conditional-on-outcome)."""

    path_direction: str  # "bull" | "bear" | "flat" — the week SO FAR, not a forecast
    elapsed_h: int
    total_bars: int
    norm_now: float  # current normalized position (×AWR)
    pct_conditional: float  # percentile within same-direction weeks at this hour
    pct_unconditional: float  # percentile within all weeks at this hour
    n_conditional: int
    n_unconditional: int
    low_hour: int | None  # hour the week's low has been set so far
    high_hour: int | None
    low_in_by_now: (
        float  # fraction of same-direction weeks that had set their low by now
    )
    # True when the same-direction cohort could not be resolved distinctly
    # from the unconditional population: either "flat" has no cohort at all
    # (cone.combos only has all/bull/bear), or the bull/bear combo key exists
    # but is empty (n=0). In both cases pct_conditional/n_conditional are
    # just copies of the unconditional numbers. The renderer MUST key off
    # this flag — not off `path_direction == "flat"` — before presenting a
    # "weeks that closed X" cohort label (see C1, 2026-07-21 review).
    conditional_is_fallback: bool


@dataclass(frozen=True)
class MonthlyContext:
    """Descriptive monthly context — three numbers, deliberately NOT a cone."""

    mtd_return_pct: float
    mtd_elapsed_frac: float  # 0–1, how much of the month has elapsed
    pct_of_months: float | None  # rank of MTD return among COMPLETED prior months
    n_months: int
    range_position: float | None  # (price − low) / (high − low), None when flat


@dataclass(frozen=True)
class SymbolPanel:
    symbol: str
    ref_close: float
    ref_close_ts_ms: int
    ref_price_source: str  # "1h" | "1d_forming" | "1d_close"
    atr14: float
    adr_pct: float | None
    regime_1d: str
    regime_4h: str
    levels_above: list[LevelRow]
    levels_below: list[LevelRow]
    zones_above: list[ZoneRow]
    zones_below: list[ZoneRow]
    seasonality: SeasonalityStrip | None
    indicators: IndicatorState | None
    sessions: SessionState | None
    error: str | None
    external: ExternalState | None = None
    weekly: WeeklyState | None = None
    monthly: MonthlyContext | None = None


def error_panel(symbol: str, message: str) -> SymbolPanel:
    """Stub panel for a symbol that failed to compute (renders the error)."""
    return SymbolPanel(
        symbol=symbol,
        ref_close=0.0,
        ref_close_ts_ms=0,
        ref_price_source="1d_close",
        atr14=0.0,
        adr_pct=None,
        regime_1d="unknown",
        regime_4h="unknown",
        levels_above=[],
        levels_below=[],
        zones_above=[],
        zones_below=[],
        seasonality=None,
        indicators=None,
        sessions=None,
        error=message,
    )


@dataclass(frozen=True)
class PunditAuthorPrior:
    author: str
    n: int
    hit_rate: float | None
    avg_r: float | None
    avg_atr_r: float | None
    flagged: bool
    # Share of RESOLVED calls `avg_r` was computed over. `avg_r` needs a stated
    # stop and winners disproportionately lack one, so without this the reader
    # pairs `avg_r` with `n` and compares authors whose coverage differs.
    # Defaults to None so a priors JSON written before 2026-08-11 still loads.
    r_coverage: float | None = None


@dataclass(frozen=True)
class PunditFamilyPrior:
    family: str
    direction: str
    n: int
    hit_rate: float | None
    avg_r: float | None
    avg_atr_r: float | None
    flagged: bool
    r_coverage: float | None = None


@dataclass(frozen=True)
class PunditCallRow:
    author: str
    symbol: str
    direction: str
    entry: str
    target: str
    horizon: str
    age_days: int
    on_panel: bool
    prior: PunditAuthorPrior | None


@dataclass(frozen=True)
class PunditBoard:
    priors_status: str  # "ok" | "absent" | "unreadable"
    priors_age_days: int | None
    min_n_marker: int | None
    ledger_status: str  # "ok" | "absent"
    ledger_total: int
    ledger_skipped: int
    recent_calls: list[PunditCallRow]
    authors: list[PunditAuthorPrior]
    families: list[PunditFamilyPrior]


@dataclass(frozen=True)
class HealthRow:
    symbol: str
    tf: str
    status: str  # "ok" | "stale" | "missing"
    bars_behind: int


@dataclass(frozen=True)
class HealthReport:
    rows: list[HealthRow]
    notes: list[str]
    data_ok: bool


@dataclass(frozen=True)
class BriefBundle:
    as_of_ms: int
    day_ahead: str  # e.g. "Fri 2026-07-04"
    session_clock: SessionClock | None
    panels: list[SymbolPanel]
    pundit: PunditBoard
    health: HealthReport


def bundle_to_dict(bundle: BriefBundle) -> dict[str, Any]:
    """JSON-safe dict for the API response and ``--json`` output."""
    return asdict(bundle)

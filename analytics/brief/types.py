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
    error: str | None


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


@dataclass(frozen=True)
class PunditFamilyPrior:
    family: str
    direction: str
    n: int
    hit_rate: float | None
    avg_r: float | None
    avg_atr_r: float | None
    flagged: bool


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
    panels: list[SymbolPanel]
    pundit: PunditBoard
    health: HealthReport


def bundle_to_dict(bundle: BriefBundle) -> dict[str, Any]:
    """JSON-safe dict for the API response and ``--json`` output."""
    return asdict(bundle)

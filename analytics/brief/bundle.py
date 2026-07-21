"""compute_brief — orchestrator assembling the BriefBundle (read-only)."""

from __future__ import annotations

import logging

import duckdb
import pandas as pd

from analytics.brief._common import (
    DAY_MS,
    TF_MS,
    BriefDataError,
    completed_bars,
    day_ahead_label,
)
from analytics.brief.config import BriefConfig
from analytics.brief.external import load_external_state
from analytics.brief.health import build_health
from analytics.brief.indicators import build_indicator_state
from analytics.brief.levels import adr_pct_14, atr14_wilder, build_level_rows
from analytics.brief.monthly import build_monthly_context
from analytics.brief.pundit import build_board
from analytics.brief.seasonality import build_strip
from analytics.brief.sessions import build_session_state
from analytics.brief.types import BriefBundle, SessionClock, SymbolPanel, error_panel
from analytics.brief.weekly import build_weekly_state
from analytics.brief.zones import build_zone_rows
from analytics.regime import classify_series
from analytics.session_windows import session_at
from analytics.stats.session import compute_session_breakdown
from analytics.stats.weekly_cone import compute_current_week_path, compute_weekly_cone
from analytics.store.market_data import get_ohlcv

logger = logging.getLogger(__name__)

_DAILY_FETCH_DAYS = 500  # regime 1d needs ~90d ATR history + EMA warmup
_H4_FETCH_DAYS = 200
_H1_FETCH_DAYS = 62  # 60d volume profile + monthly AVWAP + 2d margin
_MIN_DAILY_BARS = 15  # ATR14 + one reference bar
_REF_1H_MAX_LAG_MS = 2 * TF_MS["1h"]


def _regime_label(df: pd.DataFrame, timeframe: str) -> str:
    if df.empty:
        return "unknown"
    return str(classify_series(df, timeframe).iloc[-1])


def _session_clock(as_of_ms: int) -> SessionClock:
    cur = session_at(as_of_ms)
    return SessionClock(
        label=cur.label,
        start_ms=cur.start_ms,
        end_ms=cur.end_ms,
        is_overlap=cur.is_overlap,
        next_label=cur.next_label,
        next_start_ms=cur.next_start_ms,
    )


def _resolve_ref_price(
    completed_1h: pd.DataFrame,
    daily: pd.DataFrame,
    completed_1d: pd.DataFrame,
    as_of_ms: int,
) -> tuple[float, int, str]:
    """(price, bar_open_ms, source) — freshest available reference price.

    Chain: last completed 1h close if its close is <= 2h behind as_of;
    else the forming 1d bar's close (latest synced price); else the last
    completed 1d close (the pre-M0 behavior).
    """
    if not completed_1h.empty:
        bar = completed_1h.iloc[-1]
        close_ms = int(bar["open_time"]) + TF_MS["1h"]
        if as_of_ms - close_ms <= _REF_1H_MAX_LAG_MS:
            return float(bar["close"]), int(bar["open_time"]), "1h"
    if len(daily) > len(completed_1d):
        bar = daily.iloc[-1]
        return float(bar["close"]), int(bar["open_time"]), "1d_forming"
    bar = completed_1d.iloc[-1]
    return float(bar["close"]), int(bar["open_time"]), "1d_close"


def _compute_panel(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    cfg: BriefConfig,
    notes: list[str],
) -> SymbolPanel:
    as_of = cfg.as_of_ms
    daily = get_ohlcv(conn, symbol, "1d", as_of - _DAILY_FETCH_DAYS * DAY_MS, as_of)
    completed_1d = completed_bars(daily, "1d", as_of)
    if len(completed_1d) < _MIN_DAILY_BARS:
        raise BriefDataError(
            f"insufficient 1d history ({len(completed_1d)} completed bars)"
        )
    raw_1h = get_ohlcv(conn, symbol, "1h", as_of - _H1_FETCH_DAYS * DAY_MS, as_of)
    completed_1h = completed_bars(raw_1h, "1h", as_of)
    ref_close, ref_ts, ref_source = _resolve_ref_price(
        completed_1h, daily, completed_1d, as_of
    )
    if ref_source != "1h":
        notes.append(
            f"{symbol}: ref price fell back to {ref_source} (1h missing/stale)"
        )
    atr = atr14_wilder(completed_1d)
    levels_above, levels_below = build_level_rows(
        daily, as_of, ref_close, atr, cfg.max_levels_per_side
    )
    frames: dict[str, pd.DataFrame] = {}
    for tf in cfg.zone_tfs:
        if tf == "1d":
            frames[tf] = completed_1d
        else:
            raw = get_ohlcv(conn, symbol, tf, as_of - _H4_FETCH_DAYS * DAY_MS, as_of)
            frames[tf] = completed_bars(raw, tf, as_of)
    zones_above, zones_below = build_zone_rows(
        frames, ref_close, atr, cfg.max_zones_per_side
    )
    regime_series_1d = classify_series(completed_1d, "1d")
    indicators, ind_notes = build_indicator_state(
        completed_1d=completed_1d,
        completed_1h=completed_1h,
        regime_series_1d=regime_series_1d,
        ref_close=ref_close,
        atr14=atr,
        as_of_ms=as_of,
    )
    notes.extend(f"{symbol}: {n}" for n in ind_notes)
    try:
        tendency = compute_session_breakdown(conn, symbol, cfg.stats_days, end_ms=as_of)
    except Exception as exc:  # tendency is optional; recap may still render
        tendency = None
        notes.append(f"{symbol}: session tendency failed ({exc})")
    sessions, sess_notes = build_session_state(
        completed_1h=completed_1h,
        atr14=atr,
        as_of_ms=as_of,
        tendency=tendency,
    )
    notes.extend(f"{symbol}: {n}" for n in sess_notes)
    external, ext_notes = load_external_state(
        dir_path=cfg.external_dir,
        symbol=symbol,
        ref_close=ref_close,
        atr14=atr,
        as_of_ms=as_of,
        allowed_sources=cfg.external_allowed_sources,
        max_age_hours=cfg.external_max_age_hours,
        max_rows_per_side=cfg.external_max_rows_per_side,
    )
    notes.extend(f"{symbol}: {n}" for n in ext_notes)
    try:
        weekly_cone = compute_weekly_cone(conn, symbol, now_ms=as_of)
        current_week = compute_current_week_path(conn, symbol, now_ms=as_of)
        weekly, wk_notes = build_weekly_state(cone=weekly_cone, current=current_week)
    except Exception as exc:  # weekly block is optional
        weekly, wk_notes = None, [f"weekly cone failed ({exc})"]
    notes.extend(f"{symbol}: {n}" for n in wk_notes)
    try:
        monthly, mo_notes = build_monthly_context(
            completed_1d=completed_1d, as_of_ms=as_of
        )
    except Exception as exc:  # monthly block is optional
        monthly, mo_notes = None, [f"monthly context failed ({exc})"]
    notes.extend(f"{symbol}: {n}" for n in mo_notes)
    return SymbolPanel(
        symbol=symbol,
        ref_close=ref_close,
        ref_close_ts_ms=ref_ts,
        ref_price_source=ref_source,
        atr14=atr,
        adr_pct=adr_pct_14(completed_1d),
        regime_1d=str(regime_series_1d.iloc[-1]),
        regime_4h=_regime_label(frames.get("4h", pd.DataFrame()), "4h"),
        levels_above=levels_above,
        levels_below=levels_below,
        zones_above=zones_above,
        zones_below=zones_below,
        seasonality=build_strip(conn, symbol, as_of, cfg.stats_days),
        indicators=indicators,
        sessions=sessions,
        external=external,
        weekly=weekly,
        monthly=monthly,
        error=None,
    )


def compute_brief(
    conn: duckdb.DuckDBPyConnection,
    cfg: BriefConfig,
    extra_notes: list[str] | None = None,
) -> BriefBundle:
    """Assemble the full bundle. One failing symbol never kills the brief."""
    panels: list[SymbolPanel] = []
    panel_notes: list[str] = []
    for symbol in cfg.symbols:
        try:
            panels.append(_compute_panel(conn, symbol, cfg, panel_notes))
        except Exception as exc:  # per-symbol isolation is the contract
            logger.warning("brief: panel failed for %s: %s", symbol, exc)
            panels.append(error_panel(symbol, str(exc)))
    return BriefBundle(
        as_of_ms=cfg.as_of_ms,
        day_ahead=day_ahead_label(cfg.as_of_ms),
        session_clock=_session_clock(cfg.as_of_ms),
        panels=panels,
        pundit=build_board(cfg),
        health=build_health(conn, cfg, [*(extra_notes or []), *panel_notes]),
    )

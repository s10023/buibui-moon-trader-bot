"""Chart-tab location overlay router — GET /api/location.

Anchored VWAP (day / week / month) and 60-day volume-profile levels for the
Chart tab. Display only, never a gate: see ``web/api/chart_location.py``.
"""

import duckdb
from fastapi import APIRouter, Depends

from analytics.data_store import get_ohlcv
from web.api.chart_location import (
    ANCHORS_BY_TIMEFRAME,
    DISPLAY_ONLY_MARKER,
    anchored_vwap_series,
    earliest_anchor_ms,
    profile_levels,
    profile_window_start_ms,
)
from web.api.deps import get_db, require_token
from web.api.models.location import (
    LocationResponse,
    ProfileLevelsModel,
    VwapPoint,
    VwapSeries,
)

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/location", response_model=LocationResponse)
def get_location_endpoint(
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> LocationResponse:
    """Anchored-VWAP series on the chart's own bars + volume-profile levels.

    Bars are fetched from the month anchor covering ``start_ms`` so the first
    on-screen window is anchored on its true start; only points at or after
    ``start_ms`` are returned.
    """
    anchors = ANCHORS_BY_TIMEFRAME.get(timeframe, ())
    vwap: list[VwapSeries] = []
    if anchors:
        bars = get_ohlcv(db, symbol, timeframe, earliest_anchor_ms(start_ms), end_ms)
        for anchor in anchors:
            points = [
                VwapPoint(time_ms=p.time_ms, anchor_ms=p.anchor_ms, value=p.value)
                for p in anchored_vwap_series(bars, anchor)
                if p.time_ms >= start_ms
            ]
            vwap.append(VwapSeries(anchor=anchor, points=points))

    hourly = get_ohlcv(db, symbol, "1h", profile_window_start_ms(end_ms), end_ms)
    levels = profile_levels(hourly, end_ms)
    profile = (
        ProfileLevelsModel(
            poc=levels.poc,
            vah=levels.vah,
            val=levels.val,
            window_start_ms=levels.window_start_ms,
            window_end_ms=levels.window_end_ms,
        )
        if levels is not None
        else None
    )
    return LocationResponse(vwap=vwap, profile=profile, note=DISPLAY_ONLY_MARKER)

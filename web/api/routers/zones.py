"""Structural zone overlay router — GET /api/zones."""

import duckdb
from fastapi import APIRouter, Depends

from analytics.data_store import get_ohlcv
from analytics.zones_lib import (
    extract_bos_zones,
    extract_eqh_eql_zones,
    extract_fib_golden_zones,
    extract_fvg_zones,
    extract_order_block_zones,
    extract_ote_zones,
    extract_swing_points,
)
from web.api.deps import get_db, require_token
from web.api.models.zones import SwingPoint, ZoneBox, ZoneLine, ZonesResponse

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/zones", response_model=ZonesResponse)
def get_zones_endpoint(
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> ZonesResponse:
    """Return structural zone geometry for chart overlay rendering.

    Returns:
    - boxes: FVG, Order Block, Fib Golden Zone, OTE zones (price-range boxes)
    - lines: EQH, EQL, BOS structural levels (horizontal lines)
    - swings: recent swing high/low pivot points
    """
    df = get_ohlcv(db, symbol, timeframe, start_ms, end_ms)
    if len(df) < 4:
        return ZonesResponse(boxes=[], lines=[], swings=[])

    boxes: list[ZoneBox] = []
    lines: list[ZoneLine] = []
    swings: list[SwingPoint] = []

    for z in extract_fvg_zones(df):
        boxes.append(ZoneBox(**z))

    for z in extract_order_block_zones(df):
        boxes.append(ZoneBox(**z))

    for z in extract_fib_golden_zones(df):
        boxes.append(ZoneBox(**z))

    for z in extract_ote_zones(df):
        boxes.append(ZoneBox(**z))

    for z in extract_eqh_eql_zones(df):
        lines.append(ZoneLine(**z))

    for z in extract_bos_zones(df):
        lines.append(ZoneLine(**z))

    for z in extract_swing_points(df):
        swings.append(SwingPoint(**z))

    return ZonesResponse(boxes=boxes, lines=lines, swings=swings)

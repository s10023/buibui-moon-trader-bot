"""Brief router — GET /api/brief (computed fresh per request, never cached)."""

import time

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query

from analytics.brief._common import parse_as_of_ms
from analytics.brief.bundle import compute_brief
from analytics.brief.config import BriefConfig, default_symbols
from analytics.brief.types import bundle_to_dict
from web.api.deps import get_db, require_token
from web.api.models.brief import BriefResponse

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/brief", response_model=BriefResponse)
def get_brief(
    symbols: str | None = Query(default=None, description="CSV symbol list"),
    days: int = Query(default=180, ge=30, le=365),
    as_of: str | None = Query(default=None, description="ISO8601 anchor"),
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> BriefResponse:
    """Compute the daily market brief. One failing symbol yields an error stub
    inside the 200 response; 4xx is reserved for invalid params."""
    if as_of is not None:
        try:
            as_of_ms = parse_as_of_ms(as_of)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid as_of (want ISO8601)"
            ) from None
    else:
        as_of_ms = int(time.time() * 1000)
    notes: list[str] = []
    if symbols:
        symbol_tuple = tuple(s.strip().upper() for s in symbols.split(",") if s.strip())
    else:
        symbol_tuple, notes = default_symbols()
    if not symbol_tuple:
        raise HTTPException(status_code=400, detail="No symbols")
    cfg = BriefConfig(symbols=symbol_tuple, as_of_ms=as_of_ms, stats_days=days)
    bundle = compute_brief(db, cfg, extra_notes=notes)
    return BriefResponse(**bundle_to_dict(bundle))

"""OHLCV router — GET /api/ohlcv."""

import duckdb
from fastapi import APIRouter, Depends

from analytics.data_store import get_funding_rates, get_ohlcv, get_open_interest
from web.api.deps import get_db, require_token
from web.api.models.ohlcv import CandleRow, FundingRow, OhlcvResponse, OiRow

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/ohlcv", response_model=OhlcvResponse)
def get_ohlcv_endpoint(
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    include_funding: bool = False,
    include_oi: bool = False,
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> OhlcvResponse:
    """Return OHLCV candles for a symbol/timeframe range."""
    df = get_ohlcv(db, symbol, timeframe, start_ms, end_ms)
    candles = [CandleRow.model_validate(row) for row in df.to_dict("records")]
    funding: list[FundingRow] | None = None
    if include_funding:
        fdf = get_funding_rates(db, symbol, start_ms, end_ms)
        funding = [FundingRow.model_validate(row) for row in fdf.to_dict("records")]
    oi: list[OiRow] | None = None
    if include_oi:
        oidf = get_open_interest(db, symbol, start_ms, end_ms)
        oi = [OiRow.model_validate(row) for row in oidf.to_dict("records")]
    return OhlcvResponse(candles=candles, funding=funding, oi=oi)

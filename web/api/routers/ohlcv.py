"""OHLCV router — GET /api/ohlcv, GET /api/ohlcv/live."""

import duckdb
from binance.client import Client
from fastapi import APIRouter, Depends, HTTPException, status

from analytics.data_store import get_funding_rates, get_ohlcv, get_open_interest
from web.api.deps import get_client, get_db, require_token
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


@router.get("/ohlcv/live", response_model=CandleRow)
def get_live_candle_endpoint(
    symbol: str,
    timeframe: str,
    client: Client = Depends(get_client),
) -> CandleRow:
    """Return the current in-progress candle direct from Binance (bypasses DB).

    Used by the frontend to seed the live candle with real O/H/L/C/V rather than
    relying solely on sparse SSE price ticks.
    """
    raw = client.futures_klines(symbol=symbol, interval=timeframe, limit=1)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No candle data returned from Binance",
        )
    k = raw[-1]
    return CandleRow(
        open_time=int(k[0]),
        open=float(k[1]),
        high=float(k[2]),
        low=float(k[3]),
        close=float(k[4]),
        volume=float(k[5]),
        taker_buy_volume=float(k[9]),
    )

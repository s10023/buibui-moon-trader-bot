"""Backtest router — POST /api/backtest."""

import datetime

import duckdb
from fastapi import APIRouter, Depends, HTTPException, status

from analytics.backtest_lib import run_backtest
from analytics.backtest_runner import detect_signals_for_strategy
from analytics.data_store import (
    get_ohlcv,
    list_backtest_runs,
    upsert_backtest_run,
    upsert_backtest_trades,
)
from analytics.indicators_lib import KNOWN_STRATEGIES
from utils.binance_client import load_coins_config
from web.api.deps import get_db, require_token
from web.api.models.backtest import (
    BacktestRequest,
    BacktestResponse,
    BacktestRunSummary,
    TradeModel,
)

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/backtest/runs", response_model=list[BacktestRunSummary])
def get_backtest_runs(
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> list[BacktestRunSummary]:
    """Return all saved backtest runs, newest first."""
    df = list_backtest_runs(db)
    return [BacktestRunSummary.model_validate(row) for row in df.to_dict("records")]


@router.post("/backtest", response_model=BacktestResponse)
def run_backtest_endpoint(
    body: BacktestRequest,
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> BacktestResponse:
    """Run a backtest for a symbol/timeframe/strategy combination."""
    if body.strategy not in KNOWN_STRATEGIES or body.strategy == "seasonality":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown or unsupported strategy '{body.strategy}'.",
        )

    end_ms = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    start_ms = end_ms - body.days * 24 * 3_600 * 1_000

    ohlcv = get_ohlcv(db, body.symbol, body.timeframe, start_ms, end_ms)
    if ohlcv.empty:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No OHLCV data for {body.symbol} {body.timeframe}. Run 'analytics backfill' first.",
        )

    # Resolve SMT secondary symbol from request or coins config
    secondary_symbol = body.secondary_symbol
    if body.strategy == "smt_divergence" and secondary_symbol is None:
        try:
            coins = load_coins_config()
            cfg = coins.get(body.symbol, {})
            secondary_symbol = cfg.get("smt_secondary")
        except Exception:
            pass
        if secondary_symbol is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="smt_divergence requires secondary_symbol or smt_secondary in coins.json.",
            )

    signals = detect_signals_for_strategy(
        db,
        ohlcv,
        body.symbol,
        body.timeframe,
        body.strategy,
        start_ms,
        end_ms,
        secondary_symbol,
    )
    if signals is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Missing required data for strategy '{body.strategy}'.",
        )

    result = run_backtest(
        ohlcv,
        signals,
        body.symbol,
        body.timeframe,
        body.strategy,
        body.sl_pct,
        body.tp_r,
        body.fee_pct,
    )

    # Persist to DB
    run_id = upsert_backtest_run(
        db,
        result,
        body.days,
        start_ms,
        end_ms,
        body.sl_pct,
        body.tp_r,
        body.fee_pct,
        "off",
        0,
        secondary_symbol,
    )
    upsert_backtest_trades(db, result, run_id)

    # Build BacktestResponse manually — BacktestResult has cached_property; cannot model_validate directly
    trades = [TradeModel.model_validate(t, from_attributes=True) for t in result.trades]
    return BacktestResponse(
        symbol=result.symbol,
        timeframe=result.timeframe,
        strategy=result.strategy,
        total_trades=len(result.trades),
        closed_trades=len(result.closed_trades),
        win_count=result.win_count,
        loss_count=result.loss_count,
        win_rate=result.win_rate,
        avg_r=result.avg_r,
        total_r=result.total_r,
        max_drawdown_r=result.max_drawdown_r,
        recovery_factor=result.recovery_factor,
        long_closed_trades=len(result.long_closed_trades),
        long_win_count=result.long_win_count,
        long_win_rate=result.long_win_rate,
        long_avg_r=result.long_avg_r,
        short_closed_trades=len(result.short_closed_trades),
        short_win_count=result.short_win_count,
        short_win_rate=result.short_win_rate,
        short_avg_r=result.short_avg_r,
        trades=trades,
    )

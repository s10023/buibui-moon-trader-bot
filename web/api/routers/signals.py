"""Signals router — POST /api/signals."""

import math

import duckdb
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, status

from analytics.backtest_runner import _detect_signals_for_strategy
from analytics.data_store import get_ohlcv
from analytics.indicators_lib import KNOWN_STRATEGIES, STRATEGY_REGISTRY
from utils.binance_client import load_coins_config
from web.api.deps import get_db, require_token
from web.api.models.signals import SignalRow, SignalsRequest, SignalsResponse

router = APIRouter(dependencies=[Depends(require_token)])


@router.post("/signals", response_model=SignalsResponse)
def run_signals(
    body: SignalsRequest,
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> SignalsResponse:
    """Run one or more signal detectors on historical OHLCV data."""
    for strat in body.strategies:
        if strat not in KNOWN_STRATEGIES or strat == "seasonality":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown or unsupported strategy '{strat}'.",
            )

    ohlcv = get_ohlcv(db, body.symbol, body.timeframe, body.start_ms, body.end_ms)
    if ohlcv.empty:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No OHLCV data for {body.symbol} {body.timeframe}.",
        )

    # Load coins config once for SMT secondary resolution
    try:
        coins = load_coins_config()
    except Exception:
        coins = {}

    all_signals: list[pd.DataFrame] = []

    for strat in body.strategies:
        secondary_symbol: str | None = None
        if strat == "smt_divergence":
            cfg = coins.get(body.symbol, {})
            secondary_symbol = cfg.get("smt_secondary")
            if secondary_symbol is None:
                continue  # skip silently — no secondary configured

        signals_df = _detect_signals_for_strategy(
            db,
            ohlcv,
            body.symbol,
            body.timeframe,
            strat,
            body.start_ms,
            body.end_ms,
            secondary_symbol,
        )
        if signals_df is None or signals_df.empty:
            continue

        signals_df = signals_df.copy()
        signals_df["strategy"] = strat
        spec = STRATEGY_REGISTRY.get(strat)
        signals_df["confidence"] = spec.confidence if spec else 3
        # Ensure required columns exist with defaults
        if "reason" not in signals_df.columns:
            signals_df["reason"] = strat
        if "context" not in signals_df.columns:
            signals_df["context"] = ""
        if "sl_price" not in signals_df.columns:
            signals_df["sl_price"] = 0.0
        if "entry_price" not in signals_df.columns:
            signals_df["entry_price"] = None

        all_signals.append(signals_df)

    if not all_signals:
        return SignalsResponse(signals=[])

    merged = pd.concat(all_signals, ignore_index=True)
    merged = merged.sort_values("open_time")

    rows: list[SignalRow] = []
    for _, sig in merged.iterrows():
        rows.append(
            SignalRow(
                open_time=int(sig["open_time"]),
                direction=str(sig["direction"]),
                strategy=str(sig["strategy"]),
                reason=str(sig.get("reason", sig["strategy"])),
                sl_price=float(sig.get("sl_price", 0.0)),
                entry_price=(
                    float(sig["entry_price"])
                    if sig.get("entry_price") is not None
                    and not math.isnan(float(sig["entry_price"]))
                    else None
                ),
                confidence=int(sig.get("confidence", 3)),
                context=str(sig.get("context", "")),
            )
        )

    return SignalsResponse(signals=rows)

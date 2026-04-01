"""Config router — GET /api/config, GET /api/strategies."""

from dataclasses import asdict
from typing import Any

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query, status

from analytics.data_store import get_confidence_ratings
from analytics.indicators_lib import STRATEGY_REGISTRY
from utils.binance_client import load_coins_config
from web.api.deps import get_db, require_token

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/config")
def get_config() -> dict[str, Any]:
    """Return per-symbol configuration from coins.json."""
    try:
        coins = load_coins_config()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to load coins config: {exc}",
        ) from exc
    return {symbol: dict(cfg) for symbol, cfg in coins.items()}


@router.get("/strategies")
def get_strategies(
    config: str | None = Query(default=None),
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> dict[str, Any]:
    """Return all strategy specs from the registry.

    If ?config=<name> is provided, confidence values are overridden with per-config
    DB ratings (falling back to registry defaults for any strategy not in the DB).
    """
    specs = {name: asdict(spec) for name, spec in STRATEGY_REGISTRY.items()}
    if config:
        overrides = get_confidence_ratings(db, config)
        for strategy, tf_stars in overrides.items():
            if strategy in specs:
                specs[strategy]["confidence"] = tf_stars
    return specs

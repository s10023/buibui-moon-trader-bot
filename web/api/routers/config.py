"""Config router — GET /api/config, GET /api/strategies."""

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from analytics.indicators_lib import STRATEGY_REGISTRY
from utils.binance_client import load_coins_config
from web.api.deps import require_token

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
def get_strategies() -> dict[str, Any]:
    """Return all strategy specs from the registry."""
    return {name: asdict(spec) for name, spec in STRATEGY_REGISTRY.items()}

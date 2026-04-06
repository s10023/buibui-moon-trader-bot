"""FastAPI application — lifespan, CORS, health endpoint, router mounts."""

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import duckdb
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from analytics.data_store import DEFAULT_DB_PATH, init_schema
from utils.binance_client import create_client
from web.api.routers import (
    backtest,
    config,
    fib,
    ohlcv,
    positions,
    prices,
    signals,
    stats,
    stream,
)


def _load_active_config(config_path: str) -> None:
    """Parse TOML config and populate app.state with config name and structured data.

    Called once at startup. Logs a warning and leaves state empty on any failure.
    """
    from analytics.signal_config import load_signal_config
    from web.api.models.active_config import ActiveConfigResponse, StrategyParamsModel

    try:
        sw_cfg = load_signal_config(config_path)
        config_name = Path(config_path).stem
        app.state.config_name = config_name
        app.state.active_config = ActiveConfigResponse(
            config_name=config_name,
            symbols=sw_cfg.symbols,
            timeframes=sw_cfg.timeframes,
            strategies=sw_cfg.strategies,
            day_filter=sw_cfg.day_filter,
            tp_r=sw_cfg.tp_r,
            sl_pct=sw_cfg.sl_pct,
            fee_pct=sw_cfg.backtest.fee_pct,
            min_sl_pct=sw_cfg.backtest.min_sl_pct,
            adr_suppress_threshold=sw_cfg.bias.adr_suppress_threshold,
            strategy_params={
                name: StrategyParamsModel(
                    tp_r=override.tp_r,
                    sl_pct=override.sl_pct,
                    tp_r_per_tf=override.tp_r_per_tf,
                )
                for name, override in sw_cfg.strategy_params.items()
            },
        )
        logging.info("Active config loaded: %s", config_name)
    except Exception:
        logging.warning(
            "Failed to load active config from %s", config_path, exc_info=True
        )
        app.state.config_name = None
        app.state.active_config = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Open DB (brief RW for schema, then read-only) and Binance client on startup."""
    # Brief RW open to ensure schema is initialised.
    # Skip gracefully if signal-watch daemon holds the write lock — schema already exists.
    try:
        with duckdb.connect(str(DEFAULT_DB_PATH)) as rw_conn:
            init_schema(rw_conn)
    except duckdb.IOException:
        pass  # DB locked by signal daemon; schema already initialised
    app.state.db_path = str(DEFAULT_DB_PATH)
    app.state.binance_client = create_client()
    config_path = os.environ.get("BUIBUI_CONFIG")
    if config_path:
        _load_active_config(config_path)
    else:
        app.state.config_name = None
        app.state.active_config = None
    yield


app = FastAPI(title="Buibui Web API", version="1.0.0", lifespan=lifespan)

_cors_origins_raw = os.environ.get("CORS_ORIGINS", "http://localhost:5173")
_cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(config.router, prefix="/api")
app.include_router(ohlcv.router, prefix="/api")
app.include_router(fib.router, prefix="/api")
app.include_router(signals.router, prefix="/api")
app.include_router(backtest.router, prefix="/api")
app.include_router(positions.router, prefix="/api")
app.include_router(prices.router, prefix="/api")
app.include_router(stats.router, prefix="/api")
app.include_router(stream.router, prefix="/api")


@app.get("/api/health")
def health() -> dict[str, str]:
    """Health check — no auth required."""
    return {"status": "ok"}


_UI_DIST = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "../ui/dist")
)
if os.path.isdir(_UI_DIST):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_UI_DIST, "assets")),
        name="ui-assets",
    )

    @app.get("/", include_in_schema=False)
    def serve_ui() -> FileResponse:
        """Serve the Svelte SPA entry point."""
        return FileResponse(os.path.join(_UI_DIST, "index.html"))

    @app.get("/buibui-logo.svg", include_in_schema=False)
    def serve_logo() -> FileResponse:
        """Serve the buibui logo from the dist root."""
        return FileResponse(os.path.join(_UI_DIST, "buibui-logo.svg"))


def run(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Start the uvicorn server."""
    uvicorn.run(
        "web.api.main:app",
        host=host,
        port=port,
        reload=reload,
    )

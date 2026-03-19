"""FastAPI application — lifespan, CORS, health endpoint, router mounts."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import duckdb
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from analytics.data_store import DEFAULT_DB_PATH, init_schema
from utils.binance_client import create_client
from web.api.routers import backtest, ohlcv, positions, prices, signals, stream


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Open DB (brief RW for schema, then read-only) and Binance client on startup."""
    # Brief RW open to ensure schema is initialised
    with duckdb.connect(str(DEFAULT_DB_PATH)) as rw_conn:
        init_schema(rw_conn)
    app.state.db_conn = duckdb.connect(str(DEFAULT_DB_PATH), read_only=True)
    app.state.binance_client = create_client()
    yield
    app.state.db_conn.close()


app = FastAPI(title="Buibui Web API", version="1.0.0", lifespan=lifespan)

_cors_origins_raw = os.environ.get("CORS_ORIGINS", "http://localhost:5173")
_cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ohlcv.router, prefix="/api")
app.include_router(signals.router, prefix="/api")
app.include_router(backtest.router, prefix="/api")
app.include_router(positions.router, prefix="/api")
app.include_router(prices.router, prefix="/api")
app.include_router(stream.router, prefix="/api")


@app.get("/api/health")
def health() -> dict[str, str]:
    """Health check — no auth required."""
    return {"status": "ok"}


def run(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Start the uvicorn server."""
    uvicorn.run(
        "web.api.main:app",
        host=host,
        port=port,
        reload=reload,
    )

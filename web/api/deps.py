"""FastAPI dependency factories: get_db, get_client, require_token."""

import os
import secrets
from collections.abc import Generator

import duckdb
from binance.client import Client
from fastapi import HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer()


def get_db(request: Request) -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """Yield the shared read-only DuckDB connection from app state."""
    yield request.app.state.db_conn


def get_client(request: Request) -> Client:
    """Return the Binance client from app state."""
    return request.app.state.binance_client  # type: ignore[no-any-return]


def require_token(
    creds: HTTPAuthorizationCredentials = Security(_bearer),
) -> None:
    """Validate Bearer token against API_TOKEN env var."""
    token = os.environ.get("API_TOKEN", "")
    if not token or not secrets.compare_digest(creds.credentials, token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

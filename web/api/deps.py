"""FastAPI dependency factories: get_db, get_client, require_token."""

import os
import secrets
from collections.abc import Generator

import duckdb
from binance.client import Client
from fastapi import HTTPException, Query, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer()


def get_db(request: Request) -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """Open a fresh read-only DuckDB connection per request (thread-safe)."""
    db_path: str = request.app.state.db_path
    conn = duckdb.connect(db_path, read_only=True)
    try:
        yield conn
    finally:
        conn.close()


def get_client(request: Request) -> Client:
    """Return the Binance client from app state."""
    client: Client = request.app.state.binance_client
    return client


def require_token(
    creds: HTTPAuthorizationCredentials = Security(_bearer),
) -> None:
    """Validate Bearer token against API_TOKEN env var."""
    token = os.environ.get("API_TOKEN", "")
    if not token or not secrets.compare_digest(creds.credentials, token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


def require_token_sse(token: str = Query(default="")) -> None:
    """Validate token from ?token= query param (EventSource cannot send headers)."""
    api_token = os.environ.get("API_TOKEN", "")
    if not api_token or not token or not secrets.compare_digest(token, api_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

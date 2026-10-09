"""FastAPI dependency factories: get_db, get_client, require_token."""

import os
import secrets
from collections.abc import Generator

import duckdb
from binance.client import Client
from fastapi import HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer()


def get_db(request: Request) -> Generator[duckdb.DuckDBPyConnection]:
    """Open a fresh read-only DuckDB connection per request (thread-safe)."""
    db_path: str = request.app.state.db_path
    try:
        conn = duckdb.connect(db_path, read_only=True)
    except duckdb.IOException:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is busy (signal-watch is writing). Try again in a few seconds.",
        ) from None
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


# Cookie carrying the SSE token. EventSource cannot set headers, so the UI POSTs
# its Bearer token to /api/stream/session, which sets this HttpOnly cookie.
SSE_COOKIE = "buibui_sse"
# Explicit opt-in for serving the SSE streams with no API_TOKEN at all.
SSE_DEV_NO_AUTH_ENV = "BUIBUI_WEB_DEV_NO_AUTH"


def require_token_sse(request: Request) -> None:
    """Validate the SSE token from an ``Authorization: Bearer`` header or cookie.

    The token is never read from the query string, where it would land in URLs
    and access logs. With API_TOKEN unset every connection is refused unless
    ``BUIBUI_WEB_DEV_NO_AUTH=1`` opts in explicitly.
    """
    api_token = os.environ.get("API_TOKEN", "")
    if not api_token:
        if os.environ.get(SSE_DEV_NO_AUTH_ENV) == "1":
            return
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    scheme, _, header_token = request.headers.get("Authorization", "").partition(" ")
    presented = header_token if scheme.lower() == "bearer" else ""
    presented = presented or request.cookies.get(SSE_COOKIE, "")
    if not presented or not secrets.compare_digest(presented, api_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

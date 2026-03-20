"""Fibonacci retracement router — GET /api/fib."""

from http import HTTPStatus

import duckdb
from fastapi import APIRouter, Depends, HTTPException

from analytics.data_store import get_ohlcv
from web.api.deps import get_db, require_token
from web.api.models.fib import FibLevel, FibResponse

router = APIRouter(dependencies=[Depends(require_token)])

# Fibonacci ratios and their labels; True = golden zone
_FIB_GRID: list[tuple[str, float, bool]] = [
    ("0.0", 0.0, False),
    ("0.236", 0.236, False),
    ("0.382", 0.382, False),
    ("0.5", 0.5, True),
    ("0.618", 0.618, True),
    ("0.786", 0.786, False),
    ("1.0", 1.0, False),
]


def _detect_swings(
    highs: list[float],
    lows: list[float],
    lookback: int = 20,
) -> tuple[float, float] | None:
    """Find most recent swing high and swing low via 3-bar pivot in the last *lookback* bars.

    Returns (swing_low, swing_high) or None if either pivot cannot be found.
    Uses the same pivot logic as ``detect_fibonacci_retracement`` in indicators_lib.
    """
    n = len(highs)
    window_start = max(0, n - lookback)
    window = range(window_start + 1, n - 1)  # need one bar on each side

    swing_high: float | None = None
    swing_low: float | None = None

    for k in window:
        if highs[k] > highs[k - 1] and highs[k] > highs[k + 1]:
            if swing_high is None or highs[k] > swing_high:
                swing_high = highs[k]
        if lows[k] < lows[k - 1] and lows[k] < lows[k + 1]:
            if swing_low is None or lows[k] < swing_low:
                swing_low = lows[k]

    if swing_high is None or swing_low is None:
        return None
    return swing_low, swing_high


@router.get("/fib", response_model=FibResponse)
def get_fib_endpoint(
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> FibResponse:
    """Return Fibonacci grid levels derived from the most recent swing high/low."""
    df = get_ohlcv(db, symbol, timeframe, start_ms, end_ms)
    if len(df) < 4:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail="Not enough candles to detect swings (need at least 4).",
        )

    highs: list[float] = df["high"].tolist()
    lows: list[float] = df["low"].tolist()

    result = _detect_swings(highs, lows)
    if result is None:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail="Could not detect swing high and swing low in the given range.",
        )

    swing_low, swing_high = result
    swing_range = swing_high - swing_low
    if swing_range <= 0:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail="Swing high equals swing low — degenerate range.",
        )

    levels = [
        FibLevel(
            label=label,
            price=swing_low + ratio * swing_range,
            golden=golden,
        )
        for label, ratio, golden in _FIB_GRID
    ]

    return FibResponse(swing_low=swing_low, swing_high=swing_high, levels=levels)

"""Fibonacci retracement router — GET /api/fib."""

import duckdb
from fastapi import APIRouter, Depends, HTTPException, status

from analytics.data_store import get_ohlcv
from web.api.deps import get_db, require_token
from web.api.models.fib import FibLevel, FibResponse

router = APIRouter(dependencies=[Depends(require_token)])

# Fibonacci ratios + labels; bool = golden zone
_FIB_GRID: list[tuple[str, float, bool]] = [
    ("0.0", 0.0, False),
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
) -> tuple[float, int, float, int] | None:
    """Find most recent swing high and swing low via 3-bar pivot in the last *lookback* bars.

    Returns (swing_low, swing_low_idx, swing_high, swing_high_idx) or None if either
    pivot cannot be found. Uses the same pivot logic as
    ``detect_fibonacci_retracement`` in indicators_lib.
    """
    n = len(highs)
    window_start = max(0, n - lookback)

    swing_high: float | None = None
    swing_high_idx = 0
    swing_low: float | None = None
    swing_low_idx = 0

    for k in range(window_start + 1, n - 1):  # need one bar on each side
        h = highs[k]
        if (
            h > highs[k - 1]
            and h > highs[k + 1]
            and (swing_high is None or h > swing_high)
        ):
            swing_high = h
            swing_high_idx = k
        lo = lows[k]
        if (
            lo < lows[k - 1]
            and lo < lows[k + 1]
            and (swing_low is None or lo < swing_low)
        ):
            swing_low = lo
            swing_low_idx = k

    if swing_high is None or swing_low is None:
        return None
    return swing_low, swing_low_idx, swing_high, swing_high_idx


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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Not enough candles to detect swings (need at least 4).",
        )

    result = _detect_swings(df["high"].tolist(), df["low"].tolist())
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not detect swing high and swing low in the given range.",
        )

    swing_low, swing_low_idx, swing_high, swing_high_idx = result
    swing_range = swing_high - swing_low
    if swing_range <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Swing high equals swing low — degenerate range.",
        )

    open_times: list[int] = df["open_time"].tolist()
    swing_start_ms = int(min(open_times[swing_low_idx], open_times[swing_high_idx]))

    # Orient levels so 0.0 sits at the most recent swing and 1.0 at the prior swing.
    up_move = swing_high_idx > swing_low_idx
    anchor, sign = (swing_high, -1.0) if up_move else (swing_low, 1.0)
    levels = [
        FibLevel(label=label, price=anchor + sign * ratio * swing_range, golden=golden)
        for label, ratio, golden in _FIB_GRID
    ]

    return FibResponse(
        swing_low=swing_low,
        swing_high=swing_high,
        swing_start_ms=swing_start_ms,
        levels=levels,
    )

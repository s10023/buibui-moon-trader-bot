"""Read-only DuckDB front door for the EWMAC trend sleeve.

Loads 1d OHLCV + funding for the universe and runs the forecast book. The only
module in ``analytics/forecast/`` that touches the database; never writes.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from analytics.forecast.book import ForecastBookResult, run_forecast_backtest
from analytics.forecast.config import ForecastConfig
from analytics.store.market_data import get_funding_rates, get_ohlcv
from analytics.universe import load_universe

# Sentinels that cover any realistic data range (Unix ms).
_FAR_PAST: int = 0
_FAR_FUTURE: int = 9_999_999_999_999


def load_daily_inputs(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    """Return (closes, fundings) dicts of day-indexed Series per symbol.

    - *closes*: daily close price, index = UTC midnight Timestamps.
    - *fundings*: daily funding rate (3 × 8-h rows summed per day),
      reindexed to the close index; missing days filled with 0.0.
    Symbols with no OHLCV are silently skipped (no key in either dict).
    """
    closes: dict[str, pd.Series] = {}
    fundings: dict[str, pd.Series] = {}
    for sym in symbols:
        bars = get_ohlcv(conn, sym, "1d", _FAR_PAST, _FAR_FUTURE)
        if bars.empty:
            continue
        idx = pd.to_datetime(bars["open_time"], unit="ms", utc=True).dt.normalize()
        close = pd.Series(bars["close"].to_numpy(dtype=float), index=idx)
        close = close[~close.index.duplicated(keep="last")].sort_index()
        closes[sym] = close

        fr = get_funding_rates(conn, sym, _FAR_PAST, _FAR_FUTURE)
        if fr.empty:
            fundings[sym] = pd.Series(0.0, index=close.index)
            continue
        fidx = pd.to_datetime(fr["funding_time"], unit="ms", utc=True).dt.normalize()
        daily = (
            pd.Series(fr["funding_rate"].to_numpy(dtype=float), index=fidx)
            .groupby(level=0)
            .sum()
        )
        fundings[sym] = daily.reindex(close.index).fillna(0.0)
    return closes, fundings


def replay_universe(
    conn: duckdb.DuckDBPyConnection,
    cfg: ForecastConfig,
    symbols: list[str] | None = None,
) -> ForecastBookResult:
    """Load the universe's 1d inputs and run the forecast book (read-only)."""
    syms = symbols if symbols is not None else load_universe()
    closes, fundings = load_daily_inputs(conn, syms)
    return run_forecast_backtest(closes, fundings, cfg)

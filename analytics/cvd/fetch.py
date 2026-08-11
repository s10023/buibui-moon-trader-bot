"""Keyless Binance SPOT daily klines for the D1 CVD sleeve.

Sibling of ``analytics/venue_fetch.py:73 fetch_binance_spot_daily``, which hits
the same endpoint for H14 but keeps only the close. This one keeps volume
(field 5) and taker buy base volume (field 9) — the CVD inputs.

``get`` is injected so the suite is network-free. No API key is used or required.
"""

from __future__ import annotations

import pandas as pd

from analytics.venue_fetch import DAY_MS, Getter, http_get_json

_LIMIT = 1000
_COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "taker_buy_volume"]

# The perp name carries a 1000x multiplier that the spot pair does not. The
# imbalance primitive is a ratio, so the multiplier cancels and no rescaling is
# needed — but the mapping is explicit, never derived by stripping the prefix.
SPOT_SYMBOL_OVERRIDES: dict[str, str] = {"1000PEPEUSDT": "PEPEUSDT"}

# No spot pair exists at all. Verified 2026-07-31.
PERP_ONLY: frozenset[str] = frozenset({"HYPEUSDT", "VVVUSDT"})


def spot_symbol_for(perp_symbol: str) -> str | None:
    """Spot pair for a perp symbol, or None when no spot pair exists."""
    if perp_symbol in PERP_ONLY:
        return None
    return SPOT_SYMBOL_OVERRIDES.get(perp_symbol, perp_symbol)


def _empty() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in _COLUMNS}).astype(
        {"open_time": "int64"}
    )


def fetch_spot_ohlcv_daily(
    symbol: str, start_ms: int, *, get: Getter = http_get_json
) -> pd.DataFrame:
    """Daily spot bars for a Binance SPOT symbol from ``start_ms`` to now."""
    rows: list[tuple[int, float, float, float, float, float, float]] = []
    cursor = start_ms
    while True:
        url = (
            "https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}&interval=1d&startTime={cursor}&limit={_LIMIT}"
        )
        page = get(url) or []
        for row in page:
            rows.append(
                (
                    int(row[0]),
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                    float(row[4]),
                    float(row[5]),
                    float(row[9]),
                )
            )
        if len(page) < _LIMIT:
            break
        next_cursor = int(page[-1][0]) + DAY_MS
        if next_cursor <= cursor:
            # A full page whose last open_time didn't advance past the cursor we
            # requested (stale/misbehaving response) — stop instead of
            # re-requesting the same window forever. Mirrors venue_fetch.py:90.
            break
        cursor = next_cursor
    if not rows:
        return _empty()
    df = pd.DataFrame(rows, columns=_COLUMNS)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df.astype({"open_time": "int64"})


def fetch_trading_spot_symbols(*, get: Getter = http_get_json) -> frozenset[str]:
    """Spot symbols whose exchangeInfo status is TRADING.

    TONUSDT returns klines while not being in the TRADING set, so kline
    availability is not proof a pair is live. Check against this before use.
    """
    payload = get("https://api.binance.com/api/v3/exchangeInfo") or {}
    return frozenset(
        str(s["symbol"])
        for s in payload.get("symbols", [])
        if s.get("status") == "TRADING"
    )

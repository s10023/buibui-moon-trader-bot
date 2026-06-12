"""OKX V5 public market-data adapter.

Exposes a duck-typed ``futures_klines(symbol, interval, startTime, limit)`` matching
the subset of ``binance.Client`` used by ``analytics.data_fetcher.fetch_klines``, so
the existing backfill / sync code works unchanged when ``DATA_SOURCE=okx``.

OKX public market data is keyless (verified reachable from US GH runners; Bybit and
Binance are geo-blocked). Funding / OI are intentionally NOT implemented — no live
detector needs them on this path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd


def _okx_row_to_binance(okx: list[str]) -> list[Any]:
    """Convert one OKX candle row to a Binance-shaped kline row.

    OKX: ``[ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]`` (strings).
    Binance mapper reads ``k[0]=open_time``, ``k[1..5]=OHLCV``, ``k[9]=taker_buy_volume``.
    OKX has no taker-buy split, so ``taker_buy_volume = volume / 2`` (neutral CVD delta).
    """
    volume = float(okx[5])
    return [
        int(okx[0]),  # 0 open_time (ms)
        okx[1],  # 1 open
        okx[2],  # 2 high
        okx[3],  # 3 low
        okx[4],  # 4 close
        okx[5],  # 5 volume
        "0",  # 6 close_time (unused)
        "0",  # 7 quote_volume (unused)
        0,  # 8 trades (unused)
        str(volume / 2),  # 9 taker_buy_volume (neutral)
    ]


# USDT-perp symbol map: Binance "BTCUSDT" -> OKX "BTC-USDT-SWAP".
_INST_SUFFIX = "USDT"


def _to_okx_inst_id(symbol: str) -> str:
    if not symbol.endswith(_INST_SUFFIX):
        raise ValueError(f"cannot map symbol to OKX instId: {symbol!r}")
    base = symbol[: -len(_INST_SUFFIX)]
    return f"{base}-USDT-SWAP"


# Bar map. 1d -> 1Dutc so the daily candle opens at 00:00 UTC (matches Binance
# open_time / day_filter). OKX hour bars (1H/4H) are UTC-aligned by default.
_BAR_MAP = {"15m": "15m", "1h": "1H", "4h": "4H", "1d": "1Dutc", "1w": "1Wutc"}


def _to_okx_bar(timeframe: str) -> str:
    if timeframe not in _BAR_MAP:
        raise ValueError(f"unsupported OKX timeframe: {timeframe!r}")
    return _BAR_MAP[timeframe]


_OKX_BASE = "https://www.okx.com"
_CANDLES_PATH = "/api/v5/market/candles"
_OKX_PAGE_LIMIT = 300  # OKX max rows per request


class OKXClient:
    """Minimal OKX market-data client exposing a Binance-compatible futures_klines."""

    def __init__(self, session: Any | None = None, base_url: str = _OKX_BASE) -> None:
        if session is None:
            import requests

            session = requests.Session()
        self._session = session
        self._base = base_url

    def futures_klines(
        self,
        symbol: str,
        interval: str,
        start_time: int,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """Return klines with open_time >= start_time, ascending, Binance-shaped.

        Paginates OKX's newest-first pages backward via ``after`` until start_time is
        reached or history ends, then maps + filters + sorts. Drops the unconfirmed
        in-progress candle (confirm == "0").
        """
        inst_id = _to_okx_inst_id(symbol)
        bar = _to_okx_bar(interval)
        collected: list[list[Any]] = []
        after: str | None = None
        while len(collected) < limit:
            params: dict[str, Any] = {
                "instId": inst_id,
                "bar": bar,
                "limit": str(_OKX_PAGE_LIMIT),
            }
            if after is not None:
                params["after"] = after
            resp = self._session.get(
                f"{self._base}{_CANDLES_PATH}", params=params, timeout=15
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                break
            for okx in data:
                if okx[8] == "0":  # unconfirmed in-progress candle
                    continue
                collected.append(_okx_row_to_binance(okx))
            oldest_ts = int(data[-1][0])
            after = str(oldest_ts)
            if oldest_ts <= start_time:
                break  # reached the requested window start

        rows = [r for r in collected if r[0] >= start_time]
        rows.sort(key=lambda r: r[0])
        return _rows_to_ohlcv_df(rows[:limit], symbol, interval)


def _rows_to_ohlcv_df(
    rows: list[list[Any]], symbol: str, interval: str
) -> pd.DataFrame:
    import pandas as pd

    from analytics.data_fetcher import OHLCV_COLUMNS

    if not rows:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "timeframe": interval,
                "open_time": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
                "taker_buy_volume": float(r[9]),
            }
            for r in rows
        ],
        columns=OHLCV_COLUMNS,
    )

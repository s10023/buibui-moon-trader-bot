"""OKX V5 public market-data adapter.

Exposes a duck-typed ``futures_klines(symbol, interval, startTime, limit)`` matching
the subset of ``binance.Client`` used by ``analytics.data_fetcher.fetch_klines``, so
the existing backfill / sync code works unchanged when ``DATA_SOURCE=okx``.

OKX public market data is keyless (verified reachable from US GH runners; Bybit and
Binance are geo-blocked). Funding / OI are intentionally NOT implemented — no live
detector needs them on this path.
"""

from __future__ import annotations

from typing import Any


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

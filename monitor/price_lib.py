"""Pure business logic for the price monitor.

All functions that need a Binance client accept it as a parameter
instead of relying on module-level globals.
"""

import datetime as dt
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from zoneinfo import ZoneInfo

from binance.client import Client
from colorama import Fore, Style
from rich.text import Text

_ASIA_TZ = ZoneInfo("Asia/Shanghai")

PRICE_HEADERS: list[str] = [
    "Symbol",
    "Last Price",
    "15m %",
    "1h %",
    "Since Asia 8AM",
    "24h %",
]
VALID_SORT_COLS: frozenset[str] = frozenset(
    {"change_15m", "change_1h", "change_asia", "change_24h"}
)


def format_pct(pct: Any) -> Any:
    """Format % change with color."""
    try:
        pct = float(pct)
        if pct > 0:
            return Fore.GREEN + f"{pct:+.2f}%" + Style.RESET_ALL
        elif pct < 0:
            return Fore.RED + f"{pct:+.2f}%" + Style.RESET_ALL
        else:
            return Fore.YELLOW + f"{pct:+.2f}%" + Style.RESET_ALL
    except Exception as e:
        logging.error(f"Error in format_pct: {e}")
        return pct


def format_pct_simple(pct: Any) -> str:
    """Format % change without color."""
    try:
        return f"{float(pct):+.2f}%"
    except Exception as e:
        logging.error(f"Error in format_pct_simple: {e}")
        return str(pct)


def clear_screen() -> None:
    """Clear the terminal screen."""
    os.system("cls" if os.name == "nt" else "clear")


def sort_table(
    table: list[Any], headers: list[str], col: str, order: bool
) -> list[Any]:
    """Sort price table by a given column name."""
    sort_key_map = {
        "change_15m": headers.index("15m %"),
        "change_1h": headers.index("1h %"),
        "change_asia": headers.index("Since Asia 8AM"),
        "change_24h": headers.index("24h %"),
    }

    idx = sort_key_map[col]

    def parse_value(val: Any) -> float:
        if isinstance(val, str):
            val = re.sub(r"\x1b\[[0-9;]*m", "", val)  # Remove ANSI color
            val = val.replace("%", "").strip()
        try:
            return float(val)
        except Exception:
            # None sorts last in both directions: -inf for desc (highest first), +inf for asc (lowest first)
            return float("-inf")

    return sorted(table, key=lambda row: parse_value(row[idx]), reverse=order)


def get_klines(
    client: Client, symbol: str, interval: str, lookback_minutes: int
) -> Any | None:
    """Fetch the most recent kline for a symbol."""
    now = dt.datetime.now(dt.UTC)
    start_time = int((now - dt.timedelta(minutes=lookback_minutes)).timestamp() * 1000)
    try:
        klines = client.get_klines(
            symbol=symbol, interval=interval, startTime=start_time
        )
        return klines[-1] if klines else None
    except Exception as e:
        logging.debug("get_klines failed for %s %s: %s", symbol, interval, e)
        return None


def batch_get_klines(
    client: Client,
    symbols: list[str],
    intervals_lookbacks: list[tuple[str, int]],
) -> dict[tuple[str, str], Any]:
    """Batch fetch klines for all symbols and intervals in parallel."""
    results: dict[tuple[str, str], Any] = {}

    def fetch(
        symbol: str, interval: str, lookback: int
    ) -> tuple[tuple[str, str], Any | None]:
        return ((symbol, interval), get_klines(client, symbol, interval, lookback))

    task_count = len(symbols) * len(intervals_lookbacks)
    with ThreadPoolExecutor(max_workers=min(task_count, 16)) as executor:
        futures = [
            executor.submit(fetch, symbol, interval, lookback)
            for symbol in symbols
            for interval, lookback in intervals_lookbacks
        ]
        for future in as_completed(futures):
            try:
                key, kline = future.result()
            except Exception as e:
                logging.warning("batch_get_klines worker failed: %s", e)
                continue
            results[key] = kline
    return results


def get_open_price_asia(client: Client, symbol: str) -> float | None:
    """Get the open price at Asia 8 AM for a symbol."""
    today_asia = dt.datetime.now(_ASIA_TZ).date()
    asia_today_8am = dt.datetime(
        today_asia.year,
        today_asia.month,
        today_asia.day,
        8,
        0,
        0,
        tzinfo=_ASIA_TZ,
    )
    now_utc = dt.datetime.now(dt.UTC)
    if now_utc < asia_today_8am.astimezone(dt.UTC):
        asia_today_8am -= dt.timedelta(days=1)

    start_time = int(asia_today_8am.astimezone(dt.UTC).timestamp() * 1000)
    try:
        kline = client.get_klines(
            symbol=symbol, interval="1m", startTime=start_time, limit=1
        )
        return float(kline[0][1]) if kline else None
    except Exception:
        return None


def batch_get_asia_open(client: Client, symbols: list[str]) -> dict[str, float | None]:
    """Fetch Asia 8 AM open prices for all symbols in parallel."""
    results: dict[str, float | None] = {}

    def fetch(sym: str) -> tuple[str, float | None]:
        return (sym, get_open_price_asia(client, sym))

    with ThreadPoolExecutor(max_workers=max(1, min(len(symbols), 16))) as executor:
        futures = [executor.submit(fetch, sym) for sym in symbols]
        for future in as_completed(futures):
            try:
                sym, asia_open = future.result()
            except Exception as e:
                logging.warning("batch_get_asia_open worker failed: %s", e)
                continue
            results[sym] = asia_open
    return results


def get_price_changes(
    client: Client, symbols: list[str], telegram: bool = False
) -> tuple[list[Any], set[Any]]:
    """Compute price changes for all symbols."""
    table: list[Any] = []
    invalid_symbols: set[Any] = set()

    try:
        all_tickers = client.get_ticker()
        ticker_map = {t["symbol"]: t for t in all_tickers}
    except Exception as e:
        logging.error(f"Error fetching all tickers: {e}")
        return [[symbol, "Error", "", "", "", ""] for symbol in symbols], set()

    intervals_lookbacks = [("15m", 15), ("1h", 60)]
    kline_map = batch_get_klines(client, symbols, intervals_lookbacks)
    asia_open_map = batch_get_asia_open(client, symbols)

    fmt = format_pct_simple if telegram else format_pct

    for symbol in symbols:
        try:
            ticker = ticker_map.get(symbol)
            if not ticker:
                invalid_symbols.add((symbol, "Ticker not found"))
                table.append([symbol, "Error", "", "", "", ""])
                continue

            last_price = float(ticker["lastPrice"])
            change_24h = float(ticker["priceChangePercent"])

            k15 = kline_map.get((symbol, "15m"))
            k60 = kline_map.get((symbol, "1h"))
            open_15 = float(k15[1]) if k15 else last_price
            open_60 = float(k60[1]) if k60 else last_price

            change_15m = ((last_price - open_15) / open_15) * 100 if open_15 else 0
            change_1h = ((last_price - open_60) / open_60) * 100 if open_60 else 0

            asia_open = asia_open_map.get(symbol)
            change_asia = (
                ((last_price - asia_open) / asia_open) * 100 if asia_open else 0
            )

            last_price_str = str(round(last_price, 4))
            table.append(
                [
                    symbol,
                    last_price_str,
                    fmt(change_15m),
                    fmt(change_1h),
                    fmt(change_asia),
                    fmt(change_24h),
                ]
            )
        except Exception as e:
            msg = str(e)
            if "Invalid symbol" in msg:
                invalid_symbols.add((symbol, "Invalid symbol"))
            else:
                invalid_symbols.add((symbol, msg))
            table.append([symbol, "Error", "", "", "", ""])
    return table, invalid_symbols


def format_pct_rich(pct: float) -> Text:
    """Format % change as a Rich Text object with color."""
    formatted = f"{pct:+.2f}%"
    if pct > 0:
        return Text(formatted, style="green")
    elif pct < 0:
        return Text(formatted, style="red")
    else:
        return Text(formatted, style="yellow")


def sort_table_raw(
    rows: list[list[Any]], col_idx: int, reverse: bool
) -> list[list[Any]]:
    """Sort rows by a column of raw floats. None values always sort last."""

    def key(row: list[Any]) -> float:
        val = row[col_idx]
        if val is None:
            return float("-inf") if reverse else float("inf")
        return float(val)

    return sorted(rows, key=key, reverse=reverse)

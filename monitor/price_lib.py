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
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_MAX_WORKERS = 16

PRICE_HEADERS: list[str] = [
    "Symbol",
    "Last Price",
    "15m %",
    "1h %",
    "4h %",
    "Since Asia 8AM",
    "24h %",
]
VALID_SORT_COLS: frozenset[str] = frozenset(
    {"change_15m", "change_1h", "change_4h", "change_asia", "change_24h"}
)

_SORT_COL_HEADERS: dict[str, str] = {
    "change_15m": "15m %",
    "change_1h": "1h %",
    "change_4h": "4h %",
    "change_asia": "Since Asia 8AM",
    "change_24h": "24h %",
}


def format_pct(pct: Any) -> Any:
    """Format % change with color."""
    try:
        pct = float(pct)
    except Exception as e:
        logging.error(f"Error in format_pct: {e}")
        return pct
    if pct > 0:
        color = Fore.GREEN
    elif pct < 0:
        color = Fore.RED
    else:
        color = Fore.YELLOW
    return color + f"{pct:+.2f}%" + Style.RESET_ALL


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


def _parse_sort_value(val: Any, reverse: bool) -> float:
    """Parse a cell value to float for sorting. Strips ANSI codes and '%'.

    None/unparseable values always sort last:
    -inf when reverse=True (desc), +inf when reverse=False (asc).
    """
    if isinstance(val, str):
        val = _ANSI_RE.sub("", val).replace("%", "").strip()
    try:
        return float(val)
    except Exception:
        return float("-inf") if reverse else float("inf")


def sort_table(
    table: list[Any], headers: list[str], col: str, order: bool
) -> list[Any]:
    """Sort price table by a given column name."""
    idx = headers.index(_SORT_COL_HEADERS[col])
    return sorted(
        table, key=lambda row: _parse_sort_value(row[idx], order), reverse=order
    )


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


def _run_parallel(
    tasks: list[Any],
    worker: Any,
    label: str,
) -> list[Any]:
    """Run `worker(task)` for each task in a thread pool; log + skip worker errors."""
    results: list[Any] = []
    if not tasks:
        return results
    max_workers = min(len(tasks), _MAX_WORKERS)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(worker, t) for t in tasks]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                logging.warning("%s worker failed: %s", label, e)
    return results


def batch_get_klines(
    client: Client,
    symbols: list[str],
    intervals_lookbacks: list[tuple[str, int]],
) -> dict[tuple[str, str], Any]:
    """Batch fetch klines for all symbols and intervals in parallel."""
    tasks = [
        (symbol, interval, lookback)
        for symbol in symbols
        for interval, lookback in intervals_lookbacks
    ]

    def fetch(task: tuple[str, str, int]) -> tuple[tuple[str, str], Any | None]:
        symbol, interval, lookback = task
        return (symbol, interval), get_klines(client, symbol, interval, lookback)

    pairs = _run_parallel(tasks, fetch, "batch_get_klines")
    return dict(pairs)


def get_open_price_asia(client: Client, symbol: str) -> float | None:
    """Get the open price at Asia 8 AM for a symbol."""
    today_asia = dt.datetime.now(_ASIA_TZ).date()
    asia_8am = dt.datetime(
        today_asia.year,
        today_asia.month,
        today_asia.day,
        8,
        0,
        0,
        tzinfo=_ASIA_TZ,
    )
    if dt.datetime.now(dt.UTC) < asia_8am.astimezone(dt.UTC):
        asia_8am -= dt.timedelta(days=1)

    start_time = int(asia_8am.astimezone(dt.UTC).timestamp() * 1000)
    try:
        kline = client.get_klines(
            symbol=symbol, interval="1m", startTime=start_time, limit=1
        )
        return float(kline[0][1]) if kline else None
    except Exception:
        return None


def batch_get_asia_open(client: Client, symbols: list[str]) -> dict[str, float | None]:
    """Fetch Asia 8 AM open prices for all symbols in parallel."""

    def fetch(sym: str) -> tuple[str, float | None]:
        return sym, get_open_price_asia(client, sym)

    pairs = _run_parallel(symbols, fetch, "batch_get_asia_open")
    return dict(pairs)


def _pct_change(last: float, base: float | None) -> float:
    return ((last - base) / base) * 100 if base else 0.0


def get_price_changes(
    client: Client, symbols: list[str], telegram: bool = False
) -> tuple[list[Any], set[Any]]:
    """Compute price changes for all symbols."""
    try:
        all_tickers = client.get_ticker()
        ticker_map = {t["symbol"]: t for t in all_tickers}
    except Exception as e:
        logging.error(f"Error fetching all tickers: {e}")
        return [[symbol, "Error", "", "", "", "", ""] for symbol in symbols], set()

    kline_map = batch_get_klines(
        client, symbols, [("15m", 15), ("1h", 60), ("4h", 240)]
    )
    asia_open_map = batch_get_asia_open(client, symbols)

    fmt = format_pct_simple if telegram else format_pct

    table: list[Any] = []
    invalid_symbols: set[Any] = set()

    for symbol in symbols:
        try:
            ticker = ticker_map.get(symbol)
            if not ticker:
                invalid_symbols.add((symbol, "Ticker not found"))
                table.append([symbol, "Error", "", "", "", "", ""])
                continue

            last_price = float(ticker["lastPrice"])
            change_24h = float(ticker["priceChangePercent"])

            k15 = kline_map.get((symbol, "15m"))
            k60 = kline_map.get((symbol, "1h"))
            k240 = kline_map.get((symbol, "4h"))
            open_15 = float(k15[1]) if k15 else last_price
            open_60 = float(k60[1]) if k60 else last_price
            open_240 = float(k240[1]) if k240 else last_price
            asia_open = asia_open_map.get(symbol)

            table.append(
                [
                    symbol,
                    str(round(last_price, 4)),
                    fmt(_pct_change(last_price, open_15)),
                    fmt(_pct_change(last_price, open_60)),
                    fmt(_pct_change(last_price, open_240)),
                    fmt(_pct_change(last_price, asia_open)),
                    fmt(change_24h),
                ]
            )
        except Exception as e:
            msg = str(e)
            reason = "Invalid symbol" if "Invalid symbol" in msg else msg
            invalid_symbols.add((symbol, reason))
            table.append([symbol, "Error", "", "", "", "", ""])
    return table, invalid_symbols


def format_pct_rich(pct: float) -> Text:
    """Format % change as a Rich Text object with color."""
    formatted = f"{pct:+.2f}%"
    if pct > 0:
        return Text(formatted, style="green")
    if pct < 0:
        return Text(formatted, style="red")
    return Text(formatted, style="yellow")


def sort_table_raw(
    rows: list[list[Any]], col_idx: int, reverse: bool
) -> list[list[Any]]:
    """Sort rows by a column of raw floats. None values always sort last."""
    return sorted(
        rows, key=lambda row: _parse_sort_value(row[col_idx], reverse), reverse=reverse
    )

import argparse
import time
import os
import json
import datetime as dt
import pytz
from binance.client import Client
from dotenv import load_dotenv
from tabulate import tabulate
from colorama import init, Fore, Style
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Set, Tuple, Optional
import re

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from utils.config_validation import validate_coins_config
from utils.telegram import send_telegram_message

# Init colorama
init(autoreset=True)

# Load environment
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(message)s")


def sync_binance_time(client: Any) -> None:
    server_time = client.get_server_time()["serverTime"]
    local_time = int(time.time() * 1000)
    client.TIME_OFFSET = server_time - local_time


client = Client(API_KEY, API_SECRET)
sync_binance_time(client)

# Load symbols from config
try:
    with open("config/coins.json") as f:
        coins_config = json.load(f)
    validate_coins_config(coins_config)
    COINS = list(coins_config.keys())
except json.JSONDecodeError as e:
    logging.error(f"JSON decode error in config/coins.json: {e}")
    sys.exit(1)
except Exception as e:
    logging.error(f"Error loading config/coins.json: {e}")
    sys.exit(1)


# Format % change with color
def format_pct(pct: Any) -> Any:
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
    try:
        return f"{float(pct):+.2f}%"
    except Exception as e:
        logging.error(f"Error in format_pct_simple: {e}")
        return str(pct)


# Convert datetime to Binance-compatible string
def get_klines(symbol: str, interval: str, lookback_minutes: int) -> Optional[Any]:
    now = dt.datetime.utcnow()
    start_time = int((now - dt.timedelta(minutes=lookback_minutes)).timestamp() * 1000)
    try:
        klines = client.get_klines(
            symbol=symbol, interval=interval, startTime=start_time
        )
        return klines[-1]  # most recent kline
    except Exception as e:
        return None


def batch_get_klines(
    symbols: List[str], intervals_lookbacks: List[Tuple[str, int]]
) -> Dict[Tuple[str, str], Any]:
    """
    Batch fetch klines for all symbols and intervals in parallel.
    intervals_lookbacks: list of (interval, lookback_minutes)
    Returns: dict of {(symbol, interval): kline}
    """
    results = {}

    def fetch(
        symbol: str, interval: str, lookback: int
    ) -> Tuple[Tuple[str, str], Optional[Any]]:
        now = dt.datetime.utcnow()
        start_time = int((now - dt.timedelta(minutes=lookback)).timestamp() * 1000)
        try:
            klines = client.get_klines(
                symbol=symbol, interval=interval, startTime=start_time
            )
            return ((symbol, interval), klines[-1] if klines else None)
        except Exception as e:
            return ((symbol, interval), None)

    cpu_count = os.cpu_count() or 1
    max_workers = max(1, cpu_count // 2)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(fetch, symbol, interval, lookback)
            for symbol in symbols
            for interval, lookback in intervals_lookbacks
        ]
        for future in as_completed(futures):
            key, kline = future.result()
            results[key] = kline
    return results


def get_open_price_asia(symbol: str) -> Optional[float]:
    now_utc = dt.datetime.utcnow().replace(tzinfo=pytz.utc)
    asia_tz = pytz.timezone("Asia/Shanghai")  # GMT+8
    asia_today_8am = now_utc.astimezone(asia_tz).replace(
        hour=8, minute=0, second=0, microsecond=0
    )
    if now_utc.astimezone(asia_tz) < asia_today_8am:
        asia_today_8am -= dt.timedelta(days=1)

    start_time = int(asia_today_8am.astimezone(pytz.utc).timestamp() * 1000)
    try:
        kline = client.get_klines(
            symbol=symbol, interval="1m", startTime=start_time, limit=1
        )
        return float(kline[0][1]) if kline else None  # open price
    except Exception as e:
        return None


def get_price_changes(
    symbols: List[str], telegram: bool = False
) -> Tuple[List[Any], Set[Any]]:
    table = []
    invalid_symbols = set()
    # Get all tickers once
    try:
        all_tickers = client.get_ticker()
        ticker_map = {t["symbol"]: t for t in all_tickers}
    except Exception as e:
        logging.error(f"Error fetching all tickers: {e}")
        return [[symbol, "Error", "", "", "", ""] for symbol in symbols], set()

    # Batch fetch klines for all symbols
    intervals_lookbacks = [("15m", 15), ("1h", 60)]
    kline_map = batch_get_klines(symbols, intervals_lookbacks)

    def get_asia_open_parallel(symbols: List[str]) -> Dict[str, Optional[float]]:
        results = {}

        def fetch(symbol: str) -> Tuple[str, Optional[float]]:
            return (symbol, get_open_price_asia(symbol))

        cpu_count = os.cpu_count() or 1
        max_workers = max(1, cpu_count // 2)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(fetch, symbol) for symbol in symbols]
            for future in as_completed(futures):
                symbol, asia_open = future.result()
                results[symbol] = asia_open
        return results

    asia_open_map = get_asia_open_parallel(symbols)

    for symbol in symbols:
        try:
            ticker = ticker_map.get(symbol)
            if not ticker:
                invalid_symbols.add((symbol, "Ticker not found"))
                table.append([symbol, "Error", "", "", "", ""])
                continue

            last_price = float(ticker["lastPrice"])
            change_24h = float(ticker["priceChangePercent"])

            # Use batch klines
            k15 = kline_map.get((symbol, "15m"))
            k60 = kline_map.get((symbol, "1h"))
            open_15 = float(k15[1]) if k15 else last_price
            open_60 = float(k60[1]) if k60 else last_price

            change_15m = ((last_price - open_15) / open_15) * 100 if open_15 else 0
            change_1h = ((last_price - open_60) / open_60) * 100 if open_60 else 0

            # Asia session open (parallelized)
            asia_open = asia_open_map.get(symbol)
            change_asia = (
                ((last_price - asia_open) / asia_open) * 100 if asia_open else 0
            )

            last_price_str = str(round(last_price, 4))
            if telegram:
                table.append(
                    [
                        symbol,
                        last_price_str,
                        format_pct_simple(change_15m),
                        format_pct_simple(change_1h),
                        format_pct_simple(change_asia),
                        format_pct_simple(change_24h),
                    ]
                )
            else:
                table.append(
                    [
                        symbol,
                        last_price_str,
                        format_pct(change_15m),
                        format_pct(change_1h),
                        format_pct(change_asia),
                        format_pct(change_24h),
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


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def sort_table(
    table: List[Any], headers: List[str], col: str, order: bool
) -> List[Any]:
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
            return float("-inf")  # Treat unparseable values as lowest

    return sorted(table, key=lambda row: parse_value(row[idx]), reverse=order)


def main(live: bool = False, telegram: bool = False, sort: str = "") -> None:
    sort_col, _, sort_dir = sort.partition(":")
    sort_order = sort_dir.lower() != "asc"

    valid_sort_cols = {"change_15m", "change_1h", "change_asia", "change_24h"}

    headers = ["Symbol", "Last Price", "15m %", "1h %", "Since Asia 8AM", "24h %"]

    if not live:
        clear_screen()
        print("📈 Crypto Price Snapshot — Buibui Moon Bot\n")
        price_table, invalid_symbols = get_price_changes(COINS, telegram=telegram)
        if sort_col in valid_sort_cols:
            print(f"[DEBUG] Sorting by: {sort_col} {'desc' if sort_order else 'asc'}")
            price_table = sort_table(price_table, headers, sort_col, sort_order)

        print(tabulate(price_table, headers=headers, tablefmt="fancy_grid"))

        if invalid_symbols:
            print("\n⚠️  The following symbols had errors:")
            for symbol, reason in sorted(invalid_symbols):
                print(f"  - {symbol}: {reason}")

        if telegram:
            plain_table = tabulate(price_table, headers=headers, tablefmt="plain")
            try:
                send_telegram_message(
                    f"📈 Snapshot Price Monitor\n```\n{plain_table}\n```"
                )
            except Exception as e:
                print("❌ Telegram message failed:", e)

    else:
        try:
            while True:
                clear_screen()
                print("📈 Live Crypto Price Monitor — Buibui Moon Bot\n")
                price_table, invalid_symbols = get_price_changes(COINS)
                if sort_col in valid_sort_cols:
                    price_table = sort_table(price_table, headers, sort_col, sort_order)
                print(tabulate(price_table, headers=headers, tablefmt="fancy_grid"))
                if invalid_symbols:
                    print("\n⚠️  The following symbols had errors:")
                    for symbol, reason in sorted(invalid_symbols):
                        print(f"  - {symbol}: {reason}")
                time.sleep(5)
        except KeyboardInterrupt:
            print("\nExiting gracefully. Goodbye!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Buibui Moon Crypto Monitor")
    parser.add_argument("--live", action="store_true", help="Run in live refresh mode")
    parser.add_argument(
        "--telegram", action="store_true", help="Send output to Telegram"
    )
    parser.add_argument(
        "--sort",
        type=str,
        default="",
        help="Sort table by column[:asc|desc]. Options: change_15m, change_1h, change_asia, change_24h. Example: --sort change_15m:desc",
    )
    args = parser.parse_args()
    main(live=args.live, telegram=args.telegram, sort=args.sort)

"""Price monitor — thin wrapper that creates dependencies and delegates to price_lib."""

import argparse
import logging
import sys
import time
from typing import Any

from colorama import init
from tabulate import tabulate

from monitor.price_lib import (
    clear_screen,
    format_pct,
    format_pct_simple,
    get_klines,
    get_open_price_asia,
    get_price_changes,
    sort_table,
)
from utils.binance_client import create_client, load_coins_config, sync_binance_time
from utils.telegram import send_telegram_message

# Re-export lib functions so existing callers still work
__all__ = [
    "clear_screen",
    "format_pct",
    "format_pct_simple",
    "get_klines",
    "get_open_price_asia",
    "get_price_changes",
    "sort_table",
    "sync_binance_time",
    "main",
]

init(autoreset=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(message)s")


def main(live: bool = False, telegram: bool = False, sort: str = "") -> None:
    try:
        client: Any = create_client()
    except Exception as e:
        logging.error(f"Failed to create Binance client: {e}")
        sys.exit(1)

    try:
        coins_config = load_coins_config()
        coins = list(coins_config.keys())
    except Exception as e:
        logging.error(f"Error loading config/coins.json: {e}")
        sys.exit(1)

    sort_col, _, sort_dir = sort.partition(":")
    sort_order = sort_dir.lower() != "asc"

    valid_sort_cols = {"change_15m", "change_1h", "change_asia", "change_24h"}

    headers = ["Symbol", "Last Price", "15m %", "1h %", "Since Asia 8AM", "24h %"]

    if not live:
        clear_screen()
        print("\U0001f4c8 Crypto Price Snapshot \u2014 Buibui Moon Bot\n")
        price_table, invalid_symbols = get_price_changes(
            client, coins, telegram=telegram
        )
        if sort_col in valid_sort_cols:
            price_table = sort_table(price_table, headers, sort_col, sort_order)

        print(tabulate(price_table, headers=headers, tablefmt="fancy_grid"))

        if sort_col in valid_sort_cols:
            arrow = "\U0001f53d" if sort_order else "\U0001f53c"
            direction = "descending" if sort_order else "ascending"
            print(f"\n{arrow} Sorted by: {sort_col} ({direction})")

        if invalid_symbols:
            print("\n\u26a0\ufe0f  The following symbols had errors:")
            for symbol, reason in sorted(invalid_symbols):
                print(f"  - {symbol}: {reason}")

        if telegram:
            plain_table = tabulate(price_table, headers=headers, tablefmt="plain")
            try:
                send_telegram_message(
                    f"\U0001f4c8 Snapshot Price Monitor\n```\n{plain_table}\n```"
                )
            except Exception as e:
                print("\u274c Telegram message failed:", e)

    else:
        try:
            while True:
                clear_screen()
                print("\U0001f4c8 Live Crypto Price Monitor \u2014 Buibui Moon Bot\n")
                price_table, invalid_symbols = get_price_changes(client, coins)
                if sort_col in valid_sort_cols:
                    price_table = sort_table(price_table, headers, sort_col, sort_order)
                print(tabulate(price_table, headers=headers, tablefmt="fancy_grid"))
                if sort_col in valid_sort_cols:
                    arrow = "\U0001f53d" if sort_order else "\U0001f53c"
                    direction = "descending" if sort_order else "ascending"
                    print(f"\n{arrow} Sorted by: {sort_col} ({direction})")
                if invalid_symbols:
                    print("\n\u26a0\ufe0f  The following symbols had errors:")
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

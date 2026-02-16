"""Position monitor — thin wrapper that creates dependencies and delegates to position_lib."""

import argparse
import logging
import os
import sys
from typing import Any

from monitor.position_lib import (
    color_risk_usd,
    color_sl_size,
    colorize,
    colorize_dollar,
    display_progress_bar,
    display_table,
    fetch_open_positions,
    get_stop_loss_for_symbol,
    get_wallet_balance,
)
from utils.binance_client import (
    create_client,
    get_wallet_target,
    load_coins_config,
    sync_binance_time,
)

# Re-export lib functions so existing callers still work
__all__ = [
    "colorize",
    "colorize_dollar",
    "color_sl_size",
    "color_risk_usd",
    "display_progress_bar",
    "get_wallet_balance",
    "get_stop_loss_for_symbol",
    "fetch_open_positions",
    "display_table",
    "sync_binance_time",
    "main",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(message)s")


def main(
    sort: str = "default",
    telegram: bool = False,
    hide_empty: bool = False,
    compact: bool = False,
) -> None:
    try:
        client: Any = create_client()
    except Exception as e:
        logging.error(f"Failed to create Binance client: {e}")
        sys.exit(1)

    try:
        coins_config = load_coins_config()
        coin_order = list(coins_config.keys())
    except Exception as e:
        logging.error(f"Error loading config/coins.json: {e}")
        sys.exit(1)

    wallet_target = get_wallet_target()

    sort_key, _, sort_dir = sort.partition(":")
    sort_order = sort_dir.lower() != "asc"

    os.system("cls" if os.name == "nt" else "clear")
    print("\U0001f4c8 Trades Position Snapshot \u2014 Buibui Moon Bot\n")
    print(
        display_table(
            client,
            coins_config,
            coin_order,
            wallet_target,
            sort_by=sort_key,
            descending=sort_order,
            telegram=telegram,
            hide_empty=hide_empty,
            compact=compact,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sort",
        default="default",
        help="Sort order, e.g., 'pnl_pct:desc', 'sl_usd:asc', or 'default'",
    )
    parser.add_argument(
        "--telegram", action="store_true", help="Send output to Telegram"
    )
    parser.add_argument(
        "--hide-empty", action="store_true", help="Hide symbols with no open positions"
    )
    parser.add_argument(
        "--compact", action="store_true", help="Show compact information"
    )
    args = parser.parse_args()

    main(
        sort=args.sort,
        telegram=args.telegram,
        hide_empty=args.hide_empty,
        compact=args.compact,
    )

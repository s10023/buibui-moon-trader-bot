import os
import json
from dotenv import load_dotenv
from binance.client import Client
from tabulate import tabulate
import argparse
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import pandas as pd
import pandas_ta as ta
from utils.config_validation import validate_coins_config
from typing import Any, Dict, List, Optional, Tuple
from utils.indicators import fetch_klines_df, calculate_indicators, get_alerts

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from utils.telegram import send_telegram_message

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s:%(message)s")


def sync_binance_time(client: Any) -> None:
    server_time = client.get_server_time()["serverTime"]
    local_time = int(time.time() * 1000)
    client.TIME_OFFSET = server_time - local_time


# Load .env variables
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
WALLET_TARGET = float(os.getenv("WALLET_TARGET", 0))
client = Client(API_KEY, API_SECRET)
sync_binance_time(client)

# Load coin config
try:
    with open("config/coins.json") as f:
        coins_config = json.load(f)
    validate_coins_config(coins_config)
    COINS_CONFIG = coins_config
    COIN_ORDER = list(coins_config.keys())
except json.JSONDecodeError as e:
    logging.error(f"JSON decode error in config/coins.json: {e}")
    sys.exit(1)
except Exception as e:
    logging.error(f"Error loading config/coins.json: {e}")
    sys.exit(1)


def colorize(value: Any, threshold: float = 0) -> Any:
    try:
        value = float(value)
        if value > threshold:
            return f"\033[92m{value:+.2f}%\033[0m"
        elif value < -threshold:
            return f"\033[91m{value:+.2f}%\033[0m"
        else:
            return f"\033[93m{value:+.2f}%\033[0m"
    except Exception as e:
        logging.error(f"Error in colorize: {e}")
        return value


def colorize_dollar(value: Any) -> str:
    try:
        value = float(value)
        if value > 0:
            return f"\033[92m${value:,.2f}\033[0m"
        elif value < 0:
            return f"\033[91m-${abs(value):,.2f}\033[0m"
        else:
            return f"\033[93m$0.00\033[0m"
    except Exception as e:
        logging.error(f"Error in colorize_dollar: {e}")
        return f"${value}"


def color_sl_size(pct: float) -> str:
    if pct < 2:
        return f"\033[91m{pct:.2f}%\033[0m"
    elif pct < 3.5:
        return f"\033[93m{pct:.2f}%\033[0m"
    else:
        return f"\033[92m{pct:.2f}%\033[0m"


def color_risk_usd(value: float, total_balance: float) -> str:
    pct = (value / total_balance * 100) if total_balance else 0
    formatted = f"${value:,.2f} ({pct:.2f}%)"
    if pct < -50:
        return f"\033[91m{formatted}\033[0m"
    elif pct < -30:
        return f"\033[93m{formatted}\033[0m"
    else:
        return f"\033[92m{formatted}\033[0m"


def get_wallet_balance() -> Tuple[float, float]:
    balances = client.futures_account_balance()
    for b in balances:
        if b["asset"] == "USDT":
            balance = float(b["balance"])
            unrealized = float(b.get("crossUnPnl", 0))
            return balance, unrealized
    return 0.0, 0.0


def get_stop_loss_for_symbol(symbol: str) -> Optional[float]:
    try:
        orders = client.futures_get_open_orders(symbol=symbol)
        for o in orders:
            if o["type"] in ("STOP_MARKET", "STOP") and o.get("reduceOnly"):
                return float(o["stopPrice"])
    except Exception:
        # Suppress excessive error logs, will summarize if needed
        pass
    return None


def fetch_open_positions(
    sort_by: str = "default", descending: bool = True, show_indicators: bool = True
) -> Tuple[List[Any], float]:

    positions = client.futures_position_information()
    filtered = []
    wallet_balance, _ = get_wallet_balance()
    total_risk_usd = 0.0

    # Prepare open positions for parallel stop loss fetching
    open_positions = []
    for pos in positions:
        symbol = pos["symbol"]
        if symbol not in COINS_CONFIG:
            continue
        amt = float(pos["positionAmt"])
        if amt == 0:
            continue
        entry = float(pos["entryPrice"])
        mark = float(pos["markPrice"])
        notional = abs(float(pos["notional"]))
        margin = float(pos.get("positionInitialMargin", 0)) or 1e-6
        side_text = "LONG" if amt > 0 else "SHORT"
        open_positions.append(
            (symbol, side_text, entry, mark, margin, notional, amt, pos)
        )

    # Parallelize stop loss fetching
    def fetch_sl(
        symbol: str, side_text: str, entry: float, notional: float
    ) -> Tuple[str, Any, Any, Any, float, Optional[float]]:
        try:
            actual_sl = get_stop_loss_for_symbol(symbol)
            if actual_sl:
                if side_text == "SHORT":
                    sl_percent = (entry - actual_sl) / entry * 100
                else:
                    sl_percent = (actual_sl - entry) / entry * 100
                sl_risk_usd = notional * (sl_percent / 100)
                sl_size_str = colorize(sl_percent)
                actual_sl_str = f"{actual_sl:.5f}"
                sl_usd_str = colorize_dollar(sl_risk_usd)
            else:
                sl_percent = None
                sl_risk_usd = 0.0
                actual_sl_str = "-"
                sl_size_str = "-"
                sl_usd_str = "-"
            return (
                symbol,
                actual_sl_str,
                sl_size_str,
                sl_usd_str,
                sl_risk_usd,
                sl_percent,
            )
        except Exception:
            return (symbol, "-", "-", "-", 0.0, None)

    sl_results = {}
    cpu_count = os.cpu_count() or 1
    max_workers = max(1, cpu_count // 2)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(fetch_sl, symbol, side_text, entry, notional)
            for (
                symbol,
                side_text,
                entry,
                mark,
                margin,
                notional,
                amt,
                pos,
            ) in open_positions
        ]
        for future in as_completed(futures):
            symbol, actual_sl_str, sl_size_str, sl_usd_str, sl_risk_usd, sl_percent = (
                future.result()
            )
            sl_results[symbol] = (
                actual_sl_str,
                sl_size_str,
                sl_usd_str,
                sl_risk_usd,
                sl_percent,
            )

    for symbol, side_text, entry, mark, margin, notional, amt, pos in open_positions:
        side_colored = (
            f"\033[92m{side_text}\033[0m" if amt > 0 else f"\033[91m{side_text}\033[0m"
        )
        pnl = float(pos.get("unRealizedProfit", 0))
        pnl_pct = (pnl / margin) * 100
        leverage = round(notional / margin)
        actual_sl_str, sl_size_str, sl_usd_str, sl_risk_usd, sl_percent = (
            sl_results.get(symbol, ("-", "-", "-", 0.0, None))
        )
        if sl_risk_usd:
            total_risk_usd += sl_risk_usd
        # --- Indicator Alerts ---
        alerts_str = ""
        if show_indicators:
            indicator_timeframes = [
                ("15m", "15m", 100),
                ("1h", "1h", 100),
                ("4h", "4h", 100),
            ]
            alerts = []
            for tf, tf_label, limit in indicator_timeframes:
                df = fetch_klines_df(client, symbol, tf, limit=limit)
                rsi, macd, macdsignal = calculate_indicators(df)
                alerts.extend(get_alerts(rsi, macd, macdsignal, tf_label))
            alerts_str = "; ".join(alerts)
        row = [
            symbol,  # 0
            side_colored,  # 1
            leverage,  # 2
            round(entry, 5),  # 3
            round(mark, 5),  # 4
            round(margin, 2),  # 5
            round(notional, 2),  # 6
            colorize_dollar(pnl),  # 7
            colorize(pnl_pct),  # 8
            f"{(margin / wallet_balance) * 100:.2f}%",  # 9
            actual_sl_str,  # 10
            sl_size_str,  # 11
            sl_usd_str,  # 12
        ]
        if show_indicators:
            row.append(alerts_str)
        # Append extra values at the end for sorting (not shown)
        row.append(pnl_pct)  # index 13
        row.append(sl_risk_usd)  # index 14
        filtered.append(row)

    # Get coins without open positions
    open_symbols = set(row[0] for row in filtered)
    missing_symbols = [s for s in COIN_ORDER if s not in open_symbols]

    # Add placeholder rows for missing symbols
    for symbol in missing_symbols:
        leverage = COINS_CONFIG[symbol]["leverage"]
        row = [
            symbol,  # 0
            "-",  # side
            leverage,  # lev
            "-",
            "-",  # entry, mark
            "-",
            "-",  # margin, size
            "-",
            "-",  # pnl, pnl%
            "-",
            "-",
            "-",  # risk%, sl price, % to sl
            "-",  # sl usd
            -999,  # hidden sort: pnl_pct
            -9999,  # hidden sort: sl_usd
        ]
        filtered.append(row)

    if sort_by == "pnl_pct":
        filtered.sort(key=lambda r: r[13], reverse=descending)
    elif sort_by == "sl_usd":
        filtered.sort(key=lambda r: r[14], reverse=descending)
    else:  # default sort by COIN_ORDER
        filtered.sort(
            key=lambda r: COIN_ORDER.index(r[0]) if r[0] in COIN_ORDER else 999
        )

    # Remove hidden sort columns
    filtered = [row[:13] for row in filtered]

    return filtered, total_risk_usd


def display_progress_bar(current: float, target: float, bar_length: int = 30) -> str:
    if target <= 0:
        return ""
    pct = min(max(current / target, 0), 1)
    filled = int(bar_length * pct)
    empty = bar_length - filled
    color = "\033[92m" if pct >= 1 else ("\033[93m" if pct >= 0.5 else "\033[91m")
    bar = color + "█" * filled + "-" * empty + "\033[0m"
    return f"Wallet Target: ${current:,.2f} / ${target:,.2f} |{bar}| {pct*100:.1f}%"


def display_table(
    sort_by: str = "default", descending: bool = True, telegram: bool = False, show_indicators: bool = True
) -> str:
    table, total_risk_usd = fetch_open_positions(sort_by, descending, show_indicators=show_indicators)
    wallet, unrealized = get_wallet_balance()
    total = wallet + unrealized
    unrealized_pct = (unrealized / wallet * 100) if wallet else 0
    used_margin = sum(
        float(row[5]) for row in table if isinstance(row[5], (int, float))
    )
    available_balance = total - used_margin
    output = []
    output.append(f"\n💰 Wallet Balance: ${wallet:,.2f}")
    output.append(f"💼 Available Balance: ${available_balance:,.2f}")
    output.append(
        f"📊 Total Unrealized PnL: {colorize_dollar(unrealized)} ({colorize(unrealized_pct)} of wallet)"
    )
    output.append(f"🧾 Wallet w/ Unrealized: ${total:,.2f}")
    output.append(f"⚠️ Total SL Risk: {color_risk_usd(total_risk_usd, wallet)}\n")
    if WALLET_TARGET > 0:
        output.append(display_progress_bar(total, WALLET_TARGET))
    headers = [
        "Symbol",
        "Side",
        "Lev",
        "Entry",
        "Mark",
        "Used Margin (USD)",
        "Position Size (USD)",
        "PnL",
        "PnL%",
        "Risk%",
        "SL Price",
        "% to SL",
        "SL USD",
    ]

    if show_indicators:
        headers.append("Alerts")
    
    output.append(
        tabulate(
            table,
            headers=headers,
            tablefmt="fancy_grid",
            numalign="right",
            stralign="left",
        )
    )
    if telegram:
        summary = (
            f"📌 Open Positions Snapshot\n\n"
            f"💰 Wallet Balance: ${wallet:,.2f}\n"
            f"💼 Available Balance: ${available_balance:,.2f}\n"
            f"📊 Unrealized PnL: {unrealized:+.2f} ({unrealized_pct:+.2f}%)\n"
            f"🧾 Wallet + PnL: ${total:,.2f}\n"
            f"⚠️ SL Risk: ${total_risk_usd:,.2f}"
        )
        try:
            send_telegram_message(summary)
        except Exception as e:
            logging.error(f"❌ Telegram message failed: {e}")
    return "\n".join(output)


def main(sort: str = "default", telegram: bool = False, show_indicators: bool =True) -> None:

    sort_key, _, sort_dir = sort.partition(":")
    sort_order = sort_dir.lower() != "asc"  # default to descending if not asc

    os.system("cls" if os.name == "nt" else "clear")
    print(display_table(sort_by=sort_key, descending=sort_order, telegram=telegram, show_indicators=show_indicators))


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
        "--hide-indicators",
        action="store_true",
        help="Hide indicator alerts column (RSI/MACD)",
    )
    args = parser.parse_args()

    main(
        sort=args.sort, telegram=args.telegram, show_indicators=not args.hide_indicators
    )

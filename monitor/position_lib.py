"""Pure business logic for the position monitor.

All functions that need external dependencies (Binance client, config)
accept them as parameters instead of relying on module-level globals.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from binance.client import Client
from tabulate import tabulate

from utils.telegram import send_telegram_message


def colorize(value: Any, threshold: float = 0) -> Any:
    """Colorize a percentage value based on threshold."""
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
    """Colorize a dollar value."""
    try:
        value = float(value)
        if value > 0:
            return f"\033[92m${value:,.2f}\033[0m"
        elif value < 0:
            return f"\033[91m-${abs(value):,.2f}\033[0m"
        else:
            return "\033[93m$0.00\033[0m"
    except Exception as e:
        logging.error(f"Error in colorize_dollar: {e}")
        return f"${value}"


def color_sl_size(pct: float) -> str:
    """Colorize stop-loss size percentage."""
    if pct < 2:
        return f"\033[91m{pct:.2f}%\033[0m"
    elif pct < 3.5:
        return f"\033[93m{pct:.2f}%\033[0m"
    else:
        return f"\033[92m{pct:.2f}%\033[0m"


def color_risk_usd(value: float, total_balance: float) -> str:
    """Colorize risk in USD with percentage of balance."""
    pct = (value / total_balance * 100) if total_balance else 0
    formatted = f"${value:,.2f} ({pct:.2f}%)"
    if pct < -50:
        return f"\033[91m{formatted}\033[0m"
    elif pct < -30:
        return f"\033[93m{formatted}\033[0m"
    else:
        return f"\033[92m{formatted}\033[0m"


def display_progress_bar(current: float, target: float, bar_length: int = 30) -> str:
    """Render a colored progress bar for wallet target."""
    if target <= 0:
        return ""
    pct = min(max(current / target, 0), 1)
    filled = int(bar_length * pct)
    empty = bar_length - filled
    color = "\033[92m" if pct >= 1 else ("\033[93m" if pct >= 0.5 else "\033[91m")
    bar = color + "\u2588" * filled + "-" * empty + "\033[0m"
    return f"Wallet Target: ${current:,.2f} / ${target:,.2f} |{bar}| {pct * 100:.1f}%"


def get_wallet_balance(client: Client) -> tuple[float, float]:
    """Get USDT wallet balance and unrealized PnL."""
    balances = client.futures_account_balance()
    for b in balances:
        if b["asset"] == "USDT":
            balance = float(b["balance"])
            unrealized = float(b.get("crossUnPnl", 0))
            return balance, unrealized
    return 0.0, 0.0


def get_stop_loss_for_symbol(client: Client, symbol: str) -> float | None:
    """Get the stop-loss price for a symbol from open orders."""
    try:
        orders = client.futures_get_open_orders(symbol=symbol)
        for o in orders:
            if o["type"] in ("STOP_MARKET", "STOP") and o.get("reduceOnly"):
                return float(o["stopPrice"])
    except Exception:
        pass
    return None


def fetch_open_positions(
    client: Client,
    coins_config: dict[str, Any],
    coin_order: list[str],
    sort_by: str = "default",
    descending: bool = True,
    hide_empty: bool = False,
) -> tuple[list[Any], float]:
    """Fetch and format open futures positions."""
    positions = client.futures_position_information()
    filtered: list[Any] = []
    wallet_balance, _ = get_wallet_balance(client)
    total_risk_usd = 0.0

    open_positions = []
    for pos in positions:
        symbol = pos["symbol"]
        if symbol not in coins_config:
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

    def fetch_sl(
        symbol: str, side_text: str, entry: float, notional: float
    ) -> tuple[str, Any, Any, Any, float, float | None]:
        try:
            actual_sl = get_stop_loss_for_symbol(client, symbol)
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

    sl_results: dict[str, Any] = {}
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
        row = [
            symbol,
            side_colored,
            leverage,
            round(entry, 5),
            round(mark, 5),
            round(margin, 2),
            round(notional, 2),
            colorize_dollar(pnl),
            colorize(pnl_pct),
            f"{(margin / wallet_balance) * 100:.2f}%",
            actual_sl_str,
            sl_size_str,
            sl_usd_str,
        ]
        row.append(pnl_pct)  # index 13 — hidden sort key
        row.append(sl_risk_usd)  # index 14 — hidden sort key
        filtered.append(row)

    if not hide_empty:
        open_symbols = set(row[0] for row in filtered)
        missing_symbols = [s for s in coin_order if s not in open_symbols]

        for symbol in missing_symbols:
            leverage = coins_config[symbol]["leverage"]
            row = [
                symbol,
                "-",
                leverage,
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
                -999,
                -9999,
            ]
            filtered.append(row)

    if sort_by == "pnl_pct":
        filtered.sort(key=lambda r: r[13], reverse=descending)
    elif sort_by == "sl_usd":
        filtered.sort(key=lambda r: r[14], reverse=descending)
    else:
        filtered.sort(
            key=lambda r: coin_order.index(r[0]) if r[0] in coin_order else 999
        )

    filtered = [row[:13] for row in filtered]

    return filtered, total_risk_usd


def display_table(
    client: Client,
    coins_config: dict[str, Any],
    coin_order: list[str],
    wallet_target: float,
    sort_by: str = "default",
    descending: bool = True,
    telegram: bool = False,
    hide_empty: bool = False,
    compact: bool = False,
) -> str:
    """Build the full position display output."""
    table, total_risk_usd = fetch_open_positions(
        client, coins_config, coin_order, sort_by, descending, hide_empty
    )
    wallet, unrealized = get_wallet_balance(client)
    total = wallet + unrealized
    unrealized_pct = (unrealized / wallet * 100) if wallet else 0
    used_margin = sum(
        float(row[5]) for row in table if isinstance(row[5], (int, float))
    )
    available_balance = total - used_margin
    output = []
    output.append(f"\n\U0001f4b0 Wallet Balance: ${wallet:,.2f}")
    output.append(f"\U0001f4bc Available Balance: ${available_balance:,.2f}")
    output.append(
        f"\U0001f4ca Total Unrealized PnL: {colorize_dollar(unrealized)} ({colorize(unrealized_pct)} of wallet)"
    )
    output.append(f"\U0001f9fe Wallet w/ Unrealized: ${total:,.2f}")
    output.append(
        f"\u26a0\ufe0f Total SL Risk: {color_risk_usd(total_risk_usd, wallet)}\n"
    )
    if wallet_target > 0:
        output.append(display_progress_bar(total, wallet_target))

    if compact:
        return "\n".join(output)

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
    output.append(
        tabulate(
            table,
            headers=headers,
            tablefmt="fancy_grid",
            numalign="right",
            stralign="left",
        )
    )
    if sort_by != "default":
        arrow = "\U0001f53d" if descending else "\U0001f53c"
        direction = "descending" if descending else "ascending"
        output.append(f"\n{arrow} Sorted by: {sort_by} ({direction})")
    if telegram:
        summary = (
            f"\U0001f4cc Open Positions Snapshot\n\n"
            f"\U0001f4b0 Wallet Balance: ${wallet:,.2f}\n"
            f"\U0001f4bc Available Balance: ${available_balance:,.2f}\n"
            f"\U0001f4ca Unrealized PnL: {unrealized:+.2f} ({unrealized_pct:+.2f}%)\n"
            f"\U0001f9fe Wallet + PnL: ${total:,.2f}\n"
            f"\u26a0\ufe0f SL Risk: ${total_risk_usd:,.2f}"
        )
        try:
            send_telegram_message(summary)
        except Exception as e:
            logging.error(f"\u274c Telegram message failed: {e}")
    return "\n".join(output)

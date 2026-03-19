"""Pure business logic for the position monitor.

All functions that need external dependencies (Binance client, config)
accept them as parameters instead of relying on module-level globals.
"""

import logging
from collections.abc import Callable
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
    pct = abs(pct)
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


def get_wallet_balance(client: Client) -> tuple[float, float, float]:
    """Get USDT wallet balance, unrealized PnL, and available balance.

    availableBalance is read directly from the API rather than computed manually;
    Binance already accounts for position margin, open order margin, and unrealized
    PnL as collateral in cross-margin mode.
    """
    balances = client.futures_account_balance()
    for b in balances:
        if b["asset"] == "USDT":
            balance = float(b["balance"])
            unrealized = float(b.get("crossUnPnl", 0))
            available = float(b.get("availableBalance", 0))
            return balance, unrealized, available
    return 0.0, 0.0, 0.0


def _find_sl_in_orders(
    orders: list[dict[str, Any]], position_side: str = "BOTH"
) -> float | None:
    """Find the first SL price in a pre-fetched list of orders for one symbol.

    In hedge mode (position_side != "BOTH"), only matches orders whose
    positionSide equals position_side. Orders with positionSide "BOTH" (one-way
    mode orders) are always considered regardless of position_side.
    """
    for o in orders:
        if o["type"] not in ("STOP_MARKET", "STOP"):
            continue
        order_side = o.get("positionSide", "BOTH")
        if (
            position_side != "BOTH"
            and order_side != "BOTH"
            and order_side != position_side
        ):
            continue
        price = float(o.get("stopPrice") or 0)
        if price > 0:
            return price
    return None


def get_stop_loss_for_symbol(
    client: Client, symbol: str, position_side: str = "BOTH"
) -> float | None:
    """Get the stop-loss price for a symbol from open orders.

    Pass position_side in hedge mode to match only the correct side's SL order.
    """
    try:
        orders = client.futures_get_open_orders(symbol=symbol)
        return _find_sl_in_orders(orders, position_side)
    except Exception as e:
        logging.warning("SL fetch failed for %s: %s", symbol, e)
    return None


def _fetch_all_sl_prices(client: Client) -> dict[tuple[str, str], float]:
    """Fetch all open orders in one call and return {(symbol, positionSide): sl_price}.

    Keying by (symbol, positionSide) supports hedge mode where LONG and SHORT
    positions on the same symbol have independent SL orders.
    """
    try:
        all_orders: list[dict[str, Any]] = client.futures_get_open_orders()
    except Exception as e:
        logging.warning("Failed to fetch all open orders: %s", e)
        return {}

    orders_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for o in all_orders:
        sym = o.get("symbol", "")
        side = o.get("positionSide", "BOTH")
        orders_by_key.setdefault((sym, side), []).append(o)

    result: dict[tuple[str, str], float] = {}
    for (sym, side), orders in orders_by_key.items():
        sl = _find_sl_in_orders(orders, side)
        if sl is not None:
            result[(sym, side)] = sl
    return result


def fetch_open_positions(
    client: Client,
    coins_config: dict[str, Any],
    coin_order: list[str],
    sort_by: str = "default",
    descending: bool = True,
    hide_empty: bool = False,
) -> tuple[list[Any], float, float, float, float]:
    """Fetch and format open futures positions."""
    try:
        positions = client.futures_position_information()
    except Exception as e:
        logging.error("Failed to fetch position information: %s", e)
        raise RuntimeError(f"Failed to fetch position information: {e}") from e
    filtered: list[Any] = []
    wallet_balance, unrealized_pnl, available_balance = get_wallet_balance(client)
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
        position_side = pos.get("positionSide", "BOTH")
        open_positions.append(
            (symbol, side_text, position_side, entry, mark, margin, notional, amt, pos)
        )

    # One bulk fetch instead of one REST call per open position.
    # Fall back to per-symbol if the bulk call returns nothing (e.g. API error
    # swallowed silently, or exchange quirk) so SL is never silently dropped.
    sl_prices = _fetch_all_sl_prices(client) if open_positions else {}
    for sym, _, pos_side, *_ in open_positions:
        if (sym, pos_side) not in sl_prices and (sym, "BOTH") not in sl_prices:
            sl = get_stop_loss_for_symbol(client, sym, pos_side)
            if sl is not None:
                sl_prices[(sym, pos_side)] = sl

    for (
        symbol,
        side_text,
        position_side,
        entry,
        mark,
        margin,
        notional,
        amt,
        pos,
    ) in open_positions:
        side_colored = (
            f"\033[92m{side_text}\033[0m" if amt > 0 else f"\033[91m{side_text}\033[0m"
        )
        pnl = float(pos.get("unRealizedProfit", 0))
        pnl_pct = (pnl / margin) * 100
        leverage = round(notional / margin)

        actual_sl = sl_prices.get((symbol, position_side)) or sl_prices.get(
            (symbol, "BOTH")
        )
        if actual_sl:
            if side_text == "SHORT":
                sl_percent = (entry - actual_sl) / entry * 100
            else:
                sl_percent = (actual_sl - entry) / entry * 100
            sl_risk_usd = notional * abs(sl_percent) / 100
            actual_sl_str = f"{actual_sl:.5f}"
            sl_size_str = color_sl_size(sl_percent)
            sl_usd_str = colorize_dollar(sl_risk_usd)
            total_risk_usd += sl_risk_usd
        else:
            sl_risk_usd = 0.0
            actual_sl_str = "-"
            sl_size_str = "-"
            sl_usd_str = "-"

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
            f"{(margin / wallet_balance * 100) if wallet_balance else 0.0:.2f}%",
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

    logging.info("Found %d open position(s)", len(open_positions))
    filtered = [row[:13] for row in filtered]

    return filtered, total_risk_usd, wallet_balance, unrealized_pnl, available_balance


def display_table(
    client: Client,
    coins_config: dict[str, Any],
    coin_order: list[str],
    wallet_target: list[float],
    wallet_target_invalid: list[str] | None = None,
    sort_by: str = "default",
    descending: bool = True,
    telegram: bool = False,
    hide_empty: bool = False,
    compact: bool = False,
    send_fn: Callable[[str], None] = send_telegram_message,
) -> str:
    """Build the full position display output."""
    table, total_risk_usd, wallet, unrealized, available_balance = fetch_open_positions(
        client, coins_config, coin_order, sort_by, descending, hide_empty
    )
    total = wallet + unrealized
    unrealized_pct = (unrealized / wallet * 100) if wallet else 0
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
    for target in wallet_target:
        output.append(display_progress_bar(total, target))
    if wallet_target_invalid:
        bad = ", ".join(repr(e) for e in wallet_target_invalid)
        output.append(f"\033[93m⚠ WALLET_TARGET: invalid entries skipped: {bad}\033[0m")

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
            send_fn(summary)
        except Exception as e:
            logging.error(f"\u274c Telegram message failed: {e}")
    return "\n".join(output)

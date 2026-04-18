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

# ANSI colour codes — kept local so tests can assert on them directly.
_RED = "\033[91m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"

_SL_ORDER_TYPES = ("STOP_MARKET", "STOP")
_TP_ORDER_TYPES = ("TAKE_PROFIT_MARKET", "TAKE_PROFIT")


def colorize(value: Any, threshold: float = 0) -> Any:
    """Colorize a percentage value based on threshold."""
    try:
        value = float(value)
    except Exception as e:
        logging.error(f"Error in colorize: {e}")
        return value
    if value > threshold:
        color = _GREEN
    elif value < -threshold:
        color = _RED
    else:
        color = _YELLOW
    return f"{color}{value:+.2f}%{_RESET}"


def colorize_dollar(value: Any) -> str:
    """Colorize a dollar value."""
    try:
        value = float(value)
    except Exception as e:
        logging.error(f"Error in colorize_dollar: {e}")
        return f"${value}"
    if value > 0:
        return f"{_GREEN}${value:,.2f}{_RESET}"
    if value < 0:
        return f"{_RED}-${abs(value):,.2f}{_RESET}"
    return f"{_YELLOW}$0.00{_RESET}"


def color_sl_size(pct: float) -> str:
    """Colorize stop-loss size percentage."""
    pct = abs(pct)
    if pct < 2:
        color = _RED
    elif pct < 3.5:
        color = _YELLOW
    else:
        color = _GREEN
    return f"{color}{pct:.2f}%{_RESET}"


def color_risk_usd(value: float, total_balance: float) -> str:
    """Colorize risk in USD with percentage of balance."""
    pct = (value / total_balance * 100) if total_balance else 0
    formatted = f"${value:,.2f} ({pct:.2f}%)"
    if pct < -50:
        color = _RED
    elif pct < -30:
        color = _YELLOW
    else:
        color = _GREEN
    return f"{color}{formatted}{_RESET}"


def display_progress_bar(current: float, target: float, bar_length: int = 30) -> str:
    """Render a colored progress bar for wallet target."""
    if target <= 0:
        return ""
    pct = min(max(current / target, 0), 1)
    filled = int(bar_length * pct)
    if pct >= 1:
        color = _GREEN
    elif pct >= 0.5:
        color = _YELLOW
    else:
        color = _RED
    bar = color + "\u2588" * filled + "-" * (bar_length - filled) + _RESET
    return f"Wallet Target: ${current:,.2f} / ${target:,.2f} |{bar}| {pct * 100:.1f}%"


def get_wallet_balance(client: Client) -> tuple[float, float, float]:
    """Get USDT wallet balance, unrealized PnL, and available balance.

    availableBalance is read directly from the API rather than computed manually;
    Binance already accounts for position margin, open order margin, and unrealized
    PnL as collateral in cross-margin mode.
    """
    for b in client.futures_account_balance():
        if b["asset"] == "USDT":
            return (
                float(b["balance"]),
                float(b.get("crossUnPnl", 0)),
                float(b.get("availableBalance", 0)),
            )
    return 0.0, 0.0, 0.0


def _position_side_matches(order_side: str, position_side: str) -> bool:
    """True if a given order's positionSide is valid for `position_side`.

    One-way mode orders (positionSide="BOTH") always match, otherwise hedge-mode
    orders must share the requested side.
    """
    if position_side == "BOTH" or order_side == "BOTH":
        return True
    return order_side == position_side


def _find_order_price(
    orders: list[dict[str, Any]],
    order_types: tuple[str, ...],
    position_side: str,
) -> float | None:
    """Return the first matching order's stopPrice, or None."""
    for o in orders:
        if o["type"] not in order_types:
            continue
        if not _position_side_matches(o.get("positionSide", "BOTH"), position_side):
            continue
        price = float(o.get("stopPrice") or 0)
        if price > 0:
            return price
    return None


def _find_sl_in_orders(
    orders: list[dict[str, Any]], position_side: str = "BOTH"
) -> float | None:
    """Find the first SL price in a pre-fetched list of orders for one symbol."""
    return _find_order_price(orders, _SL_ORDER_TYPES, position_side)


def _find_tp_in_orders(
    orders: list[dict[str, Any]], position_side: str = "BOTH"
) -> float | None:
    """Find the first TP price in a pre-fetched list of orders for one symbol."""
    return _find_order_price(orders, _TP_ORDER_TYPES, position_side)


def get_stop_loss_for_symbol(
    client: Client, symbol: str, position_side: str = "BOTH"
) -> float | None:
    """Get the stop-loss price for a symbol from open orders.

    Pass position_side in hedge mode to match only the correct side's SL order.
    """
    try:
        orders = client.futures_get_open_orders(symbol=symbol)
    except Exception as e:
        logging.warning("SL fetch failed for %s: %s", symbol, e)
        return None
    return _find_sl_in_orders(orders, position_side)


def _fetch_all_tpsl_prices(
    client: Client,
) -> dict[tuple[str, str], dict[str, float | None]]:
    """Fetch all open orders and return {(symbol, positionSide): {"sl": price, "tp": price}}.

    Keying by (symbol, positionSide) supports hedge mode where LONG and SHORT
    positions on the same symbol have independent SL/TP orders.
    """
    try:
        all_orders: list[dict[str, Any]] = client.futures_get_open_orders()
    except Exception as e:
        logging.warning("Failed to fetch all open orders: %s", e)
        return {}

    orders_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for o in all_orders:
        key = (o.get("symbol", ""), o.get("positionSide", "BOTH"))
        orders_by_key.setdefault(key, []).append(o)

    result: dict[tuple[str, str], dict[str, float | None]] = {}
    for (sym, side), orders in orders_by_key.items():
        sl = _find_sl_in_orders(orders, side)
        tp = _find_tp_in_orders(orders, side)
        if sl is not None or tp is not None:
            result[(sym, side)] = {"sl": sl, "tp": tp}
    return result


def _build_position_row(
    pos: dict[str, Any],
    tpsl_prices: dict[tuple[str, str], dict[str, float | None]],
    wallet_balance: float,
) -> tuple[list[Any], float]:
    """Build a single display row for one open position. Returns (row, sl_risk_usd)."""
    symbol = pos["symbol"]
    amt = float(pos["positionAmt"])
    entry = float(pos["entryPrice"])
    mark = float(pos["markPrice"])
    notional = abs(float(pos["notional"]))
    margin = float(pos.get("positionInitialMargin", 0)) or 1e-6
    side_text = "LONG" if amt > 0 else "SHORT"
    position_side = pos.get("positionSide", "BOTH")

    side_color = _GREEN if amt > 0 else _RED
    side_colored = f"{side_color}{side_text}{_RESET}"

    pnl = float(pos.get("unRealizedProfit", 0))
    pnl_pct = (pnl / margin) * 100
    leverage = round(notional / margin)

    tpsl = tpsl_prices.get((symbol, position_side)) or tpsl_prices.get((symbol, "BOTH"))
    actual_sl = tpsl["sl"] if tpsl else None
    actual_tp = tpsl["tp"] if tpsl else None

    if actual_sl:
        if side_text == "SHORT":
            sl_percent = (entry - actual_sl) / entry * 100
        else:
            sl_percent = (actual_sl - entry) / entry * 100
        sl_risk_usd = notional * abs(sl_percent) / 100
        actual_sl_str = f"{actual_sl:.5f}"
        sl_size_str = color_sl_size(sl_percent)
        sl_usd_str = colorize_dollar(sl_risk_usd)
    else:
        sl_risk_usd = 0.0
        actual_sl_str = "-"
        sl_size_str = "-"
        sl_usd_str = "-"

    liq_price_raw = float(pos.get("liquidationPrice") or 0)
    liq_price: float | None = liq_price_raw if liq_price_raw > 0 else None

    isolated_wallet = float(pos.get("isolatedWallet") or 0)
    margin_type = "isolated" if isolated_wallet > 0 else "cross"

    risk_pct = (margin / wallet_balance * 100) if wallet_balance else 0.0

    row: list[Any] = [
        symbol,  # 0
        side_colored,  # 1
        leverage,  # 2
        round(entry, 5),  # 3
        round(mark, 5),  # 4
        round(margin, 2),  # 5
        round(notional, 2),  # 6
        colorize_dollar(pnl),  # 7
        colorize(pnl_pct),  # 8
        f"{risk_pct:.2f}%",  # 9
        actual_sl_str,  # 10
        sl_size_str,  # 11
        sl_usd_str,  # 12
        pnl_pct,  # 13 sort key
        sl_risk_usd,  # 14 sort key
        actual_tp,  # 15 tp_price
        liq_price,  # 16 liq_price
        position_side,  # 17
        margin_type,  # 18
    ]
    return row, sl_risk_usd


def _placeholder_row(symbol: str, leverage: int) -> list[Any]:
    """Row shown for configured symbols without an open position."""
    return [
        symbol,  # 0
        "-",  # 1
        leverage,  # 2
        "-",  # 3
        "-",  # 4
        "-",  # 5
        "-",  # 6
        "-",  # 7
        "-",  # 8
        "-",  # 9
        "-",  # 10
        "-",  # 11
        "-",  # 12
        -999,  # 13 sort key
        -9999,  # 14 sort key
        None,  # 15 tp_price
        None,  # 16 liq_price
        "BOTH",  # 17 position_side
        "cross",  # 18 margin_type
    ]


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

    wallet_balance, _cross_unrealized_pnl, available_balance = get_wallet_balance(
        client
    )

    open_positions: list[dict[str, Any]] = [
        pos
        for pos in positions
        if pos["symbol"] in coins_config and float(pos["positionAmt"]) != 0
    ]

    # One bulk fetch instead of one REST call per open position.
    # Fall back to per-symbol if the bulk call returns nothing (e.g. API error
    # swallowed silently, or exchange quirk) so SL/TP is never silently dropped.
    tpsl_prices = _fetch_all_tpsl_prices(client) if open_positions else {}
    for pos in open_positions:
        sym = pos["symbol"]
        pos_side = pos.get("positionSide", "BOTH")
        if (sym, pos_side) in tpsl_prices or (sym, "BOTH") in tpsl_prices:
            continue
        sl = get_stop_loss_for_symbol(client, sym, pos_side)
        if sl is not None:
            tpsl_prices[(sym, pos_side)] = {"sl": sl, "tp": None}

    # Sum unrealized PnL from positions — crossUnPnl is 0 for isolated margin.
    unrealized_pnl = sum(float(p.get("unRealizedProfit", 0)) for p in open_positions)

    filtered: list[Any] = []
    total_risk_usd = 0.0
    for pos in open_positions:
        row, sl_risk_usd = _build_position_row(pos, tpsl_prices, wallet_balance)
        total_risk_usd += sl_risk_usd
        filtered.append(row)

    if not hide_empty:
        open_symbols = {row[0] for row in filtered}
        for symbol in coin_order:
            if symbol not in open_symbols:
                filtered.append(
                    _placeholder_row(symbol, coins_config[symbol]["leverage"])
                )

    if sort_by == "pnl_pct":
        filtered.sort(key=lambda r: r[13], reverse=descending)
    elif sort_by == "sl_usd":
        filtered.sort(key=lambda r: r[14], reverse=descending)
    else:
        filtered.sort(
            key=lambda r: coin_order.index(r[0]) if r[0] in coin_order else 999
        )

    logging.info("Found %d open position(s)", len(open_positions))
    # NOTE: do NOT slice to [:13] here — callers that need only display columns
    # (e.g. display_table → tabulate) slice themselves. The web router uses [15:18].
    return filtered, total_risk_usd, wallet_balance, unrealized_pnl, available_balance


_DISPLAY_HEADERS = [
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

    output: list[str] = [
        f"\n\U0001f4b0 Wallet Balance: ${wallet:,.2f}",
        f"\U0001f4bc Available Balance: ${available_balance:,.2f}",
        (
            f"\U0001f4ca Total Unrealized PnL: {colorize_dollar(unrealized)} "
            f"({colorize(unrealized_pct)} of wallet)"
        ),
        f"\U0001f9fe Wallet w/ Unrealized: ${total:,.2f}",
        f"\u26a0\ufe0f Total SL Risk: {color_risk_usd(total_risk_usd, wallet)}\n",
    ]
    for target in wallet_target:
        output.append(display_progress_bar(total, target))
    if wallet_target_invalid:
        bad = ", ".join(repr(e) for e in wallet_target_invalid)
        output.append(f"\033[93m⚠ WALLET_TARGET: invalid entries skipped: {bad}\033[0m")

    if compact:
        return "\n".join(output)

    output.append(
        tabulate(
            [row[:13] for row in table],
            headers=_DISPLAY_HEADERS,
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

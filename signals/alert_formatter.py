"""Signal event model and Telegram alert formatter."""

from dataclasses import dataclass


@dataclass
class SignalEvent:
    symbol: str
    timeframe: str
    strategy: str
    direction: str  # "long" | "short"
    reason: str  # raw reason string from detector (e.g. "fvg_long@43200.00-43350.00")
    open_time: int  # Unix ms of the signal candle
    price: float  # close price of the signal candle


def format_signal_alert(
    event: SignalEvent,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
) -> str:
    """Format a SignalEvent as a Markdown Telegram message.

    Computes SL and TP levels from sl_pct and tp_r (risk:reward ratio).
    """
    direction_label = "LONG 🟢" if event.direction == "long" else "SHORT 🔴"
    if event.direction == "long":
        sl_price = event.price * (1 - sl_pct)
        tp_price = event.price * (1 + sl_pct * tp_r)
    else:
        sl_price = event.price * (1 + sl_pct)
        tp_price = event.price * (1 - sl_pct * tp_r)

    sl_pct_display = sl_pct * 100
    tp_pct_display = sl_pct * tp_r * 100

    return (
        f"*SIGNAL — {event.symbol} {event.timeframe}*\n"
        f"Direction: {direction_label}  Strategy: `{event.strategy}`\n"
        f"Reason: `{event.reason}`\n"
        f"Price: {event.price:,.2f}\n"
        f"SL: {sl_price:,.2f} ({sl_pct_display:.1f}%)  "
        f"TP: {tp_price:,.2f} ({tp_pct_display:.1f}% | {tp_r:.1f}x R)"
    )

"""Signal event model and Telegram alert formatter."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_SGT = timezone(timedelta(hours=8))


def _fmt_time(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=_SGT).strftime("%d-%b %H:%M")


def _stars(n: int) -> str:
    """Return a 5-char star string, e.g. n=4 → '★★★★☆'."""
    return "★" * n + "☆" * (5 - n)


@dataclass
class SignalEvent:
    symbol: str
    timeframe: str
    strategy: str
    direction: str  # "long" | "short"
    reason: str  # raw reason string from detector (e.g. "fvg_long@43200.00-43350.00")
    open_time: int  # Unix ms of the signal candle
    price: float  # close price of the signal candle
    sl_price: float = 0.0  # structural invalidation level (0 = use sl_pct fallback)
    context: str = ""  # human-readable pattern context (e.g. candle timestamps)
    confidence: int = 0  # 1–5 editorial quality score (0 = unset); shown as stars


def _resolve_sl(
    direction: str,
    price: float,
    sl_price: float,
    sl_pct: float,
) -> float:
    """Return structural SL if valid, otherwise fall back to percentage-based SL."""
    if sl_price > 0:
        if direction == "long" and sl_price < price:
            return sl_price
        if direction == "short" and sl_price > price:
            return sl_price
    if direction == "long":
        return price * (1 - sl_pct)
    return price * (1 + sl_pct)


def _tightest_sl(
    events: list["SignalEvent"],
    direction: str,
    price: float,
    sl_pct: float,
) -> float:
    """Return the tightest valid structural SL across a list of events.

    Tightest for long = highest sl_price below price (smallest risk distance).
    Tightest for short = lowest sl_price above price.
    Falls back to pct-based SL if no valid structural level exists.
    """
    if direction == "long":
        valid = [e.sl_price for e in events if 0 < e.sl_price < price]
        return max(valid) if valid else price * (1 - sl_pct)
    else:
        valid = [e.sl_price for e in events if e.sl_price > price]
        return min(valid) if valid else price * (1 + sl_pct)


def format_signal_alert(
    event: "SignalEvent",
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
) -> str:
    """Format a single SignalEvent as a Markdown Telegram message.

    Uses structural sl_price when valid; falls back to sl_pct otherwise.
    """
    return format_confluence_alert([event], sl_pct=sl_pct, tp_r=tp_r)


def format_confluence_alert(
    events: list["SignalEvent"],
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
) -> str:
    """Format one or more SignalEvents (same symbol/tf/direction) as a Telegram message.

    Single event: original single-strategy layout.
    Multiple events: stacked confluence layout showing all strategies.
    SL is the tightest structural level across all events.
    """
    first = events[0]
    direction_label = "LONG 🟢" if first.direction == "long" else "SHORT 🔴"
    price = first.price
    signal_time = _fmt_time(first.open_time)

    sl_price = _tightest_sl(events, first.direction, price, sl_pct)
    if first.direction == "long":
        sl_dist = price - sl_price
        tp_price = price + sl_dist * tp_r
    else:
        sl_dist = sl_price - price
        tp_price = price - sl_dist * tp_r

    sl_pct_display = abs(sl_dist / price) * 100
    tp_pct_display = abs(sl_dist / price) * tp_r * 100

    if len(events) == 1:
        ev = events[0]
        stars = f"  {_stars(ev.confidence)}" if ev.confidence else ""
        header = (
            f"*SIGNAL — {ev.symbol} {ev.timeframe}*\n"
            f"Direction: {direction_label}  Strategy: `{ev.strategy}`{stars}\n"
            f"Reason: `{ev.reason}`\n"
        )
        if ev.context:
            header += f"{ev.context}\n"
    else:
        header = (
            f"*SIGNAL — {first.symbol} {first.timeframe}*\n"
            f"Direction: {direction_label}  Confluence: {len(events)} strategies\n"
        )
        for ev in events:
            stars = f" {_stars(ev.confidence)}" if ev.confidence else ""
            line = f"• `{ev.strategy}`{stars} — `{ev.reason}`"
            if ev.context:
                line += f"  ({ev.context})"
            header += line + "\n"

    return (
        header
        + f"Price: {price:,.2f}  |  {signal_time} SGT\n"
        + f"SL: {sl_price:,.2f} ({sl_pct_display:.1f}%)  "
        + f"TP: {tp_price:,.2f} ({tp_pct_display:.1f}% | {tp_r:.1f}x R)"
    )

"""Signal event model and Telegram alert formatter."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_MYT = timezone(timedelta(hours=8))


_SESSION_EMOJI = {
    "Asia": "🌏",
    "London": "🇬🇧",
    "NY": "🗽",
}


def _get_session_label(dt: datetime) -> str:
    """Return the ICT AMD session label for a datetime, or empty string if outside.

    Sessions (MYT / UTC+8):
    - Asia   (Accumulation):  04:00–07:59
    - London (Manipulation):  10:00–12:59
    - NY     (Distribution):  17:30–19:59
    """
    dt_myt = dt.astimezone(_MYT)
    hour = dt_myt.hour
    minute = dt_myt.minute
    # Asia: 04:00–07:59 MYT
    if 4 <= hour < 8:
        return "Asia"
    # London: 10:00–12:59 MYT
    if 10 <= hour < 13:
        return "London"
    # NY: 17:30–19:59 MYT
    if hour == 17 and minute >= 30:
        return "NY"
    if hour == 18 or hour == 19:
        return "NY"
    return ""


def _fmt_time(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=_MYT).strftime("%d-%b %H:%M")


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
    conflict: bool = False  # True when opposing direction fired same cycle


def _widest_sl(
    events: list["SignalEvent"],
    direction: str,
    price: float,
    sl_pct: float,
) -> float:
    """Return the widest (most conservative) structural SL across a list of events.

    Widest for long = lowest sl_price below price (largest risk distance).
    Widest for short = highest sl_price above price.
    Falls back to pct-based SL if no valid structural level exists.

    Using the widest level for confluence means the trade has room to breathe —
    you need the furthest structural level to actually be invalidated.
    """
    if direction == "long":
        valid = [e.sl_price for e in events if 0 < e.sl_price < price]
        return min(valid) if valid else price * (1 - sl_pct)
    else:
        valid = [e.sl_price for e in events if e.sl_price > price]
        return max(valid) if valid else price * (1 + sl_pct)


def format_signal_alert(
    event: "SignalEvent",
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
    min_sl_pct: float = 0.0,
) -> str:
    """Format a single SignalEvent as a Markdown Telegram message.

    Uses structural sl_price when valid; falls back to sl_pct otherwise.
    min_sl_pct: if set, SL distance is floored at this fraction of price.
    """
    return format_confluence_alert(
        [event], sl_pct=sl_pct, tp_r=tp_r, min_sl_pct=min_sl_pct
    )


def format_confluence_alert(
    events: list["SignalEvent"],
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
    min_sl_pct: float = 0.0,
    backtest_summary: str | None = None,
) -> str:
    """Format one or more SignalEvents (same symbol/tf/direction) as a Telegram message.

    Single event: original single-strategy layout.
    Multiple events: stacked confluence layout showing all strategies.
    SL is the widest (most conservative) structural level across all events.
    min_sl_pct: if set, SL distance is floored at this fraction of price (e.g. 0.005
    ensures SL is at least 0.5% away from entry — useful to suppress noise signals).
    """
    first = events[0]
    direction_label = "LONG 🟢" if first.direction == "long" else "SHORT 🔴"
    price = first.price
    signal_time = _fmt_time(first.open_time)
    signal_dt = datetime.fromtimestamp(first.open_time / 1000, tz=_MYT)
    session = _get_session_label(signal_dt)
    session_line = f"{_SESSION_EMOJI[session]} {session} Kill Zone\n" if session else ""

    sl_price = _widest_sl(events, first.direction, price, sl_pct)
    if min_sl_pct > 0:
        min_dist = price * min_sl_pct
        if first.direction == "long" and (price - sl_price) < min_dist:
            sl_price = price - min_dist
        elif first.direction == "short" and (sl_price - price) < min_dist:
            sl_price = price + min_dist
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
        conflict_tag = " ⚠️ conflict" if ev.conflict else ""
        header = (
            f"<b>SIGNAL — {ev.symbol} {ev.timeframe}</b>\n"
            f"Direction: {direction_label}  Strategy: <code>{ev.strategy}</code>{stars}\n"
            f"Reason: <code>{ev.reason}</code>{conflict_tag}\n"
        )
        if ev.context:
            header += f"{ev.context}\n"
    else:
        header = (
            f"<b>SIGNAL — {first.symbol} {first.timeframe}</b>\n"
            f"Direction: {direction_label}  Confluence: {len(events)} strategies\n"
        )
        for ev in events:
            stars = f" {_stars(ev.confidence)}" if ev.confidence else ""
            conflict_tag = " ⚠️ conflict" if ev.conflict else ""
            line = f"• <code>{ev.strategy}</code>{stars} — <code>{ev.reason}</code>{conflict_tag}"
            if ev.context:
                line += f"  ({ev.context})"
            header += line + "\n"

    msg = (
        header
        + f"Price: {price:,.2f}  |  {signal_time} MYT\n"
        + session_line
        + f"SL: {sl_price:,.2f} ({sl_pct_display:.1f}%)  "
        + f"TP: {tp_price:,.2f} ({tp_pct_display:.1f}% | {tp_r:.1f}x R)"
    )
    if backtest_summary:
        msg += f"\n{backtest_summary}"
    return msg

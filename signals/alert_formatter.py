"""Signal event model and Telegram alert formatter."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_MYT = timezone(timedelta(hours=8))
_ET = ZoneInfo("America/New_York")


_SESSION_EMOJI = {
    "Asia": "🌏",
    "London": "🇬🇧",
    "NY": "🗽",
}


def _get_session_label(dt: datetime) -> str:
    """Return the ICT kill zone label for a datetime, or empty string if outside.

    ICT kill zones (ET, DST-aware via America/New_York):
    - Asia    (Accumulation):  20:00–22:59 ET — around Tokyo open
    - London  (Manipulation):  02:00–04:59 ET — before/around London open (~03:00 ET)
    - NY      (Distribution):  07:00–09:59 ET — before/around NY open (09:30 ET)

    Using ET (America/New_York) handles EST/EDT transitions automatically so the
    windows stay anchored to the correct real-world session opens year-round.
    """
    dt_et = dt.astimezone(_ET)
    hour = dt_et.hour
    # Asia KZ: 8 PM – 11 PM ET
    if 20 <= hour < 23:
        return "Asia"
    # London KZ: 2 AM – 5 AM ET
    if 2 <= hour < 5:
        return "London"
    # NY KZ: 7 AM – 10 AM ET
    if 7 <= hour < 10:
        return "NY"
    return ""


def _fmt_time(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=_MYT).strftime("%d-%b %H:%M")


def _stars(n: int) -> str:
    """Return a 5-char star string, e.g. n=4 → '★★★★☆'."""
    return "★" * n + "☆" * (5 - n)


@dataclass
class StatsContext:
    """Statistical context for a signal alert — appended as two plain-English lines."""

    today_dow: str  # e.g. "Thursday"
    p1_low_pct_today: float  # P1=Low % for today's DOW, e.g. 0.58
    adr_14: float  # 14-day ADR as fraction, e.g. 0.028
    adr_consumed_pct: float | None  # today_range / adr_14, or None if unknown
    peak_high_hour_myt: int  # overall peak high hour (MYT) — kept for backwards compat
    peak_low_hour_myt: int  # overall peak low hour (MYT) — kept for backwards compat
    bull_pct_today: float = 0.5  # % of this DOW that closed bullish, e.g. 0.67
    avg_return_today: float = 0.0  # avg directional return this DOW, e.g. +0.021
    peak_high_hour_dow: int | None = None  # per-DOW peak high hour (MODE per DOW)
    peak_low_hour_dow: int | None = None  # per-DOW peak low hour
    wk_low_still_ahead_pct: float | None = (
        None  # % weeks where weekly low still to come
    )
    wk_high_still_ahead_pct: float | None = (
        None  # % weeks where weekly high still to come
    )
    adr_move_up: bool | None = (
        None  # True if today's move was upward (close > range midpoint)
    )
    wk_low_still_ahead_conditioned_pct: float | None = None
    wk_high_still_ahead_conditioned_pct: float | None = None
    wk_move_bucket: str | None = None  # "small" | "medium" | "large"


def _adr_bar(consumed_pct: float) -> str:
    """Return a 10-char ASCII progress bar for ADR consumption.

    0–100%: filled █ + empty ░, e.g. 88% → [████████░░]
    >100%:  full bar + overflow indicator ▓, e.g. 112% → [██████████▓]
    """
    BAR_LEN = 10
    filled = min(round(consumed_pct * BAR_LEN), BAR_LEN)
    bar = "█" * filled + "░" * (BAR_LEN - filled)
    suffix = "▓" if consumed_pct > 1.0 else ""
    return f"[{bar}{suffix}]"


def _format_stats_line(ctx: "StatsContext", direction: str) -> str:
    """Format the stats context as two plain-English lines for Telegram.

    Line 1: bull%, P1 context (direction-aware), ADR bar
    Line 2: TP window (peak hour for target direction) + weekly timing
    """
    dow_short = ctx.today_dow[:3]
    dow_plural = ctx.today_dow + "s"  # e.g. "Mondays"

    avg_ret_str = f"{ctx.avg_return_today:+.1%}"
    bull_str = (
        f"{dow_short} closes bullish {ctx.bull_pct_today:.0%} ({avg_ret_str} avg)"
    )

    is_long = direction != "short"
    # "still ahead" = probability the directional extreme has NOT been set yet.
    # High number = caution (likely more adverse move coming).
    # Low number = favourable (extreme likely already behind you).
    if is_long:
        still_ahead = 1 - ctx.p1_low_pct_today
        p1_str = f"Low still ahead {still_ahead:.0%} of {dow_plural}"
    else:
        still_ahead = ctx.p1_low_pct_today
        p1_str = f"High still ahead {still_ahead:.0%} of {dow_plural}"

    if ctx.adr_consumed_pct is not None:
        bar = _adr_bar(ctx.adr_consumed_pct)
        adr_str = f"ADR {bar} {ctx.adr_consumed_pct:.0%} · {ctx.adr_14:.1%}"
    else:
        adr_str = f"ADR {ctx.adr_14:.1%}"

    line1 = f"📐 {bull_str} · {p1_str} · {adr_str}"

    parts2 = []
    if is_long and ctx.peak_high_hour_dow is not None:
        parts2.append(
            f"TP window: high ~{ctx.peak_high_hour_dow:02d}:00 MYT on {dow_plural}"
        )
    elif not is_long and ctx.peak_low_hour_dow is not None:
        parts2.append(
            f"TP window: low ~{ctx.peak_low_hour_dow:02d}:00 MYT on {dow_plural}"
        )

    if is_long:
        if (
            ctx.wk_low_still_ahead_conditioned_pct is not None
            and ctx.wk_move_bucket is not None
        ):
            parts2.append(
                f"Weekly low: {ctx.wk_low_still_ahead_conditioned_pct:.0%} still ahead"
                f" ({ctx.wk_move_bucket} move)"
            )
        elif ctx.wk_low_still_ahead_pct is not None:
            parts2.append(f"Weekly low: {ctx.wk_low_still_ahead_pct:.0%} still ahead")
    elif not is_long:
        if (
            ctx.wk_high_still_ahead_conditioned_pct is not None
            and ctx.wk_move_bucket is not None
        ):
            parts2.append(
                f"Weekly high: {ctx.wk_high_still_ahead_conditioned_pct:.0%} still ahead"
                f" ({ctx.wk_move_bucket} move)"
            )
        elif ctx.wk_high_still_ahead_pct is not None:
            parts2.append(f"Weekly high: {ctx.wk_high_still_ahead_pct:.0%} still ahead")

    if parts2:
        return line1 + "\n🎯 " + " · ".join(parts2)
    return line1


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
    low_volume: bool = False  # True when volume was below confirmation threshold
    volume_spike: bool = (
        False  # True when volume was above 3× rolling mean (high conviction)
    )
    tp_price: float = (
        0.0  # structural TP from detector (e.g. 1.618 fib ext); 0 = use tp_r fallback
    )


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
    cme_gap_warning: str | None = None,
) -> str:
    """Format a single SignalEvent as a Markdown Telegram message.

    Uses structural sl_price when valid; falls back to sl_pct otherwise.
    min_sl_pct: if set, SL distance is floored at this fraction of price.
    """
    return format_confluence_alert(
        [event],
        sl_pct=sl_pct,
        tp_r=tp_r,
        min_sl_pct=min_sl_pct,
        cme_gap_warning=cme_gap_warning,
    )


def format_confluence_alert(
    events: list["SignalEvent"],
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
    min_sl_pct: float = 0.0,
    backtest_summary: str | None = None,
    stats_context: "StatsContext | None" = None,
    cme_gap_warning: str | None = None,
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
        structural_tp = first.tp_price if first.tp_price > price else 0.0
        tp_price = structural_tp if structural_tp > 0 else price + sl_dist * tp_r
    else:
        sl_dist = sl_price - price
        structural_tp = first.tp_price if 0 < first.tp_price < price else 0.0
        tp_price = structural_tp if structural_tp > 0 else price - sl_dist * tp_r

    sl_pct_display = abs(sl_dist / price) * 100
    actual_r = abs(tp_price - price) / sl_dist if sl_dist > 0 else tp_r
    tp_pct_display = abs(tp_price - price) / price * 100

    if len(events) == 1:
        ev = events[0]
        stars = f"  {_stars(ev.confidence)}" if ev.confidence else ""
        conflict_tag = " ⚠️ conflict" if ev.conflict else ""
        header = (
            f"<b>SIGNAL — {ev.symbol} {ev.timeframe}  ·  {direction_label}</b>\n"
            f"<code>{ev.strategy}</code>{stars}{conflict_tag}\n"
            f"<code>{ev.reason}</code>\n"
        )
        if ev.context:
            header += f"{ev.context}\n"
        if ev.volume_spike:
            header += "⚡ Volume spike — high conviction\n"
        elif ev.low_volume:
            header += "⚠️ Low volume — weaker conviction\n"
    else:
        header = (
            f"<b>SIGNAL — {first.symbol} {first.timeframe}  ·  {direction_label}</b>\n"
            f"Confluence: {len(events)} strategies\n"
        )
        for ev in events:
            stars = f" {_stars(ev.confidence)}" if ev.confidence else ""
            conflict_tag = " ⚠️ conflict" if ev.conflict else ""
            line = f"• <code>{ev.strategy}</code>{stars} — <code>{ev.reason}</code>{conflict_tag}"
            if ev.context:
                line += f"  ({ev.context})"
            header += line + "\n"
        if any(e.volume_spike for e in events):
            header += "⚡ Volume spike — high conviction\n"
        elif any(e.low_volume for e in events):
            header += "⚠️ Low volume — weaker conviction\n"

    sl_tp = (
        f"SL: {sl_price:,.2f}  ({sl_pct_display:.1f}%)\n"
        f"TP: {tp_price:,.2f}  ({tp_pct_display:.1f}%  ·  {actual_r:.1f}R)"
    )
    msg = (
        header + f"\n{price:,.2f}  ·  {signal_time} MYT\n" + session_line + f"\n{sl_tp}"
    )
    if cme_gap_warning:
        msg += f"\n{cme_gap_warning}"
    if backtest_summary:
        msg += f"\n\n{backtest_summary}"
    if stats_context is not None:
        msg += f"\n\n{_format_stats_line(stats_context, first.direction)}"
    return msg

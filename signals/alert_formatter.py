"""Signal event model and Telegram alert formatter."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

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
class ConfluenceData:
    """Co-firing confluence metadata attached to a SignalEvent.

    Populated by _find_live_cofire in signal_lib.py when a known-good strategy
    pair from backtest_combos fired within ±window candles of the current signal.

    orderflow_signals is a step-5 extension point for CoinGlass/NPOC data —
    each entry is a pre-formatted string appended to the blockquote.
    """

    co_strategy: str  # the strategy that co-fired (not the primary alert strategy)
    candles_ago: int  # 0 = same candle, N = N candles before current signal
    avg_r: float  # backtest combo avg R
    trades: int  # backtest combo closed trades
    win_rate: float  # 0.0–1.0
    type_a: str  # strategy_type of co_strategy (e.g. "fib")
    type_b: str  # strategy_type of primary strategy (e.g. "structural")
    orderflow_signals: list[str] = field(default_factory=list)  # step 5: OI/CVD/NPOC
    # Cross-TF fields (empty string = same-TF combo).
    htf_tf: str = ""  # HTF timeframe, e.g. "4h" — set for cross-TF confluences
    ltf_tf: str = ""  # LTF timeframe, e.g. "15m" — set for cross-TF confluences


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
    confluence_combo: "ConfluenceData | None" = (
        None  # set by _find_live_cofire when a known-good pair co-fired recently
    )


# ---------------------------------------------------------------------------
# Candle-level warning helpers
# ---------------------------------------------------------------------------


def _is_marubozu(o: float, h: float, lo: float, c: float) -> bool:
    """Both wicks ≤ 10% of body → wickless candle, body tends to fill first."""
    body = abs(c - o)
    if body == 0:
        return False
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - lo
    return upper_wick / body <= 0.10 and lower_wick / body <= 0.10


def _is_doji(o: float, h: float, lo: float, c: float) -> bool:
    """Body < 10% of total range → pure indecision candle."""
    total_range = h - lo
    if total_range == 0:
        return False
    return abs(c - o) / total_range <= 0.10


def _is_inside_bar(h: float, lo: float, prev_h: float, prev_l: float) -> bool:
    """Signal candle range entirely within prior candle's range → breakout unconfirmed."""
    return h <= prev_h and lo >= prev_l


def _wick_rejection_against(
    o: float, h: float, lo: float, c: float, direction: str
) -> bool:
    """Wick pointing against signal direction > 40% of range → price resisted.

    LONG: long upper wick = price rejected higher levels on this candle.
    SHORT: long lower wick = price rejected lower levels on this candle.
    """
    total_range = h - lo
    if total_range == 0:
        return False
    if direction == "long":
        upper_wick = h - max(o, c)
        return upper_wick / total_range > 0.40
    else:
        lower_wick = min(o, c) - lo
        return lower_wick / total_range > 0.40


def _has_equal_levels(
    df: pd.DataFrame,
    price: float,
    direction: str,
    lookback: int = 10,
    tol_pct: float = 0.0015,
) -> bool:
    """Detect equal highs (above price, warns on SHORT) or equal lows (below, warns on LONG).

    Equal highs above entry = buy-side liquidity → sweep likely before SHORT continues.
    Equal lows below entry = sell-side liquidity → sweep likely before LONG continues.
    Excludes the signal candle itself; looks at the preceding `lookback` candles.
    """
    hist = df.iloc[:-1].tail(lookback)
    if len(hist) < 2:
        return False
    if direction == "long":
        candidates = [float(v) for v in hist["low"].values if float(v) < price]
    else:
        candidates = [float(v) for v in hist["high"].values if float(v) > price]
    if len(candidates) < 2:
        return False
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            if abs(candidates[i] - candidates[j]) / price <= tol_pct:
                return True
    return False


def _has_consecutive_candles(df: pd.DataFrame, direction: str, n: int = 3) -> bool:
    """Last n candles (including signal candle) all closed in signal direction → overextension."""
    if len(df) < n:
        return False
    recent = df.tail(n)
    closes = recent["close"].values
    opens = recent["open"].values
    if direction == "long":
        return all(closes[i] > opens[i] for i in range(len(closes)))
    else:
        return all(closes[i] < opens[i] for i in range(len(closes)))


def _build_candle_warnings(
    events: list["SignalEvent"],
    ohlcv_df: pd.DataFrame | None,
) -> list[str]:
    """Build ordered list of warning/note strings for the consolidated warnings block.

    Volume notes (moved from header), candle shape checks, structural, and momentum
    warnings. All are silent unless triggered. Rendered after SL/TP in the alert.
    """
    notes: list[str] = []

    # Volume conviction note (moved from header for section consolidation)
    if any(e.volume_spike for e in events):
        notes.append("⚡ Volume spike — high conviction")
    elif any(e.low_volume for e in events):
        notes.append("⚠️ Low volume — weaker conviction")

    if ohlcv_df is None or len(ohlcv_df) < 2:
        return notes

    last = ohlcv_df.iloc[-1]
    o = float(last["open"])
    h = float(last["high"])
    lo = float(last["low"])
    c = float(last["close"])
    prev = ohlcv_df.iloc[-2]
    prev_h = float(prev["high"])
    prev_l = float(prev["low"])
    direction = events[0].direction
    price = events[0].price

    # W7: Doji — check before marubozu (doji has no dominant body to call wickless)
    if _is_doji(o, h, lo, c):
        notes.append("⚠️ Doji signal candle — direction uncertain")
    # W1: Marubozu (wickless body)
    elif _is_marubozu(o, h, lo, c):
        notes.append("⚠️ Wickless candle — body tends to fill first")

    # W8: Inside bar
    if _is_inside_bar(h, lo, prev_h, prev_l):
        notes.append("⚠️ Signal inside prior range — breakout unconfirmed")

    # W5: Wick rejection against direction (skip on doji — no dominant wick)
    if not _is_doji(o, h, lo, c) and _wick_rejection_against(o, h, lo, c, direction):
        wick_label = "Upper" if direction == "long" else "Lower"
        notes.append(f"⚠️ {wick_label} wick rejection — price resisted signal direction")

    # W2: Equal highs / equal lows (liquidity pool warning)
    if _has_equal_levels(ohlcv_df, price, direction):
        if direction == "long":
            notes.append("⚠️ Equal lows below — sell-side liquidity, sweep likely first")
        else:
            notes.append("⚠️ Equal highs above — buy-side liquidity, sweep likely first")

    # W6: Consecutive candles in signal direction → possible overextension
    if _has_consecutive_candles(ohlcv_df, direction):
        bias = "bullish" if direction == "long" else "bearish"
        notes.append(f"⚠️ 3 {bias} candles in a row — possible overextension")

    return notes


# ---------------------------------------------------------------------------
# SL helper
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Public formatters
# ---------------------------------------------------------------------------


def format_signal_alert(
    event: "SignalEvent",
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
    min_sl_pct: float = 0.0,
    cme_gap_warning: str | None = None,
    ohlcv_df: pd.DataFrame | None = None,
) -> str:
    """Format a single SignalEvent as a Markdown Telegram message.

    Uses structural sl_price when valid; falls back to sl_pct otherwise.
    min_sl_pct: if set, SL distance is floored at this fraction of price.
    ohlcv_df: recent OHLCV (signal candle = last row) for candle-level warnings.
    """
    return format_confluence_alert(
        [event],
        sl_pct=sl_pct,
        tp_r=tp_r,
        min_sl_pct=min_sl_pct,
        cme_gap_warning=cme_gap_warning,
        ohlcv_df=ohlcv_df,
    )


def format_confluence_alert(
    events: list["SignalEvent"],
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
    min_sl_pct: float = 0.0,
    backtest_summary: str | None = None,
    stats_context: "StatsContext | None" = None,
    cme_gap_warning: str | None = None,
    ohlcv_df: pd.DataFrame | None = None,
) -> str:
    """Format one or more SignalEvents (same symbol/tf/direction) as a Telegram message.

    Single event: original single-strategy layout.
    Multiple events: stacked confluence layout showing all strategies.
    SL is the widest (most conservative) structural level across all events.
    min_sl_pct: if set, SL distance is floored at this fraction of price (e.g. 0.005
    ensures SL is at least 0.5% away from entry — useful to suppress noise signals).
    ohlcv_df: recent OHLCV (signal candle = last row) enables candle-level warnings.

    Layout (sections separated by blank lines):
      1. Header — symbol/tf/direction, strategy, stars, reason, context
      2. Entry — price, time, session kill zone
      3. Levels — SL and TP
      4. Warnings — consolidated notes block (volume, candle shape, structure, momentum)
      5. Edge — backtest summary + co-firing confluence blockquote
      6. Context — stats lines (DOW, ADR, TP timing, weekly framing)
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

    # --- Section 1: Header (strategy identity, no volume note) ---
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

    # --- Section 2: Entry ---
    entry_line = f"{price:,.2f}  ·  {signal_time} MYT\n"

    # --- Section 3: Levels ---
    sl_tp = (
        f"SL: {sl_price:,.2f}  ({sl_pct_display:.1f}%)\n"
        f"TP: {tp_price:,.2f}  ({tp_pct_display:.1f}%  ·  {actual_r:.1f}R)"
    )

    # --- Section 4: Warnings (consolidated — volume, candle, structural, momentum) ---
    warnings = _build_candle_warnings(events, ohlcv_df)
    if cme_gap_warning:
        warnings.append(cme_gap_warning)
    warnings_block = ("\n\n" + "\n".join(warnings)) if warnings else ""

    # --- Section 5: Edge (backtest summary + co-firing confluence) ---
    edge_block = ""
    if backtest_summary:
        edge_block += f"\n\n{backtest_summary}"

    best_cofire: ConfluenceData | None = max(
        (e.confluence_combo for e in events if e.confluence_combo is not None),
        key=lambda c: c.avg_r,
        default=None,
    )
    if best_cofire is not None:
        if best_cofire.candles_ago == 0:
            ago_str = "this candle"
        elif best_cofire.candles_ago == 1:
            ago_str = "1 candle ago"
        else:
            ago_str = f"{best_cofire.candles_ago} candles ago"
        if best_cofire.htf_tf:
            cofire_header = (
                f"\n> ⚡⚡ CONFLUENCE ({best_cofire.htf_tf} → {best_cofire.ltf_tf})"
                f"\n> {best_cofire.co_strategy} ({best_cofire.htf_tf}) {ago_str}"
            )
        else:
            cofire_header = (
                f"\n> ⚡⚡ CONFLUENCE\n> {best_cofire.co_strategy} co-fired {ago_str}"
            )
        cofire_block = (
            cofire_header + f"\n> Combo avg R: +{best_cofire.avg_r:.2f}R"
            f" · {best_cofire.trades} trades"
            f" · {best_cofire.win_rate:.1%} win"
            f"\n> Types: {best_cofire.type_a} + {best_cofire.type_b}"
        )
        for sig in best_cofire.orderflow_signals:
            cofire_block += f"\n> {sig}"
        edge_block += f"\n{cofire_block}"

    # --- Section 6: Context (stats) ---
    stats_block = ""
    if stats_context is not None:
        stats_block = f"\n\n{_format_stats_line(stats_context, first.direction)}"

    return (
        header
        + f"\n{entry_line}"
        + session_line
        + f"\n{sl_tp}"
        + warnings_block
        + edge_block
        + stats_block
    )

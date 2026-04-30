"""Signal event + lightweight context dataclasses.

Moved verbatim from `signals/alert_formatter.py` in PR signal-1 to close the
`analytics/* → signals/*` boundary violation. `signals.alert_formatter` keeps
re-exporting these names for backwards compatibility.
"""

from dataclasses import dataclass, field


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
    wk_low_still_ahead_pct: float | None = None
    wk_high_still_ahead_pct: float | None = None
    adr_move_up: bool | None = None
    wk_low_still_ahead_conditioned_pct: float | None = None
    wk_high_still_ahead_conditioned_pct: float | None = None
    wk_move_bucket: str | None = None  # "small" | "medium" | "large"


@dataclass
class ConfluenceData:
    """Co-firing confluence metadata attached to a SignalEvent.

    Populated by _find_live_cofire in signal_lib.py when a known-good strategy
    pair from backtest_combos fired within ±window candles of the current signal.

    orderflow_signals is a step-5 extension point for CoinGlass/NPOC data —
    each entry is a pre-formatted string appended to the blockquote.
    """

    co_strategy: str
    candles_ago: int
    avg_r: float
    trades: int
    win_rate: float
    type_a: str
    type_b: str
    orderflow_signals: list[str] = field(default_factory=list)
    # Cross-TF fields (empty string = same-TF combo).
    htf_tf: str = ""
    ltf_tf: str = ""


@dataclass
class SignalEvent:
    symbol: str
    timeframe: str
    strategy: str
    direction: str  # "long" | "short"
    reason: str
    open_time: int  # Unix ms of the signal candle
    price: float  # close price of the signal candle
    sl_price: float = 0.0  # structural invalidation level (0 = use sl_pct fallback)
    context: str = ""  # human-readable pattern context
    confidence: int = 0  # 1–5 editorial quality score (0 = unset); shown as stars
    conflict: bool = False  # True when opposing direction fired same cycle
    low_volume: bool = False
    volume_spike: bool = False
    tp_price: float = 0.0  # structural TP from detector; 0 = use tp_r fallback
    confluence_combo: "ConfluenceData | None" = None

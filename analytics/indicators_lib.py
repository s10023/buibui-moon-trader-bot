"""Pure strategy signal detection functions for analytics.

All functions accept pandas DataFrames of OHLCV data and return a DataFrame
of detected signals with columns: open_time (int), direction (str), reason (str),
sl_price (float), context (str).

Seasonality returns a summary statistics DataFrame instead.
No module-level side effects.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd


@dataclass
class ParamSpec:
    name: str
    param_type: str  # "int" or "float"
    default: int | float
    min_val: int | float
    max_val: int | float
    description: str


@dataclass
class StrategySpec:
    name: str
    description: str
    params: list[ParamSpec] = field(default_factory=list)
    requires_funding: bool = False
    requires_secondary: bool = False
    # Taxonomy group for confluence logic. One of: structural, fib, price_action,
    # candlestick, flow, session. Empty string means unclassified.
    strategy_type: str = ""
    # 1–5 quality score per TF or a single value for all TFs.
    # Use a dict with a "default" key for TF-specific ratings:
    #   {"default": 2, "4h": 4}  → 4★ on 4h, 2★ on all other TFs
    # A plain int applies to all TFs.
    confidence: dict[str, int] | int = 3
    # Optional direction-split TP multiples. When set, the directional value is used
    # instead of tp_r for that direction. Falls back to tp_r when None.
    tp_r_long: float | None = None
    tp_r_short: float | None = None

    def get_tp_r(self, direction: str) -> float:
        """Resolve effective tp_r for a given direction.

        Falls back to the combined tp_r (2.0) when no directional value is set.
        """
        if direction == "long" and self.tp_r_long is not None:
            return self.tp_r_long
        if direction == "short" and self.tp_r_short is not None:
            return self.tp_r_short
        return 2.0

    def get_confidence(self, tf: str) -> int:
        """Resolve confidence for a given timeframe.

        If confidence is a plain int, returns it directly.
        If confidence is a dict, looks up tf, then "default", then falls back to 3.
        """
        if isinstance(self.confidence, int):
            return self.confidence
        return self.confidence.get(tf, self.confidence.get("default", 3))


STRATEGY_REGISTRY: dict[str, StrategySpec] = {
    "seasonality": StrategySpec(
        name="seasonality",
        description="Day-of-week, hour-of-day, and week-of-month return statistics.",
        strategy_type="session",
        confidence=2,
    ),
    "wick_fill": StrategySpec(
        name="wick_fill",
        description="Signals when price re-enters a prior candle's significant wick zone.",
        strategy_type="price_action",
        params=[
            ParamSpec(
                "min_wick_body_ratio",
                "float",
                1.5,
                0.1,
                2.0,
                "Minimum wick-to-body ratio to qualify as significant.",
            ),
            ParamSpec(
                "lookback",
                "int",
                20,
                1,
                200,
                "Candles to watch for wick zone fill after the signal candle.",
            ),
        ],
        confidence={"15m": 1, "1d": 2, "1h": 1, "4h": 1},
    ),
    "marubozu": StrategySpec(
        name="marubozu",
        description="Signals on retests of Marubozu (wickless) candle open prices.",
        strategy_type="price_action",
        params=[
            ParamSpec(
                "max_wick_ratio",
                "float",
                0.1,
                0.01,
                0.5,
                "Maximum wick-to-body ratio to qualify as wickless.",
            ),
            ParamSpec(
                "lookback",
                "int",
                30,
                1,
                200,
                "Candles to watch for marubozu open retest.",
            ),
            ParamSpec(
                "min_body_pct",
                "float",
                0.005,
                0.0001,
                0.05,
                "Minimum body size as fraction of open price to qualify as a Marubozu.",
            ),
        ],
        confidence={"15m": 1, "1h": 1, "4h": 1},
    ),
    "orb": StrategySpec(
        strategy_type="session",
        name="orb",
        description=(
            "Opening Range Breakout: high/low of the first 2 candles of each UTC day"
            " defines the range; a breakout signal fires on any later candle that closes"
            " outside the range (one signal per day per direction)."
        ),
        params=[
            ParamSpec(
                "range_candles",
                "int",
                2,
                1,
                4,
                "Number of candles from 00:00 UTC that form the opening range.",
            ),
        ],
        confidence={"15m": 1, "1h": 2, "4h": 2},
    ),
    "liquidity_sweep": StrategySpec(
        name="liquidity_sweep",
        description="Signals when a wick sweeps the rolling high/low but the candle closes back inside.",
        strategy_type="structural",
        params=[
            ParamSpec(
                "lookback",
                "int",
                20,
                2,
                200,
                "Rolling window size for swing high/low detection.",
            ),
        ],
        confidence={"15m": 1, "1d": 4, "1h": 1, "4h": 1},
    ),
    "fvg": StrategySpec(
        name="fvg",
        description="Fair Value Gap: signals when price fills a 3-candle imbalance zone.",
        strategy_type="structural",
        params=[
            ParamSpec(
                "lookback",
                "int",
                50,
                1,
                500,
                "Candles to watch for FVG fill after the gap forms.",
            ),
            ParamSpec(
                "min_gap_pct",
                "float",
                0.001,
                0.0001,
                0.05,
                "Minimum gap size as fraction of midpoint price (0.001 = 0.1%); filters noise.",
            ),
        ],
        confidence={"15m": 1, "1d": 1, "1h": 1, "4h": 1},
    ),
    "bos": StrategySpec(
        name="bos",
        description="Break of Structure / Change of Character: market structure shift signals.",
        strategy_type="structural",
        params=[
            ParamSpec(
                "swing_lookback",
                "int",
                5,
                1,
                50,
                "Half-window size for swing high/low identification (window = 2×n+1).",
            ),
            ParamSpec(
                "min_swing_pct",
                "float",
                0.005,
                0.0,
                0.1,
                "Minimum price range (fraction) a swing level must span to qualify for BOS/CHoCH.",
            ),
        ],
        confidence={"15m": 1, "1d": 1, "1h": 1, "4h": 1},
    ),
    "smt_divergence": StrategySpec(
        name="smt_divergence",
        description="SMT divergence: primary makes new swing extreme but correlated asset does not.",
        strategy_type="flow",
        params=[
            ParamSpec(
                "lookback",
                "int",
                10,
                2,
                200,
                "Rolling window for swing high/low comparison between assets.",
            ),
            ParamSpec(
                "trend_filter",
                "int",
                1,
                0,
                1,
                "Require close > EMA(50) for LONG and close < EMA(50) for SHORT (1=on, 0=off).",
            ),
        ],
        requires_secondary=True,
        confidence={"15m": 4, "1h": 3, "4h": 1},
    ),
    "eqh_eql": StrategySpec(
        name="eqh_eql",
        description="Equal Highs/Lows: liquidity sweep of a double-top or double-bottom level.",
        strategy_type="structural",
        params=[
            ParamSpec(
                "lookback",
                "int",
                50,
                5,
                500,
                "Candles to scan for equal high/low pairs.",
            ),
            ParamSpec(
                "tolerance_pct",
                "float",
                0.003,
                0.0001,
                0.05,
                "Max relative difference for two highs/lows to qualify as equal.",
            ),
        ],
        confidence={
            "15m": 1,
            "1h": 2,
            "4h": 3,
        },  # 0 trades in backtest — fires too rarely to have data
    ),
    "order_block": StrategySpec(
        name="order_block",
        description="ICT Order Block: last up/down-candle before displacement; entry on retest.",
        strategy_type="structural",
        params=[
            ParamSpec(
                "lookback",
                "int",
                50,
                5,
                500,
                "Candles to scan back for order block formations.",
            ),
            ParamSpec(
                "displacement_pct",
                "float",
                0.005,
                0.001,
                0.05,
                "Minimum % move on displacement candle to qualify an order block.",
            ),
        ],
        confidence={"15m": 1, "1d": 3, "1h": 1, "4h": 1},
    ),
    "cvd_divergence": StrategySpec(
        name="cvd_divergence",
        description="CVD Divergence: price makes a new swing extreme but cumulative volume delta disagrees.",
        strategy_type="flow",
        params=[
            ParamSpec(
                "lookback",
                "int",
                10,
                2,
                100,
                "Half-window for swing high/low detection.",
            ),
            ParamSpec(
                "cvd_lookback",
                "int",
                50,
                10,
                500,
                "Candles of CVD history to compare swing extremes across.",
            ),
        ],
        confidence={"15m": 2, "1h": 4},  # no CVD data in DB — never backtested
    ),
    "trend_day": StrategySpec(
        name="trend_day",
        description="Trend Day: candle opens near one extreme and closes near the other with a large body and tiny wicks.",
        strategy_type="price_action",
        params=[
            ParamSpec(
                "body_pct_min",
                "float",
                0.65,
                0.4,
                0.95,
                "Minimum body-to-range ratio (body / (high - low)) to qualify as a trend day.",
            ),
            ParamSpec(
                "wick_max",
                "float",
                0.15,
                0.0,
                0.4,
                "Maximum wick-to-range ratio for the wick in the trend direction (leading wick).",
            ),
        ],
        confidence={"15m": 1, "1d": 2, "1h": 1, "4h": 3},
    ),
    "engulfing": StrategySpec(
        name="engulfing",
        description="Bullish/Bearish Engulfing: current candle body fully engulfs the prior candle body.",
        strategy_type="candlestick",
        params=[
            ParamSpec(
                "sl_pct",
                "float",
                0.02,
                0.001,
                0.1,
                "Stop-loss distance as a fraction of entry price.",
            ),
            ParamSpec(
                "tp_r",
                "float",
                2.0,
                0.5,
                10.0,
                "Take-profit as a multiple of SL distance (risk-reward ratio).",
            ),
        ],
        confidence={"15m": 2, "1d": 4, "1h": 3, "4h": 3},
    ),
    "pin_bar": StrategySpec(
        name="pin_bar",
        description="Pin Bar: small body with a long wick (≥2× body) indicating rejection.",
        strategy_type="candlestick",
        params=[
            ParamSpec(
                "wick_ratio",
                "float",
                2.0,
                1.0,
                10.0,
                "Minimum ratio of rejection wick to body to qualify as a pin bar.",
            ),
            ParamSpec(
                "sl_pct",
                "float",
                0.02,
                0.001,
                0.1,
                "Stop-loss distance as a fraction of entry price.",
            ),
            ParamSpec(
                "tp_r",
                "float",
                2.0,
                0.5,
                10.0,
                "Take-profit as a multiple of SL distance.",
            ),
        ],
        confidence={"15m": 2, "1d": 4, "1h": 2, "4h": 2},
    ),
    "inside_bar": StrategySpec(
        name="inside_bar",
        description="Inside Bar breakout: body of current candle is fully within prior candle body; signal fires on breakout close.",
        strategy_type="price_action",
        params=[
            ParamSpec(
                "sl_pct",
                "float",
                0.02,
                0.001,
                0.1,
                "Stop-loss distance as a fraction of entry price.",
            ),
            ParamSpec(
                "tp_r",
                "float",
                2.0,
                0.5,
                10.0,
                "Take-profit as a multiple of SL distance.",
            ),
        ],
        confidence={"15m": 2, "1d": 3, "1h": 2, "4h": 2},
    ),
    "hammer_hanging_man": StrategySpec(
        name="hammer_hanging_man",
        description="Hammer (bullish reversal at recent low) / Hanging Man (bearish at recent high): pin-bar shape with context.",
        strategy_type="candlestick",
        params=[
            ParamSpec(
                "wick_ratio",
                "float",
                2.0,
                1.0,
                10.0,
                "Minimum lower-wick-to-body ratio for hammer/hanging man shape.",
            ),
            ParamSpec(
                "context_lookback",
                "int",
                10,
                3,
                50,
                "Bars to check for prior trend context (down for hammer, up for hanging man).",
            ),
            ParamSpec(
                "sl_pct",
                "float",
                0.02,
                0.001,
                0.1,
                "Stop-loss distance as a fraction of entry price.",
            ),
            ParamSpec(
                "tp_r",
                "float",
                2.0,
                0.5,
                10.0,
                "Take-profit as a multiple of SL distance.",
            ),
        ],
        confidence={"15m": 2, "1d": 4, "1h": 2, "4h": 2},
    ),
    "doji": StrategySpec(
        name="doji",
        description="Doji (open ≈ close) followed by a strongly directional candle.",
        strategy_type="candlestick",
        params=[
            ParamSpec(
                "body_threshold",
                "float",
                0.1,
                0.01,
                0.3,
                "Maximum body-to-range ratio to qualify as a doji (open ≈ close).",
            ),
            ParamSpec(
                "confirm_body_pct",
                "float",
                0.6,
                0.3,
                0.95,
                "Minimum body-to-range ratio of the confirmation candle.",
            ),
            ParamSpec(
                "sl_pct",
                "float",
                0.02,
                0.001,
                0.1,
                "Stop-loss distance as a fraction of entry price.",
            ),
            ParamSpec(
                "tp_r",
                "float",
                2.0,
                0.5,
                10.0,
                "Take-profit as a multiple of SL distance.",
            ),
        ],
        confidence={"15m": 2, "1d": 5, "1h": 3, "4h": 2},
    ),
    "morning_evening_star": StrategySpec(
        name="morning_evening_star",
        description="Morning Star (3-candle bullish reversal) / Evening Star (3-candle bearish reversal).",
        strategy_type="candlestick",
        params=[
            ParamSpec(
                "star_body_max",
                "float",
                0.3,
                0.05,
                0.5,
                "Maximum body-to-range ratio for the star (middle) candle.",
            ),
            ParamSpec(
                "sl_pct",
                "float",
                0.02,
                0.001,
                0.1,
                "Stop-loss distance as a fraction of entry price.",
            ),
            ParamSpec(
                "tp_r",
                "float",
                2.0,
                0.5,
                10.0,
                "Take-profit as a multiple of SL distance.",
            ),
        ],
        confidence={"15m": 2, "1d": 4, "1h": 2, "4h": 3},
    ),
    # Legacy — superseded by fib_golden_zone (adds BOS confirmation, better SL/TP structure).
    # Uncomment to re-enable for backtest comparison.
    # "fibonacci_retracement": StrategySpec(
    #     name="fibonacci_retracement",
    #     description="Fibonacci golden zone (0.5–0.618) retracement entry after a swing high/low.",
    #     params=[
    #         ParamSpec("swing_lookback", "int", 20, 5, 100,
    #                   "Number of bars to scan for the most recent swing high and swing low."),
    #         ParamSpec("sl_pct", "float", 0.02, 0.001, 0.1,
    #                   "Stop-loss distance as a fraction of entry price (fallback; actual SL is fib_0.786)."),
    #         ParamSpec("tp_r", "float", 2.0, 0.5, 10.0, "Take-profit as a multiple of SL distance."),
    #     ],
    #     confidence=3,
    # ),
    "fib_golden_zone": StrategySpec(
        name="fib_golden_zone",
        description="Fibonacci golden zone (0.5–0.618) entry after a confirmed BOS; TP = 1.618 extension.",
        strategy_type="fib",
        params=[
            ParamSpec(
                "swing_lookback",
                "int",
                20,
                5,
                100,
                "Number of bars to scan for the BOS swing high/low.",
            ),
            ParamSpec(
                "bos_lookback",
                "int",
                5,
                2,
                30,
                "Rolling window half-size for BOS swing detection.",
            ),
        ],
        confidence={"15m": 1, "1h": 4, "4h": 4},
    ),
    "ote_entry": StrategySpec(
        name="ote_entry",
        description="Optimal Trade Entry (OTE): 0.618–0.786 retracement after a confirmed BOS; TP = 1.618 extension.",
        strategy_type="fib",
        params=[
            ParamSpec(
                "swing_lookback",
                "int",
                20,
                5,
                100,
                "Number of bars to scan for the BOS swing high/low.",
            ),
            ParamSpec(
                "bos_lookback",
                "int",
                5,
                2,
                30,
                "Rolling window half-size for BOS swing detection.",
            ),
        ],
        confidence=1,  # 0 trades in backtest — multi-signal dedup inflates count, needs investigation
    ),
}

SIGNAL_COLUMNS: list[str] = [
    "open_time",
    "direction",
    "reason",
    "sl_price",
    "context",
    "low_volume",
    "tp_price",
]


_MYT = timezone(timedelta(hours=8))


def _fmt_time(ts_ms: int) -> str:
    """Format a Unix ms timestamp as a short MYT (UTC+8) string for alert context."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=_MYT).strftime("%d-%b %H:%M")


SEASONALITY_COLUMNS: list[str] = [
    "period_type",
    "period_value",
    "avg_return_pct",
    "win_rate",
    "count",
]

KNOWN_STRATEGIES: list[str] = list(STRATEGY_REGISTRY.keys())

# All valid strategy type labels — used for D10 confluence filtering and UI grouping.
KNOWN_STRATEGY_TYPES: list[str] = [
    "structural",
    "fib",
    "price_action",
    "candlestick",
    "flow",
    "session",
]

# Inverse index: type → list of strategy names (derived from STRATEGY_REGISTRY).
STRATEGY_TYPE_GROUPS: dict[str, list[str]] = {t: [] for t in KNOWN_STRATEGY_TYPES}
for _name, _spec in STRATEGY_REGISTRY.items():
    if _spec.strategy_type in STRATEGY_TYPE_GROUPS:
        STRATEGY_TYPE_GROUPS[_spec.strategy_type].append(_name)

# Strategy pairs that must not be combined in co-firing backtests because one
# embeds the other's detection logic — pairing them would double-count the same edge.
# fib_golden_zone and ote_entry both call _find_bos_swing() internally.
INCOMPATIBLE_PAIRS: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"fib_golden_zone", "bos"}),
        frozenset({"ote_entry", "bos"}),
    }
)


def patch_confidence_scores(updates: dict[str, dict[str, int] | int]) -> None:
    """Mutate STRATEGY_REGISTRY confidence values in-place.

    Only updates strategies that exist in the registry; unknown keys are silently skipped.
    Intended for use by the recalibration runner after computing new star ratings.
    """
    for name, stars in updates.items():
        if name in STRATEGY_REGISTRY:
            STRATEGY_REGISTRY[name].confidence = stars


def _empty_signals() -> pd.DataFrame:
    return pd.DataFrame(columns=SIGNAL_COLUMNS)


def _signals_to_df(signals: list[dict[str, object]]) -> pd.DataFrame:
    if not signals:
        return _empty_signals()
    df = pd.DataFrame(signals)
    # Ensure all expected columns exist; fill low_volume with False for detectors
    # that don't use a volume gate (keeps the column schema uniform).
    for col in SIGNAL_COLUMNS:
        if col not in df.columns:
            df[col] = False if col == "low_volume" else None
    return (
        df[SIGNAL_COLUMNS].drop_duplicates(subset=["open_time"]).reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# 1. Seasonality / Day-of-Week stats
# ---------------------------------------------------------------------------


def seasonality_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Compute average returns by day-of-week, hour-of-day, and week-of-month.

    df must have columns: open_time (Unix ms BIGINT), open (float), close (float).
    Returns a DataFrame with SEASONALITY_COLUMNS.
    """
    if df.empty or len(df) < 2:
        return pd.DataFrame(columns=SEASONALITY_COLUMNS)

    work = df[["open_time", "open", "close"]].copy()
    work["ts"] = pd.to_datetime(work["open_time"].astype("int64"), unit="ms", utc=True)
    work["return_pct"] = (work["close"] - work["open"]) / work["open"] * 100.0
    work["is_win"] = work["return_pct"] > 0.0

    rows: list[dict[str, object]] = []

    for dow, group in work.groupby(work["ts"].dt.dayofweek):
        rows.append(
            {
                "period_type": "day_of_week",
                "period_value": int(dow),
                "avg_return_pct": float(group["return_pct"].mean()),
                "win_rate": float(group["is_win"].mean()),
                "count": int(len(group)),
            }
        )

    for hour, group in work.groupby(work["ts"].dt.hour):
        rows.append(
            {
                "period_type": "hour_of_day",
                "period_value": int(hour),
                "avg_return_pct": float(group["return_pct"].mean()),
                "win_rate": float(group["is_win"].mean()),
                "count": int(len(group)),
            }
        )

    work["week_of_month"] = (work["ts"].dt.day - 1) // 7 + 1
    for wom, group in work.groupby(work["week_of_month"]):
        rows.append(
            {
                "period_type": "week_of_month",
                "period_value": int(wom),
                "avg_return_pct": float(group["return_pct"].mean()),
                "win_rate": float(group["is_win"].mean()),
                "count": int(len(group)),
            }
        )

    return pd.DataFrame(rows, columns=SEASONALITY_COLUMNS)


# ---------------------------------------------------------------------------
# 2. Wick Fill
# ---------------------------------------------------------------------------


def detect_wick_fills(
    df: pd.DataFrame,
    min_wick_body_ratio: float = 1.5,
    lookback: int = 20,
) -> pd.DataFrame:
    """Detect candles where price fills a prior significant wick zone.

    A significant wick must be at least min_wick_body_ratio × the candle body.
    Signals on the first candle within lookback that enters the wick zone.

    Long = fills a lower wick zone (bullish).
    Short = fills an upper wick zone (bearish).
    """
    n = len(df)
    if n < 2:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    for i in range(n - 1):
        row = df.iloc[i]
        body = abs(float(row["close"]) - float(row["open"]))
        if body == 0.0:
            continue

        candle_open = float(row["open"])
        candle_close = float(row["close"])
        candle_high = float(row["high"])
        candle_low = float(row["low"])

        upper_wick = candle_high - max(candle_open, candle_close)
        lower_wick = min(candle_open, candle_close) - candle_low

        end = min(i + lookback + 1, n)

        if lower_wick >= min_wick_body_ratio * body:
            zone_top = min(candle_open, candle_close)
            zone_bot = candle_low
            wick_ctx = f"Wick: {_fmt_time(int(row['open_time']))}"
            for j in range(i + 1, end):
                fut = df.iloc[j]
                if float(fut["low"]) <= zone_top and float(fut["close"]) > zone_bot:
                    signals.append(
                        {
                            "open_time": int(fut["open_time"]),
                            "direction": "long",
                            "reason": f"wick_fill_long@{zone_bot:.2f}-{zone_top:.2f}",
                            "sl_price": zone_bot,
                            "context": wick_ctx,
                        }
                    )
                    break

        if upper_wick >= min_wick_body_ratio * body:
            zone_bot = max(candle_open, candle_close)
            zone_top = candle_high
            wick_ctx = f"Wick: {_fmt_time(int(row['open_time']))}"
            for j in range(i + 1, end):
                fut = df.iloc[j]
                if float(fut["high"]) >= zone_bot and float(fut["close"]) < zone_top:
                    signals.append(
                        {
                            "open_time": int(fut["open_time"]),
                            "direction": "short",
                            "reason": f"wick_fill_short@{zone_bot:.2f}-{zone_top:.2f}",
                            "sl_price": zone_top,
                            "context": wick_ctx,
                        }
                    )
                    break

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 3. Wickless Candle (Marubozu) Retest
# ---------------------------------------------------------------------------


def detect_marubozu_retest(
    df: pd.DataFrame,
    max_wick_ratio: float = 0.1,
    lookback: int = 30,
    min_body_pct: float = 0.005,
) -> pd.DataFrame:
    """Detect retests of Marubozu (wickless) candle open prices.

    A Marubozu is a candle where both wicks are <= max_wick_ratio × body.
    The open of a bullish Marubozu acts as support (order block).
    The open of a bearish Marubozu acts as resistance (supply zone).

    Signal fires when a later candle retests the open price zone.

    min_body_pct: suppress Marubozus where body / open_price < min_body_pct.
    Default 0.005 (0.5%) filters out small indecisive candles.
    Note: SL is placed at the wick tip (very tight); this filter reduces
    stop-outs from low-volatility candles where noise exceeds SL distance.
    """
    n = len(df)
    if n < 2:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    for i in range(n - 1):
        row = df.iloc[i]
        row_open = float(row["open"])
        row_close = float(row["close"])
        row_high = float(row["high"])
        row_low = float(row["low"])

        body = abs(row_close - row_open)
        if body == 0.0:
            continue
        if body < min_body_pct * row_open:
            continue

        upper_wick = row_high - max(row_open, row_close)
        lower_wick = min(row_open, row_close) - row_low
        is_marubozu = (
            upper_wick <= max_wick_ratio * body and lower_wick <= max_wick_ratio * body
        )
        if not is_marubozu:
            continue

        is_bullish = row_close > row_open
        end = min(i + lookback + 1, n)

        maru_ctx = f"Marubozu: {_fmt_time(int(row['open_time']))}"
        if is_bullish:
            support = row_open
            for j in range(i + 1, end):
                fut = df.iloc[j]
                if float(fut["low"]) <= support and float(fut["close"]) > support:
                    signals.append(
                        {
                            "open_time": int(fut["open_time"]),
                            "direction": "long",
                            "reason": f"marubozu_long@{support:.2f}",
                            "sl_price": row_low,
                            "context": maru_ctx,
                        }
                    )
                    break
        else:
            resistance = row_open
            for j in range(i + 1, end):
                fut = df.iloc[j]
                if (
                    float(fut["high"]) >= resistance
                    and float(fut["close"]) < resistance
                ):
                    signals.append(
                        {
                            "open_time": int(fut["open_time"]),
                            "direction": "short",
                            "reason": f"marubozu_short@{resistance:.2f}",
                            "sl_price": row_high,
                            "context": maru_ctx,
                        }
                    )
                    break

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 4. Opening Range Breakout (ORB)
# ---------------------------------------------------------------------------


def detect_orb_breakout(
    df: pd.DataFrame,
    range_candles: int = 2,
    # Legacy param kept so existing callers that pass session_hour_utc= don't crash.
    # It is intentionally ignored — the new implementation anchors on 00:00 UTC.
    session_hour_utc: int = 0,
    timeframe_minutes: int = 0,
) -> pd.DataFrame:
    """Detect Opening Range Breakout (ORB) signals.

    For 24/7 crypto futures the session anchor is 00:00 UTC (daily open).
    The opening range is defined by the first ``range_candles`` candles of each
    UTC calendar day (default 2).  A breakout signal fires on any subsequent
    candle within the same day that *closes* outside the range:

    * close > range_high  →  LONG  (SL = range_low)
    * close < range_low   →  SHORT (SL = range_high)

    TP is placed at entry ± 1.5 × range_width (stored in ``context``).
    Only one signal per day per direction is emitted (per-day dedup).

    Parameters
    ----------
    df:
        OHLCV DataFrame with at least ``open_time``, ``high``, ``low``,
        ``close`` columns.  ``open_time`` must be Unix milliseconds UTC.
    range_candles:
        Number of candles from 00:00 UTC that form the opening range (1–4).
    session_hour_utc:
        Ignored (kept for backwards-compatibility with old callers).
    timeframe_minutes:
        Ignored (kept for backwards-compatibility with old callers).
    """
    n = len(df)
    if n < range_candles + 1:
        return _empty_signals()

    dt_utc = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
    dates = dt_utc.dt.date  # calendar date in UTC

    signals: list[dict[str, object]] = []
    # Track which (date, direction) pairs have already fired to avoid duplicates.
    fired: set[tuple[object, str]] = set()

    unique_dates = dates.unique()
    for day in unique_dates:
        day_mask = dates == day
        day_idx = df.index[day_mask].tolist()

        # Need at least range_candles + 1 candles on this day.
        if len(day_idx) < range_candles + 1:
            continue

        # Opening range = first range_candles candles of the day.
        range_rows = df.loc[day_idx[:range_candles]]
        range_high = float(range_rows["high"].max())
        range_low = float(range_rows["low"].min())
        range_width = range_high - range_low
        if range_width <= 0:
            continue

        range_open_ts = int(df.loc[day_idx[0]]["open_time"])
        range_ctx = (
            f"ORB range {_fmt_time(range_open_ts)} H:{range_high:.2f} L:{range_low:.2f}"
        )

        # Check every candle after the opening range window.
        for idx in day_idx[range_candles:]:
            row = df.loc[idx]
            close = float(row["close"])
            open_time_ms = int(row["open_time"])

            if close > range_high and (day, "long") not in fired:
                tp_price = close + range_width * 1.5
                signals.append(
                    {
                        "open_time": open_time_ms,
                        "direction": "long",
                        "reason": f"orb_long@{range_high:.2f}",
                        "sl_price": range_low,
                        "context": f"{range_ctx} TP:{tp_price:.2f}",
                    }
                )
                fired.add((day, "long"))

            elif close < range_low and (day, "short") not in fired:
                tp_price = close - range_width * 1.5
                signals.append(
                    {
                        "open_time": open_time_ms,
                        "direction": "short",
                        "reason": f"orb_short@{range_low:.2f}",
                        "sl_price": range_high,
                        "context": f"{range_ctx} TP:{tp_price:.2f}",
                    }
                )
                fired.add((day, "short"))

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 5. Liquidity Sweep + Reversal
# ---------------------------------------------------------------------------


def detect_liquidity_sweep(
    df: pd.DataFrame,
    lookback: int = 50,
    swing_n: int = 5,
    use_fib_extension: bool = True,
    require_close_rejection: bool = True,
    fib_require_range_close: bool = False,
) -> pd.DataFrame:
    """Detect liquidity sweep fakeout reversal signals.

    Two entry modes controlled by use_fib_extension:

    use_fib_extension=True (default — fib-extension mode):
        Price breaks above a genuine pivot swing high (fakeout), extends to the
        1.13 or 1.27 Fibonacci extension of the prior swing range, then closes
        back below that level — that is the reversal entry.

        Fib levels (measured from swing_high, outward from range):
            fib_1.13 = swing_high + 0.13 × (swing_high − swing_low)
            fib_1.27 = swing_high + 0.27 × (swing_high − swing_low)
        1.27 is checked first; 1.13 is the fallback.

    use_fib_extension=False (pivot-sweep mode):
        Entry fires when price wicks above the pivot swing high and closes back
        below it — no fib extension required. Useful as a baseline to compare
        against fib-mode via backtest.

    require_close_rejection (default True, applies to both modes):
        If True, candle must CLOSE below the trigger level (fib level in fib
        mode; swing high in pivot mode) — confirming a rejection candle.
        If False, a wick touch alone suffices. This choice is a named param so
        it can be toggled without touching the detection logic.

    fib_require_range_close (default False, applies to fib mode only):
        If True, the close must come back BELOW the original swing_high (fully
        inside the prior range), not just below the fib extension level. This
        is a stricter confirmation — the candle wicks into the fib zone but the
        body closes back inside the range. Ignored when use_fib_extension=False.

    Pivot detection: a candle is a swing high/low if its high/low is the
    extreme of the [k−swing_n, k+swing_n] centred window (default swing_n=5,
    i.e. 11-candle window). Anchors signals to structurally significant levels
    rather than arbitrary rolling extremes. Both modes use proper pivots.

    sl_price = the candle's wick high (for shorts) / wick low (for longs).
    """
    n = len(df)
    win = 2 * swing_n + 1
    if n < win + lookback:
        return _empty_signals()

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    # Precompute pivot highs/lows with a centred window (uses swing_n candles
    # on each side to confirm the pivot — acceptable lookahead for structural
    # levels; consistent with detect_eqh_eql).
    roll_max = (
        pd.Series(highs).rolling(win, center=True, min_periods=1).max().to_numpy()
    )
    roll_min = pd.Series(lows).rolling(win, center=True, min_periods=1).min().to_numpy()
    sh_idx: np.ndarray = np.where(highs >= roll_max)[0]
    sl_idx: np.ndarray = np.where(lows <= roll_min)[0]

    signals: list[dict[str, object]] = []

    for sig_i in range(lookback, n):
        ws = sig_i - lookback
        sig_h = highs[sig_i]
        sig_l = lows[sig_i]
        sig_c = closes[sig_i]
        sig_t = open_times[sig_i]

        # --- Short: fakeout above pivot swing high ---
        hi_sh = int(np.searchsorted(sh_idx, sig_i))
        lo_sh = int(np.searchsorted(sh_idx, ws))
        if hi_sh > lo_sh:
            pivot_sh_i = int(sh_idx[hi_sh - 1])
            swing_high = highs[pivot_sh_i]

            # Anchor: most recent pivot swing low before the swing high
            hi_sl = int(np.searchsorted(sl_idx, pivot_sh_i))
            lo_sl = int(np.searchsorted(sl_idx, ws))
            if hi_sl > lo_sl:
                swing_low = lows[int(sl_idx[hi_sl - 1])]
                rng = swing_high - swing_low

                if rng > 0:
                    fired = False
                    reason_s = ""
                    context_s = ""

                    if use_fib_extension:
                        fib_127 = swing_high + 0.27 * rng
                        fib_113 = swing_high + 0.13 * rng
                        fib_hit: float | None = None
                        fib_label: str | None = None
                        # close threshold: range boundary (stricter) or fib level
                        close_127 = swing_high if fib_require_range_close else fib_127
                        close_113 = swing_high if fib_require_range_close else fib_113
                        if sig_h >= fib_127 and (
                            not require_close_rejection or sig_c < close_127
                        ):
                            fib_hit, fib_label = fib_127, "1.27"
                        elif sig_h >= fib_113 and (
                            not require_close_rejection or sig_c < close_113
                        ):
                            fib_hit, fib_label = fib_113, "1.13"
                        if fib_hit is not None and fib_label is not None:
                            fired = True
                            reason_s = (
                                f"sweep_high@{swing_high:.2f}"
                                f"_fib{fib_label}@{fib_hit:.2f}"
                            )
                            context_s = (
                                f"range [{swing_low:.2f}–{swing_high:.2f}]"
                                f" · fib{fib_label}={fib_hit:.2f}"
                            )
                    else:
                        # Pivot-sweep mode: wick above swing_high, close inside
                        if sig_h > swing_high and (
                            not require_close_rejection or sig_c < swing_high
                        ):
                            fired = True
                            reason_s = f"sweep_high@{swing_high:.2f}"
                            context_s = f"range [{swing_low:.2f}–{swing_high:.2f}]"

                    if fired:
                        signals.append(
                            {
                                "open_time": sig_t,
                                "direction": "short",
                                "reason": reason_s,
                                "sl_price": sig_h,
                                "context": context_s,
                            }
                        )

        # --- Long: fakeout below pivot swing low ---
        hi_sl2 = int(np.searchsorted(sl_idx, sig_i))
        lo_sl2 = int(np.searchsorted(sl_idx, ws))
        if hi_sl2 > lo_sl2:
            pivot_sl_i = int(sl_idx[hi_sl2 - 1])
            swing_low2 = lows[pivot_sl_i]

            # Anchor: most recent pivot swing high before the swing low
            hi_sh2 = int(np.searchsorted(sh_idx, pivot_sl_i))
            lo_sh2 = int(np.searchsorted(sh_idx, ws))
            if hi_sh2 > lo_sh2:
                swing_high2 = highs[int(sh_idx[hi_sh2 - 1])]
                rng2 = swing_high2 - swing_low2

                if rng2 > 0:
                    fired_l = False
                    reason_l = ""
                    context_l = ""

                    if use_fib_extension:
                        fib_127_l = swing_low2 - 0.27 * rng2
                        fib_113_l = swing_low2 - 0.13 * rng2
                        fib_hit_l: float | None = None
                        fib_label_l: str | None = None
                        close_127_l = (
                            swing_low2 if fib_require_range_close else fib_127_l
                        )
                        close_113_l = (
                            swing_low2 if fib_require_range_close else fib_113_l
                        )
                        if sig_l <= fib_127_l and (
                            not require_close_rejection or sig_c > close_127_l
                        ):
                            fib_hit_l, fib_label_l = fib_127_l, "1.27"
                        elif sig_l <= fib_113_l and (
                            not require_close_rejection or sig_c > close_113_l
                        ):
                            fib_hit_l, fib_label_l = fib_113_l, "1.13"
                        if fib_hit_l is not None and fib_label_l is not None:
                            fired_l = True
                            reason_l = (
                                f"sweep_low@{swing_low2:.2f}"
                                f"_fib{fib_label_l}@{fib_hit_l:.2f}"
                            )
                            context_l = (
                                f"range [{swing_low2:.2f}–{swing_high2:.2f}]"
                                f" · fib{fib_label_l}={fib_hit_l:.2f}"
                            )
                    else:
                        # Pivot-sweep mode: wick below swing_low, close inside
                        if sig_l < swing_low2 and (
                            not require_close_rejection or sig_c > swing_low2
                        ):
                            fired_l = True
                            reason_l = f"sweep_low@{swing_low2:.2f}"
                            context_l = f"range [{swing_low2:.2f}–{swing_high2:.2f}]"

                    if fired_l:
                        signals.append(
                            {
                                "open_time": sig_t,
                                "direction": "long",
                                "reason": reason_l,
                                "sl_price": sig_l,
                                "context": context_l,
                            }
                        )

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 6. Fair Value Gap (FVG)
# ---------------------------------------------------------------------------


def detect_fvg(
    df: pd.DataFrame,
    lookback: int = 50,
    min_gap_pct: float = 0.001,
    trend_filter: int = 1,
) -> pd.DataFrame:
    """Detect Fair Value Gap (3-candle imbalance) fill signals.

    Bullish FVG: candle[i-1].high < candle[i+1].low — gap up imbalance.
    Bearish FVG: candle[i-1].low > candle[i+1].high — gap down imbalance.

    Signal fires on the first candle within lookback that enters the FVG zone.
    Long = price fills bullish FVG. Short = price fills bearish FVG.

    min_gap_pct: suppress FVGs whose size is < min_gap_pct * midpoint price.
    Default 0.001 (0.1%) filters out tiny imbalances that are noise.

    trend_filter: 1 (default) enables EMA-50 trend gate — long FVGs only fire
    when close > EMA-50; short FVGs only fire when close < EMA-50. Set to 0 to
    disable and allow signals regardless of trend direction.
    """
    n = len(df)
    if n < 3:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    ema50 = df["close"].ewm(span=50, adjust=False).mean()

    for i in range(1, n - 1):
        prev_high = float(df.iloc[i - 1]["high"])
        prev_low = float(df.iloc[i - 1]["low"])
        nxt_low = float(df.iloc[i + 1]["low"])
        nxt_high = float(df.iloc[i + 1]["high"])

        end = min(i + 2 + lookback, n)

        fvg_ctx = (
            f"Gap: {_fmt_time(int(df.iloc[i - 1]['open_time']))} · "
            f"{_fmt_time(int(df.iloc[i]['open_time']))} · "
            f"{_fmt_time(int(df.iloc[i + 1]['open_time']))}"
        )

        if prev_high < nxt_low:
            gap_bot = prev_high
            gap_top = nxt_low
            mid_price = (gap_bot + gap_top) / 2
            if (gap_top - gap_bot) < min_gap_pct * mid_price:
                continue
            ce = (gap_bot + gap_top) / 2
            for j in range(i + 2, end):
                fut = df.iloc[j]
                if float(fut["low"]) <= ce and float(fut["close"]) > gap_bot:
                    if trend_filter == 0 or float(fut["close"]) > float(ema50.iloc[j]):
                        signals.append(
                            {
                                "open_time": int(fut["open_time"]),
                                "direction": "long",
                                "reason": f"fvg_long@{gap_bot:.2f}-{gap_top:.2f}",
                                "sl_price": gap_bot,
                                "context": fvg_ctx,
                            }
                        )
                    break

        if prev_low > nxt_high:
            gap_top = prev_low
            gap_bot = nxt_high
            mid_price = (gap_bot + gap_top) / 2
            if (gap_top - gap_bot) < min_gap_pct * mid_price:
                continue
            ce = (gap_bot + gap_top) / 2
            for j in range(i + 2, end):
                fut = df.iloc[j]
                if float(fut["high"]) >= ce and float(fut["close"]) < gap_top:
                    if trend_filter == 0 or float(fut["close"]) < float(ema50.iloc[j]):
                        signals.append(
                            {
                                "open_time": int(fut["open_time"]),
                                "direction": "short",
                                "reason": f"fvg_short@{gap_top:.2f}-{gap_bot:.2f}",
                                "sl_price": gap_top,
                                "context": fvg_ctx,
                            }
                        )
                    break

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 7. Market Structure Break (BOS / CHoCH)
# ---------------------------------------------------------------------------


def detect_market_structure(
    df: pd.DataFrame,
    swing_lookback: int = 5,
    min_swing_pct: float = 0.005,
) -> pd.DataFrame:
    """Detect Break of Structure (BOS) and Change of Character (CHoCH).

    Swing highs and lows are identified using a rolling window of size
    2 × swing_lookback + 1.

    BOS long:   higher swing high in established uptrend.
    BOS short:  lower swing low in established downtrend.
    CHoCH long: first higher swing high after a downtrend (trend reversal).
    CHoCH short: first lower swing low after an uptrend (trend reversal).

    min_swing_pct: suppress signals where the structural level (swing_high -
    swing_low) / swing_high is smaller than this fraction.  Default 0.0
    keeps the original behaviour (no filter).
    """
    n = len(df)
    if n < swing_lookback * 3:
        return _empty_signals()

    high_series = df["high"].astype(float)
    low_series = df["low"].astype(float)

    window = 2 * swing_lookback + 1
    rolling_max = high_series.rolling(
        window=window, center=True, min_periods=window
    ).max()
    rolling_min = low_series.rolling(
        window=window, center=True, min_periods=window
    ).min()

    is_swing_high = high_series == rolling_max
    is_swing_low = low_series == rolling_min

    swings: list[tuple[int, float, str]] = []
    for i in range(n):
        if bool(is_swing_high.iloc[i]):
            swings.append((i, float(high_series.iloc[i]), "H"))
        if bool(is_swing_low.iloc[i]):
            swings.append((i, float(low_series.iloc[i]), "L"))
    swings.sort(key=lambda x: x[0])

    signals: list[dict[str, object]] = []
    last_sh: float | None = None
    last_sl: float | None = None
    trend: str = "unknown"

    for row_idx, price, typ in swings:
        open_time = int(df.iloc[row_idx]["open_time"])

        if typ == "H":
            if last_sh is not None and price > last_sh:
                sl_val = last_sl if last_sl is not None else 0.0
                swing_range = (price - sl_val) / price if price > 0 else 0.0
                if swing_range >= min_swing_pct:
                    label = "choch_long" if trend == "down" else "bos_long"
                    signals.append(
                        {
                            "open_time": open_time,
                            "direction": "long",
                            "reason": f"{label}@{price:.2f}",
                            "sl_price": sl_val,
                            "context": "",
                        }
                    )
                trend = "up"
            last_sh = price

        else:  # "L"
            if last_sl is not None and price < last_sl:
                sh_val = last_sh if last_sh is not None else 0.0
                swing_range = (sh_val - price) / price if price > 0 else 0.0
                if swing_range >= min_swing_pct:
                    label = "choch_short" if trend == "up" else "bos_short"
                    signals.append(
                        {
                            "open_time": open_time,
                            "direction": "short",
                            "reason": f"{label}@{price:.2f}",
                            "sl_price": sh_val,
                            "context": "",
                        }
                    )
                trend = "down"
            last_sl = price

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 8. Funding Rate Mean Reversion
# ---------------------------------------------------------------------------


def detect_funding_extreme(
    ohlcv_df: pd.DataFrame,
    funding_df: pd.DataFrame,
    threshold: float = 0.0001,
) -> pd.DataFrame:
    """Detect extreme funding rate conditions as contrarian signals.

    Extreme positive funding (rate > threshold) → short signal.
    Extreme negative funding (rate < -threshold) → long signal.

    Default threshold is 0.0001 (0.01%) — Binance Futures caps standard funding
    at 0.01% per 8h period; use this floor to catch any above-average extreme.

    funding_df must have columns: funding_time (Unix ms), funding_rate (float).
    Funding data is joined to OHLCV by the nearest prior funding_time.
    """
    if ohlcv_df.empty or funding_df.empty:
        return _empty_signals()

    ohlcv_sorted = ohlcv_df.sort_values("open_time").reset_index(drop=True)
    funding_sorted = funding_df.sort_values("funding_time").reset_index(drop=True)

    left = ohlcv_sorted[["open_time"]].rename(columns={"open_time": "ts"})

    # Keep funding_time as a separate column so we can deduplicate by it.
    right_with_ft = funding_sorted[["funding_time", "funding_rate"]].copy()
    right_with_ft["ts"] = right_with_ft["funding_time"]

    merged = pd.merge_asof(left, right_with_ft, on="ts", direction="backward")
    merged["open_time"] = ohlcv_sorted["open_time"].values
    merged["rate"] = pd.to_numeric(merged["funding_rate"], errors="coerce")

    # Only emit on the FIRST candle after each unique funding_time (one signal per period).
    valid = (
        merged[merged["rate"].notna()]
        .drop_duplicates(subset=["funding_time"])
        .reset_index(drop=True)
    )

    signals: list[dict[str, object]] = []

    for _, row in valid.iterrows():
        rate = float(row["rate"])
        open_time = int(row["open_time"])

        if rate > threshold:
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "short",
                    "reason": f"funding_long_extreme@{rate:.4f}",
                    "sl_price": 0.0,
                    "context": "",
                }
            )
        elif rate < -threshold:
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "long",
                    "reason": f"funding_short_extreme@{rate:.4f}",
                    "sl_price": 0.0,
                    "context": "",
                }
            )

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 9. SMT Divergence
# ---------------------------------------------------------------------------


def detect_smt_divergence(
    df_primary: pd.DataFrame,
    df_secondary: pd.DataFrame,
    lookback: int = 50,
    trend_filter: int = 1,
    swing_n: int = 5,
) -> pd.DataFrame:
    """Detect Smart Money Technique (SMT) divergence between two correlated assets.

    Bearish SMT: primary makes a confirmed new swing high but secondary does NOT →
    the primary's new high is a likely stop hunt → short signal.

    Bullish SMT: primary makes a confirmed new swing low but secondary does NOT →
    the primary's new low is a likely stop hunt → long signal.

    Swing highs/lows are identified using a centred window of 2×swing_n+1 candles
    (default swing_n=5 → 11-candle window). A candle is a swing high if its high
    equals the rolling max of the centred window. This is the same approach used by
    detect_eqh_eql() to find structurally significant pivot levels.

    A swing is "confirmed" only when swing_n candles have formed to its right.
    The signal fires at the first candle after confirmation (i >= pivot + swing_n).

    Signals are tagged on the primary asset's open_time.
    Both DataFrames must share open_time values (inner join used).

    When trend_filter=1 (default), signals are only taken with the trend:
    - LONG signals require close > EMA(50) on the primary asset.
    - SHORT signals require close < EMA(50) on the primary asset.
    """
    if df_primary.empty or df_secondary.empty:
        return _empty_signals()

    primary = df_primary.set_index("open_time")[["high", "low", "close"]].copy()
    primary.columns = pd.Index(["high_p", "low_p", "close_p"])
    secondary = df_secondary.set_index("open_time")[["high", "low"]].copy()
    secondary.columns = pd.Index(["high_s", "low_s"])

    merged = primary.join(secondary, how="inner")
    min_len = lookback + swing_n + 1
    if len(merged) < min_len:
        return _empty_signals()

    merged = merged.reset_index()
    n = len(merged)

    win = 2 * swing_n + 1

    # Precompute swing pivot indices using centred rolling window (same as detect_eqh_eql).
    highs_p = merged["high_p"].to_numpy(dtype=float)
    lows_p = merged["low_p"].to_numpy(dtype=float)
    highs_s = merged["high_s"].to_numpy(dtype=float)
    lows_s = merged["low_s"].to_numpy(dtype=float)
    open_times = merged["open_time"].to_numpy(dtype=int)
    closes_p = merged["close_p"].to_numpy(dtype=float)

    roll_max_p = (
        pd.Series(highs_p).rolling(win, center=True, min_periods=1).max().to_numpy()
    )
    roll_min_p = (
        pd.Series(lows_p).rolling(win, center=True, min_periods=1).min().to_numpy()
    )
    roll_max_s = (
        pd.Series(highs_s).rolling(win, center=True, min_periods=1).max().to_numpy()
    )
    roll_min_s = (
        pd.Series(lows_s).rolling(win, center=True, min_periods=1).min().to_numpy()
    )

    # Swing pivot candle indices — confirmed at candle k when k + swing_n has passed.
    sh_p_idx: np.ndarray = np.where(highs_p >= roll_max_p)[0]
    sl_p_idx: np.ndarray = np.where(lows_p <= roll_min_p)[0]
    sh_s_idx: np.ndarray = np.where(highs_s >= roll_max_s)[0]
    sl_s_idx: np.ndarray = np.where(lows_s <= roll_min_s)[0]

    ema50: np.ndarray | None = None
    if trend_filter:
        ema50 = merged["close_p"].ewm(span=50, adjust=False).mean().to_numpy()

    signals: list[dict[str, object]] = []

    for i in range(lookback, n):
        close_p = float(closes_p[i])
        ema_val = float(ema50[i]) if ema50 is not None else 0.0

        # Window start (inclusive) for confirmed pivots: confirmed means pivot_k + swing_n <= i
        # i.e. pivot_k <= i - swing_n.  Window also bounded by lookback from signal candle.
        win_start = i - lookback
        win_end_confirmed = i - swing_n  # pivot must be <= this to be confirmed

        if win_end_confirmed < win_start:
            continue

        # ---- Bearish SMT ------------------------------------------------
        # Find confirmed swing highs on primary within [win_start, win_end_confirmed].
        lo_p = int(np.searchsorted(sh_p_idx, win_start))
        hi_p = int(np.searchsorted(sh_p_idx, win_end_confirmed + 1))
        sh_p_window = sh_p_idx[lo_p:hi_p]

        if len(sh_p_window) >= 2:
            # Most recent swing high on primary
            latest_p_sh_idx = int(sh_p_window[-1])
            latest_p_sh_val = float(highs_p[latest_p_sh_idx])
            # Prior swing high on primary (any earlier one)
            prior_p_sh_val = float(highs_p[sh_p_window[:-1]].max())

            if latest_p_sh_val > prior_p_sh_val:
                # Primary made a new structural swing high.
                # Check secondary: find confirmed swing highs on secondary in same window.
                lo_s = int(np.searchsorted(sh_s_idx, win_start))
                hi_s = int(np.searchsorted(sh_s_idx, win_end_confirmed + 1))
                sh_s_window = sh_s_idx[lo_s:hi_s]

                secondary_also_new_high = False
                if len(sh_s_window) >= 2:
                    latest_s_sh_val = float(highs_s[sh_s_window[-1]])
                    prior_s_sh_val = float(highs_s[sh_s_window[:-1]].max())
                    secondary_also_new_high = latest_s_sh_val > prior_s_sh_val

                if (
                    i == latest_p_sh_idx + swing_n
                    and not secondary_also_new_high
                    and (not trend_filter or close_p < ema_val)
                ):
                    signals.append(
                        {
                            "open_time": int(open_times[i]),
                            "direction": "short",
                            "reason": f"smt_bearish@{latest_p_sh_val:.2f}",
                            "sl_price": latest_p_sh_val,
                            "context": "",
                        }
                    )

        # ---- Bullish SMT ------------------------------------------------
        lo_p2 = int(np.searchsorted(sl_p_idx, win_start))
        hi_p2 = int(np.searchsorted(sl_p_idx, win_end_confirmed + 1))
        sl_p_window = sl_p_idx[lo_p2:hi_p2]

        if len(sl_p_window) >= 2:
            latest_p_sl_idx = int(sl_p_window[-1])
            latest_p_sl_val = float(lows_p[latest_p_sl_idx])
            prior_p_sl_val = float(lows_p[sl_p_window[:-1]].min())

            if latest_p_sl_val < prior_p_sl_val:
                # Primary made a new structural swing low.
                lo_s2 = int(np.searchsorted(sl_s_idx, win_start))
                hi_s2 = int(np.searchsorted(sl_s_idx, win_end_confirmed + 1))
                sl_s_window = sl_s_idx[lo_s2:hi_s2]

                secondary_also_new_low = False
                if len(sl_s_window) >= 2:
                    latest_s_sl_val = float(lows_s[sl_s_window[-1]])
                    prior_s_sl_val = float(lows_s[sl_s_window[:-1]].min())
                    secondary_also_new_low = latest_s_sl_val < prior_s_sl_val

                if (
                    i == latest_p_sl_idx + swing_n
                    and not secondary_also_new_low
                    and (not trend_filter or close_p > ema_val)
                ):
                    signals.append(
                        {
                            "open_time": int(open_times[i]),
                            "direction": "long",
                            "reason": f"smt_bullish@{latest_p_sl_val:.2f}",
                            "sl_price": latest_p_sl_val,
                            "context": "",
                        }
                    )

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 10. Equal Highs / Equal Lows (EQH / EQL)
# ---------------------------------------------------------------------------


def detect_eqh_eql(
    df: pd.DataFrame,
    lookback: int = 50,
    tolerance_pct: float = 0.003,
    swing_n: int = 5,
) -> pd.DataFrame:
    """Detect Equal Highs / Equal Lows liquidity sweep signals.

    Equal Highs (EQH): two swing highs within tolerance_pct of each other form a
    liquidity pool. When a candle wicks above that level (high > EQH) but closes
    below it, a liquidity raid has occurred → short signal.

    Equal Lows (EQL): two swing lows within tolerance_pct form a pool below price.
    When a candle wicks below that level (low < EQL) but closes above it → long signal.

    Swing highs/lows are identified using a centred window of 2×swing_n+1 candles
    (default swing_n=5 → 11-candle window, i.e. 5 candles each side). A candle is a
    swing high if its high is the max of that window. Wider swing_n = fewer, more
    structurally significant pivot levels.

    Signals are generated across the full history (rolling window): each candle
    from index `lookback` onward is evaluated as a potential signal candle.

    Performance: swing highs/lows are precomputed globally using pandas rolling
    max/min (O(n)); per-candle work uses numpy searchsorted (O(log n)) to find
    swings in the window, avoiding per-iteration DataFrame creation.
    """
    n = len(df)
    if n < lookback + 1:
        return _empty_signals()

    swing_side = swing_n  # candles on each side of the pivot candidate
    win = 2 * swing_side + 1

    # Precompute arrays — no pandas operations inside the main loop.
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    # Rolling max/min with center=True + min_periods=1 matches the original
    # truncated-neighbourhood behaviour at window boundaries (confirmed below):
    # for any candle k with lookback candles on both sides, the centred rolling
    # window is fully within [k-swing_side, k+swing_side+1], identical to the
    # per-slice neighbourhood used in the original implementation.
    roll_max = (
        pd.Series(highs).rolling(win, center=True, min_periods=1).max().to_numpy()
    )
    roll_min = pd.Series(lows).rolling(win, center=True, min_periods=1).min().to_numpy()
    sh_idx: np.ndarray = np.where(highs >= roll_max)[0]
    sl_idx: np.ndarray = np.where(lows <= roll_min)[0]
    sh_prices = highs[sh_idx]
    sl_prices = lows[sl_idx]

    signals: list[dict[str, object]] = []

    for sig_i in range(lookback, n):
        ws = sig_i - lookback
        sig_h = highs[sig_i]
        sig_l = lows[sig_i]
        sig_c = closes[sig_i]
        sig_t = open_times[sig_i]

        # --- EQH: swing highs in [ws, sig_i) via binary search ---
        lo = int(np.searchsorted(sh_idx, ws))
        hi = int(np.searchsorted(sh_idx, sig_i))
        sw_h_idx = sh_idx[lo:hi]
        sw_h_pri = sh_prices[lo:hi]

        if len(sw_h_pri) >= 2:
            best_eqh: tuple[int, int, float, float] | None = None
            for a in range(len(sw_h_pri)):
                for b in range(a + 1, len(sw_h_pri)):
                    h1, h2 = sw_h_pri[a], sw_h_pri[b]
                    level = max(h1, h2)
                    if abs(h1 - h2) / level <= tolerance_pct:
                        # Reject if price already broke above the EQH level
                        # between the two pivots — the pool was already raided.
                        between = highs[sw_h_idx[a] + 1 : sw_h_idx[b]]
                        if len(between) > 0 and np.any(between > level):
                            continue
                        if sig_h <= level or sig_c >= level:
                            continue
                        if best_eqh is None or level > max(best_eqh[2], best_eqh[3]):
                            best_eqh = (
                                int(sw_h_idx[a]),
                                int(sw_h_idx[b]),
                                h1,
                                h2,
                            )

            if best_eqh is not None:
                ai, bi, h1, h2 = best_eqh
                eqh_level = max(h1, h2)
                later = max(ai, bi)
                post = highs[later : sig_i + 1]
                above = post[post > eqh_level]
                sl_price = float(above.max()) if len(above) > 0 else sig_h
                signals.append(
                    {
                        "open_time": sig_t,
                        "direction": "short",
                        "reason": f"eqh_short@{h1:.2f}-{h2:.2f}",
                        "sl_price": sl_price,
                        "context": (
                            f"EQH: {_fmt_time(open_times[ai])} @ {h1:,.2f}"
                            f" · {_fmt_time(open_times[bi])} @ {h2:,.2f}"
                        ),
                    }
                )

        # --- EQL: swing lows in [ws, sig_i) via binary search ---
        lo = int(np.searchsorted(sl_idx, ws))
        hi = int(np.searchsorted(sl_idx, sig_i))
        sw_l_idx = sl_idx[lo:hi]
        sw_l_pri = sl_prices[lo:hi]

        if len(sw_l_pri) >= 2:
            best_eql: tuple[int, int, float, float] | None = None
            for a in range(len(sw_l_pri)):
                for b in range(a + 1, len(sw_l_pri)):
                    l1, l2 = sw_l_pri[a], sw_l_pri[b]
                    level = min(l1, l2)
                    if level == 0.0:
                        continue
                    if abs(l1 - l2) / level <= tolerance_pct:
                        # Reject if price already broke below the EQL level
                        # between the two pivots — the pool was already raided.
                        between = lows[sw_l_idx[a] + 1 : sw_l_idx[b]]
                        if len(between) > 0 and np.any(between < level):
                            continue
                        if sig_l >= level or sig_c <= level:
                            continue
                        if best_eql is None or level < min(best_eql[2], best_eql[3]):
                            best_eql = (
                                int(sw_l_idx[a]),
                                int(sw_l_idx[b]),
                                l1,
                                l2,
                            )

            if best_eql is not None:
                ai, bi, l1, l2 = best_eql
                eql_level = min(l1, l2)
                later = max(ai, bi)
                post = lows[later : sig_i + 1]
                below = post[post < eql_level]
                sl_price = float(below.min()) if len(below) > 0 else sig_l
                signals.append(
                    {
                        "open_time": sig_t,
                        "direction": "long",
                        "reason": f"eql_long@{l1:.2f}-{l2:.2f}",
                        "sl_price": sl_price,
                        "context": (
                            f"EQL: {_fmt_time(open_times[ai])} @ {l1:,.2f}"
                            f" · {_fmt_time(open_times[bi])} @ {l2:,.2f}"
                        ),
                    }
                )

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 11. Order Block (ICT)
# ---------------------------------------------------------------------------


def detect_order_block(
    df: pd.DataFrame,
    lookback: int = 100,
    displacement_pct: float = 0.003,
) -> pd.DataFrame:
    """Detect ICT Order Block retest signals.

    Bearish OB: the last bullish candle (close > open) immediately before a
    significant bearish displacement candle (close < ob_low × (1 - displacement_pct)).
    Short signal fires on the first candle that retests the OB zone from below
    (candle enters [ob_open, ob_close]) after the displacement.
    SL = ob candle high.

    Bullish OB: the last bearish candle (open > close) immediately before a
    significant bullish displacement candle (close > ob_high × (1 + displacement_pct)).
    Long signal fires on the first candle that retests the OB zone from above
    (candle enters [ob_close, ob_open]) after the displacement.
    SL = ob candle low.

    All candles are scanned for OB formation (full history).
    `lookback` — max candles forward to search for a retest after the OB forms.
    This prevents stale OBs from generating signals months after formation.
    """
    n = len(df)
    if n < 3:
        return _empty_signals()

    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    signals: list[dict[str, object]] = []

    for i in range(n - 2):
        ob_open = opens[i]
        ob_high = highs[i]
        ob_low = lows[i]
        ob_close = closes[i]
        ob_time = open_times[i]
        disp_close = closes[i + 1]

        # --- Bearish OB: bullish candle followed by bearish displacement ---
        if ob_close > ob_open and disp_close < ob_low * (1 - displacement_pct):
            ob_zone_bot = ob_open
            ob_zone_top = ob_close
            ctx = f"Bearish OB: {_fmt_time(ob_time)} [{ob_zone_bot:,.2f}–{ob_zone_top:,.2f}]"
            retest_end = min(i + 2 + lookback, n)
            for j in range(i + 2, retest_end):
                # Retest: candle enters OB zone and closes below zone top
                if (
                    highs[j] >= ob_zone_bot
                    and lows[j] <= ob_zone_top
                    and closes[j] < ob_zone_top
                ):
                    signals.append(
                        {
                            "open_time": open_times[j],
                            "direction": "short",
                            "reason": f"ob_short@{ob_zone_bot:.2f}-{ob_zone_top:.2f}",
                            "sl_price": ob_high,
                            "context": ctx,
                        }
                    )
                    break  # one signal per OB

        # --- Bullish OB: bearish candle followed by bullish displacement ---
        elif ob_open > ob_close and disp_close > ob_high * (1 + displacement_pct):
            ob_zone_bot = ob_close
            ob_zone_top = ob_open
            ctx = f"Bullish OB: {_fmt_time(ob_time)} [{ob_zone_bot:,.2f}–{ob_zone_top:,.2f}]"
            retest_end = min(i + 2 + lookback, n)
            for j in range(i + 2, retest_end):
                # Retest: candle enters OB zone and closes above zone bot
                if (
                    lows[j] <= ob_zone_top
                    and highs[j] >= ob_zone_bot
                    and closes[j] > ob_zone_bot
                ):
                    signals.append(
                        {
                            "open_time": open_times[j],
                            "direction": "long",
                            "reason": f"ob_long@{ob_zone_bot:.2f}-{ob_zone_top:.2f}",
                            "sl_price": ob_low,
                            "context": ctx,
                        }
                    )
                    break  # one signal per OB

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 12. CVD Divergence
# ---------------------------------------------------------------------------


def detect_cvd_divergence(
    df: pd.DataFrame,
    lookback: int = 10,
    cvd_lookback: int = 50,
) -> pd.DataFrame:
    """Detect CVD divergence signals.

    Bearish: price higher swing high + CVD lower swing high → short.
    Bullish: price lower swing low + CVD higher swing low → long.

    CVD = cumsum(taker_buy_volume - taker_sell_volume)
        = cumsum(2 * taker_buy_volume - volume)

    taker_buy_volume NULLs are dropped gracefully.
    SL = structural swing extreme (high for short, low for long).

    Signals are generated across the full history (rolling window): each candle
    from index `cvd_lookback - 1` onward is evaluated. Each divergence pair fires
    exactly once (deduplicated by the 2nd swing peak's timestamp).

    Performance: global CVD and swing arrays are precomputed once (O(n)); the
    main loop uses numpy searchsorted (O(log n)) avoiding per-window DataFrame
    creation. CVD comparisons use global offsets — window-relative and global
    CVD orderings are identical (ch2 < ch1 ↔ CVD_global[i2] < CVD_global[i1]).
    """
    if "taker_buy_volume" not in df.columns or df["taker_buy_volume"].isna().all():
        return _empty_signals()
    df = df.dropna(subset=["taker_buy_volume"]).reset_index(drop=True)
    n = len(df)
    if n < lookback * 2 + 1:
        return _empty_signals()

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)
    tbv = df["taker_buy_volume"].to_numpy(dtype=float)
    vol = df["volume"].to_numpy(dtype=float)

    # Global CVD — window-relative orderings are preserved (offset cancels in
    # the ch2 < ch1 comparison, so global values can be used directly).
    cvd_global: np.ndarray = (2.0 * tbv - vol).cumsum()

    # Precompute confirmed swing highs/lows using rolling max/min.
    # A swing at absolute index k is confirmed iff it has `lookback` candles on
    # each side — the searchsorted step below restricts to [ws+lookback, end_i-lookback+1).
    win = 2 * lookback + 1
    roll_max = (
        pd.Series(highs).rolling(win, center=True, min_periods=1).max().to_numpy()
    )
    roll_min = pd.Series(lows).rolling(win, center=True, min_periods=1).min().to_numpy()
    sh_idx: np.ndarray = np.where(highs >= roll_max)[0]
    sl_idx: np.ndarray = np.where(lows <= roll_min)[0]

    signals: list[dict[str, object]] = []
    seen_pairs: set[tuple[int, str]] = set()

    for end_i in range(cvd_lookback - 1, n):
        ws = max(0, end_i - cvd_lookback + 1)
        sig_time = int(open_times[end_i])

        # Confirmed swing region: [ws+lookback, end_i-lookback+1)
        # This matches range(lookback, wn-lookback) in window coordinates.
        c_start = ws + lookback
        c_end = end_i - lookback + 1

        lo = int(np.searchsorted(sh_idx, c_start))
        hi = int(np.searchsorted(sh_idx, c_end))
        wsh = sh_idx[lo:hi]

        lo = int(np.searchsorted(sl_idx, c_start))
        hi = int(np.searchsorted(sl_idx, c_end))
        wsl = sl_idx[lo:hi]

        # Dedup consecutive plateaus (keep first of each run)
        def _dedup(arr: np.ndarray) -> list[int]:
            out: list[int] = []
            prev = -2
            for idx in arr:
                if idx > prev + 1:
                    out.append(int(idx))
                prev = idx
            return out

        sh_peaks = _dedup(wsh)
        sl_peaks = _dedup(wsl)

        if len(sh_peaks) >= 2:
            i1, i2 = sh_peaks[-2], sh_peaks[-1]
            peak2_time = int(open_times[i2])
            pair_key: tuple[int, str] = (peak2_time, "short")
            if pair_key not in seen_pairs:
                ph1, ph2 = highs[i1], highs[i2]
                ch1, ch2 = cvd_global[i1], cvd_global[i2]
                if ph2 > ph1 and ch2 < ch1:
                    seen_pairs.add(pair_key)
                    signals.append(
                        {
                            "open_time": sig_time,
                            "direction": "short",
                            "reason": f"cvd_div_bear@{ph2:.2f}",
                            "sl_price": ph2,
                            "context": (
                                f"CVD div: price H {ph1:.2f}→{ph2:.2f}, "
                                f"CVD {ch1:.0f}→{ch2:.0f} at {_fmt_time(sig_time)}"
                            ),
                        }
                    )

        if len(sl_peaks) >= 2:
            i1, i2 = sl_peaks[-2], sl_peaks[-1]
            peak2_time = int(open_times[i2])
            pair_key = (peak2_time, "long")
            if pair_key not in seen_pairs:
                pl1, pl2 = lows[i1], lows[i2]
                cl1, cl2 = cvd_global[i1], cvd_global[i2]
                if pl2 < pl1 and cl2 > cl1:
                    seen_pairs.add(pair_key)
                    signals.append(
                        {
                            "open_time": sig_time,
                            "direction": "long",
                            "reason": f"cvd_div_bull@{pl2:.2f}",
                            "sl_price": pl2,
                            "context": (
                                f"CVD div: price L {pl1:.2f}→{pl2:.2f}, "
                                f"CVD {cl1:.0f}→{cl2:.0f} at {_fmt_time(sig_time)}"
                            ),
                        }
                    )

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 13. Trend Day
# ---------------------------------------------------------------------------


def detect_trend_day(
    df: pd.DataFrame,
    body_pct_min: float = 0.65,
    wick_max: float = 0.15,
) -> pd.DataFrame:
    """Detect Trend Day candles — sessions that open near one extreme and close near the other.

    A Trend Day is characterised by:
    - A large body relative to the total candle range (body_pct >= body_pct_min).
    - A tiny leading wick (the wick on the direction-of-travel side).

    Bullish trend day: body_pct >= body_pct_min AND lower_wick_pct <= wick_max AND close > open.
    Bearish trend day: body_pct >= body_pct_min AND upper_wick_pct <= wick_max AND close < open.

    Candles with zero range (high == low) are skipped.

    body_pct_min: minimum abs(close - open) / (high - low) ratio (default 0.65 = 65%).
    wick_max: maximum leading wick / range ratio (default 0.15 = 15%).

    Signal open_time is the candle's own open_time (the event IS the candle).
    SL is placed at the candle's opposite extreme (low for bullish, high for bearish).
    """
    if df.empty:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    for i in range(len(df)):
        row = df.iloc[i]
        o = float(row["open"])
        h = float(row["high"])
        lo = float(row["low"])
        c = float(row["close"])

        candle_range = h - lo
        if candle_range == 0.0:
            continue

        body_pct = abs(c - o) / candle_range
        upper_wick_pct = (h - max(o, c)) / candle_range
        lower_wick_pct = (min(o, c) - lo) / candle_range

        open_time = int(row["open_time"])
        ctx = f"Trend Day: {_fmt_time(open_time)} body={body_pct:.0%}"

        if body_pct >= body_pct_min and lower_wick_pct <= wick_max and c > o:
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "long",
                    "reason": f"trend_day_bull@{o:.2f}-{c:.2f}",
                    "sl_price": lo,
                    "context": ctx,
                }
            )
        elif body_pct >= body_pct_min and upper_wick_pct <= wick_max and c < o:
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "short",
                    "reason": f"trend_day_bear@{o:.2f}-{c:.2f}",
                    "sl_price": h,
                    "context": ctx,
                }
            )

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 14. Bullish / Bearish Engulfing
# ---------------------------------------------------------------------------


def detect_engulfing(
    df: pd.DataFrame,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
) -> pd.DataFrame:
    """Detect Bullish and Bearish Engulfing 2-candle patterns.

    Bullish Engulfing: current bullish candle body fully engulfs the prior
    bearish candle body (current open < prior close AND current close > prior open).

    Bearish Engulfing: current bearish candle body fully engulfs the prior
    bullish candle body (current open > prior close AND current close < prior open).

    SL: entry_price * (1 - sl_pct) for long, * (1 + sl_pct) for short.
    TP: entry_price ± sl_distance * tp_r.
    Signal open_time is the engulfing candle's open_time.
    """
    n = len(df)
    if n < 2:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    opens = df["open"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    for i in range(1, n):
        prev_open = opens[i - 1]
        prev_close = closes[i - 1]
        curr_open = opens[i]
        curr_close = closes[i]
        open_time = open_times[i]

        prev_body_top = max(prev_open, prev_close)
        prev_body_bot = min(prev_open, prev_close)

        # Bullish engulfing: prev bearish, curr bullish, curr body engulfs prev body
        if (
            prev_close < prev_open  # prev bearish
            and curr_close > curr_open  # curr bullish
            and curr_open < prev_body_bot
            and curr_close > prev_body_top
        ):
            entry = curr_close
            sl = entry * (1 - sl_pct)
            sl_dist = entry - sl
            tp = entry + sl_dist * tp_r
            vol_ok = volume_confirm(df, i)
            ctx = f"TP={tp:.2f}"
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "long",
                    "reason": f"bullish_engulfing@{entry:.2f}",
                    "sl_price": sl,
                    "context": ctx,
                    "low_volume": not vol_ok,
                }
            )

        # Bearish engulfing: prev bullish, curr bearish, curr body engulfs prev body
        elif (
            prev_close > prev_open  # prev bullish
            and curr_close < curr_open  # curr bearish
            and curr_open > prev_body_top
            and curr_close < prev_body_bot
        ):
            entry = curr_close
            sl = entry * (1 + sl_pct)
            sl_dist = sl - entry
            tp = entry - sl_dist * tp_r
            vol_ok = volume_confirm(df, i)
            ctx = f"TP={tp:.2f}"
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "short",
                    "reason": f"bearish_engulfing@{entry:.2f}",
                    "sl_price": sl,
                    "context": ctx,
                    "low_volume": not vol_ok,
                }
            )

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 15. Pin Bar
# ---------------------------------------------------------------------------


def detect_pin_bar(
    df: pd.DataFrame,
    wick_ratio: float = 2.0,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
) -> pd.DataFrame:
    """Detect Pin Bar patterns (hammer / shooting star shape).

    Bullish Pin Bar (hammer shape): small body at the top of the range with
    a long lower wick ≥ wick_ratio × body.
    Bearish Pin Bar (shooting star shape): small body at the bottom of the
    range with a long upper wick ≥ wick_ratio × body.

    SL: entry_price * (1 ± sl_pct).
    """
    n = len(df)
    if n < 1:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    for i in range(n):
        row = df.iloc[i]
        o = float(row["open"])
        h = float(row["high"])
        lo = float(row["low"])
        c = float(row["close"])

        body = abs(c - o)
        if body == 0.0:
            continue

        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - lo
        open_time = int(row["open_time"])

        # Bullish pin bar: long lower wick, small upper wick (≤ body)
        if lower_wick >= wick_ratio * body and upper_wick <= body:
            entry = c
            sl = entry * (1 - sl_pct)
            sl_dist = entry - sl
            tp = entry + sl_dist * tp_r
            vol_ok = volume_confirm(df, i)
            ctx = f"TP={tp:.2f}"
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "long",
                    "reason": f"pin_bar_bull@{entry:.2f}",
                    "sl_price": sl,
                    "context": ctx,
                    "low_volume": not vol_ok,
                }
            )

        # Bearish pin bar: long upper wick, small lower wick (≤ body)
        elif upper_wick >= wick_ratio * body and lower_wick <= body:
            entry = c
            sl = entry * (1 + sl_pct)
            sl_dist = sl - entry
            tp = entry - sl_dist * tp_r
            vol_ok = volume_confirm(df, i)
            ctx = f"TP={tp:.2f}"
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "short",
                    "reason": f"pin_bar_bear@{entry:.2f}",
                    "sl_price": sl,
                    "context": ctx,
                    "low_volume": not vol_ok,
                }
            )

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 16. Inside Bar Breakout
# ---------------------------------------------------------------------------


def detect_inside_bar(
    df: pd.DataFrame,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
) -> pd.DataFrame:
    """Detect Inside Bar breakout patterns.

    An inside bar forms when the current candle's body is fully within the
    prior candle's body (high of inside ≤ high of mother, low of inside ≥ low
    of mother using body extremes, not wicks).

    Signal fires on the breakout candle (the candle AFTER the inside bar) that
    closes above/below the mother bar body:
    - Long: close > mother bar body top.
    - Short: close < mother bar body bottom.

    SL: entry_price * (1 ± sl_pct).
    """
    n = len(df)
    if n < 3:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    opens = df["open"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    for i in range(1, n - 1):
        mother_top = max(opens[i - 1], closes[i - 1])
        mother_bot = min(opens[i - 1], closes[i - 1])
        inside_top = max(opens[i], closes[i])
        inside_bot = min(opens[i], closes[i])

        # Check inside bar: body of candle i fully inside body of candle i-1
        if inside_top <= mother_top and inside_bot >= mother_bot:
            # Breakout candle is i+1
            breakout_close = closes[i + 1]
            open_time = open_times[i + 1]
            if breakout_close > mother_top:
                entry = breakout_close
                sl = entry * (1 - sl_pct)
                sl_dist = entry - sl
                tp = entry + sl_dist * tp_r
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "long",
                        "reason": f"inside_bar_long@{entry:.2f}",
                        "sl_price": sl,
                        "context": f"TP={tp:.2f}",
                    }
                )
            elif breakout_close < mother_bot:
                entry = breakout_close
                sl = entry * (1 + sl_pct)
                sl_dist = sl - entry
                tp = entry - sl_dist * tp_r
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "short",
                        "reason": f"inside_bar_short@{entry:.2f}",
                        "sl_price": sl,
                        "context": f"TP={tp:.2f}",
                    }
                )

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 17. Hammer / Hanging Man (context-aware pin bar)
# ---------------------------------------------------------------------------


def detect_hammer_hanging_man(
    df: pd.DataFrame,
    wick_ratio: float = 2.0,
    context_lookback: int = 10,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
) -> pd.DataFrame:
    """Detect Hammer (bullish reversal) and Hanging Man (bearish reversal).

    Same shape as a bullish pin bar (small body, long lower wick ≥ wick_ratio × body),
    but context-aware:
    - Hammer: shape appears after a downtrend (close[i] < close[i - context_lookback]).
    - Hanging Man: same shape appears after an uptrend (close[i] > close[i - context_lookback]).

    SL: entry_price * (1 ± sl_pct).
    """
    n = len(df)
    if n < context_lookback + 1:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    for i in range(context_lookback, n):
        row = df.iloc[i]
        o = float(row["open"])
        h = float(row["high"])
        lo = float(row["low"])
        c = float(row["close"])

        body = abs(c - o)
        if body == 0.0:
            continue

        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - lo

        # Must have long lower wick and short upper wick (upper wick ≤ body)
        if lower_wick < wick_ratio * body or upper_wick > body:
            continue

        open_time = int(row["open_time"])
        prior_close = float(df.iloc[i - context_lookback]["close"])
        vol_ok = volume_confirm(df, i)

        if c < prior_close:
            # Downtrend context → Hammer (bullish)
            entry = c
            sl = entry * (1 - sl_pct)
            sl_dist = entry - sl
            tp = entry + sl_dist * tp_r
            ctx = f"TP={tp:.2f}"
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "long",
                    "reason": f"hammer@{entry:.2f}",
                    "sl_price": sl,
                    "context": ctx,
                    "low_volume": not vol_ok,
                }
            )
        else:
            # Uptrend context → Hanging Man (bearish)
            entry = c
            sl = entry * (1 + sl_pct)
            sl_dist = sl - entry
            tp = entry - sl_dist * tp_r
            ctx = f"TP={tp:.2f}"
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "short",
                    "reason": f"hanging_man@{entry:.2f}",
                    "sl_price": sl,
                    "context": ctx,
                    "low_volume": not vol_ok,
                }
            )

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 18. Doji + Confirmation
# ---------------------------------------------------------------------------


def detect_doji(
    df: pd.DataFrame,
    body_threshold: float = 0.1,
    confirm_body_pct: float = 0.6,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
) -> pd.DataFrame:
    """Detect Doji + directional confirmation signals.

    A Doji is a candle where open ≈ close (body ≤ body_threshold × range).
    Signal fires when the NEXT candle is strongly directional
    (body ≥ confirm_body_pct × range).

    Long: confirmation candle is bullish (close > open).
    Short: confirmation candle is bearish (close < open).

    SL: entry_price * (1 ± sl_pct).
    """
    n = len(df)
    if n < 2:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    for i in range(n - 1):
        row = df.iloc[i]
        o = float(row["open"])
        h = float(row["high"])
        lo = float(row["low"])
        c = float(row["close"])

        candle_range = h - lo
        if candle_range == 0.0:
            continue

        body = abs(c - o)
        if body > body_threshold * candle_range:
            continue

        # Check confirmation candle
        nxt = df.iloc[i + 1]
        nxt_o = float(nxt["open"])
        nxt_h = float(nxt["high"])
        nxt_lo = float(nxt["low"])
        nxt_c = float(nxt["close"])
        nxt_range = nxt_h - nxt_lo
        if nxt_range == 0.0:
            continue

        nxt_body = abs(nxt_c - nxt_o)
        if nxt_body < confirm_body_pct * nxt_range:
            continue

        open_time = int(nxt["open_time"])

        if nxt_c > nxt_o:
            entry = nxt_c
            sl = entry * (1 - sl_pct)
            sl_dist = entry - sl
            tp = entry + sl_dist * tp_r
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "long",
                    "reason": f"doji_bull@{entry:.2f}",
                    "sl_price": sl,
                    "context": f"TP={tp:.2f}",
                }
            )
        else:
            entry = nxt_c
            sl = entry * (1 + sl_pct)
            sl_dist = sl - entry
            tp = entry - sl_dist * tp_r
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "short",
                    "reason": f"doji_bear@{entry:.2f}",
                    "sl_price": sl,
                    "context": f"TP={tp:.2f}",
                }
            )

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 19. Morning Star / Evening Star (3-candle reversal)
# ---------------------------------------------------------------------------


def detect_morning_evening_star(
    df: pd.DataFrame,
    star_body_max: float = 0.3,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
) -> pd.DataFrame:
    """Detect Morning Star (3-candle bullish) and Evening Star (3-candle bearish) patterns.

    Morning Star:
    - Candle[i-2]: large bearish candle (close < open).
    - Candle[i-1]: small-body star candle (body ≤ star_body_max × range), gaps lower.
    - Candle[i]:   large bullish candle (close > open) closing above midpoint of candle[i-2] body.

    Evening Star (mirror):
    - Candle[i-2]: large bullish candle.
    - Candle[i-1]: small-body star.
    - Candle[i]:   large bearish candle closing below midpoint of candle[i-2] body.

    SL: entry_price * (1 ± sl_pct).
    """
    n = len(df)
    if n < 3:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    for i in range(2, n):
        a_o, _, _, a_c = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        s_o, s_h, s_l, s_c = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        b_o, _, _, b_c = opens[i], highs[i], lows[i], closes[i]

        star_range = s_h - s_l
        if star_range == 0.0:
            continue
        star_body = abs(s_c - s_o)

        # Star candle must have small body
        if star_body > star_body_max * star_range:
            continue

        # Morning Star: A bearish, B bullish, B closes above midpoint of A
        if a_c < a_o and b_c > b_o:
            a_mid = (a_o + a_c) / 2
            if b_c > a_mid:
                open_time = open_times[i]
                entry = b_c
                sl = entry * (1 - sl_pct)
                sl_dist = entry - sl
                tp = entry + sl_dist * tp_r
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "long",
                        "reason": f"morning_star@{entry:.2f}",
                        "sl_price": sl,
                        "context": f"TP={tp:.2f}",
                    }
                )

        # Evening Star: A bullish, B bearish, B closes below midpoint of A
        elif a_c > a_o and b_c < b_o:
            a_mid = (a_o + a_c) / 2
            if b_c < a_mid:
                open_time = open_times[i]
                entry = b_c
                sl = entry * (1 + sl_pct)
                sl_dist = sl - entry
                tp = entry - sl_dist * tp_r
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "short",
                        "reason": f"evening_star@{entry:.2f}",
                        "sl_price": sl,
                        "context": f"TP={tp:.2f}",
                    }
                )

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# Volume confirmation helper (D5)
# ---------------------------------------------------------------------------


def volume_confirm(
    df: pd.DataFrame,
    idx: int,
    multiplier: float = 1.5,
    lookback: int = 20,
) -> bool:
    """Return True if the candle at `idx` has volume >= multiplier × rolling mean.

    Uses the `lookback` candles *before* idx (no lookahead) to compute the
    rolling average.  Returns True when volume data is unavailable (safe default).
    """
    if "volume" not in df.columns:
        return True
    if idx < 1:
        return True
    start = max(0, idx - lookback)
    prior_vols = df["volume"].iloc[start:idx].astype(float)
    if prior_vols.empty:
        return True
    avg = float(prior_vols.mean())
    if avg == 0.0:
        return True
    return float(df["volume"].iloc[idx]) >= multiplier * avg


# ---------------------------------------------------------------------------
# 20. Fibonacci Retracement (Golden Zone)
# ---------------------------------------------------------------------------


def detect_fibonacci_retracement(
    df: pd.DataFrame,
    swing_lookback: int = 20,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
) -> pd.DataFrame:
    """Detect Fibonacci golden zone (0.5–0.618) retracement signals.

    Swing detection: scan the last `swing_lookback` bars (excluding the signal
    candle) for the most recent swing high and swing low using a 3-bar pivot
    (bar[i] is a pivot if it is strictly greater/less than bar[i-1] and bar[i+1]).

    LONG signal (bullish swing established — swing_low before swing_high):
    - Price retraces into the golden zone: fib_0.618 ≤ close ≤ fib_0.5.
    - SL: fib_0.786 price level.
    - TP: swing_high.

    SHORT signal (bearish swing — swing_high before swing_low):
    - Price retraces up into the golden zone from below: fib_0.5 ≤ close ≤ fib_0.618.
    - SL: fib_0.786 above swing_high.
    - TP: swing_low.

    reason: e.g. "fib_golden_zone@70000.00 (0.618=69500.00)"
    """
    n = len(df)
    if n < swing_lookback + 2:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    for sig_i in range(swing_lookback + 1, n):
        # Scan window: indices [sig_i - swing_lookback, sig_i - 1] (no lookahead)
        win_start = sig_i - swing_lookback
        win_end = sig_i - 1  # inclusive

        # Find swing highs and lows in the window using 3-bar pivots
        # (need at least 1 bar on each side, so scan [win_start+1, win_end-1])
        swing_highs: list[tuple[int, float]] = []  # (idx_in_df, price)
        swing_lows: list[tuple[int, float]] = []
        for k in range(win_start + 1, win_end):
            if highs[k] > highs[k - 1] and highs[k] > highs[k + 1]:
                swing_highs.append((k, highs[k]))
            if lows[k] < lows[k - 1] and lows[k] < lows[k + 1]:
                swing_lows.append((k, lows[k]))

        if not swing_highs or not swing_lows:
            continue

        # Most recent swing high and low
        sh_idx, sh_price = swing_highs[-1]
        sl_idx, sl_price = swing_lows[-1]

        swing_range = sh_price - sl_price
        if swing_range <= 0.0:
            continue

        # Fibonacci levels: measured as retracement from swing_high down (standard convention).
        # 0% = swing_high, 100% = swing_low.
        # fib_0_5   = sh_price - 0.5 * swing_range   (50% retracement)
        # fib_0_618 = sh_price - 0.618 * swing_range  (61.8% retracement — golden zone bottom)
        # fib_0_786 = sh_price - 0.786 * swing_range  (78.6% retracement — SL invalidation)
        fib_0_5 = sh_price - 0.5 * swing_range
        fib_0_618 = sh_price - 0.618 * swing_range
        fib_0_786 = sh_price - 0.786 * swing_range

        curr_close = closes[sig_i]
        open_time = open_times[sig_i]

        # LONG: swing_low established before swing_high (upswing), price now retracing.
        # Enter long when price retraces into the golden zone (50%–61.8% retracement).
        # SL: fib_0.786 — if price retraces 78.6%+ the up-move is invalidated.
        if sl_idx < sh_idx:
            if fib_0_5 >= curr_close >= fib_0_618:
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "long",
                        "reason": f"fib_golden_zone@{curr_close:.2f} (0.618={fib_0_618:.2f})",
                        "sl_price": fib_0_786,
                        "context": (
                            f"Fib: swing_low={sl_price:.2f} swing_high={sh_price:.2f} "
                            f"TP={sh_price:.2f}"
                        ),
                    }
                )

        # SHORT: swing_high established before swing_low (downswing), price bouncing up.
        # Enter short when price retraces UP into the golden zone (50%–61.8% of the down-move).
        # Fib levels here measured from swing_low upward.
        # SL: fib_0.786 measured from swing_low upward (price recovers too much → invalidated).
        elif sh_idx < sl_idx:
            short_fib_0_5 = sl_price + 0.5 * swing_range
            short_fib_0_618 = sl_price + 0.618 * swing_range
            short_fib_0_786 = sl_price + 0.786 * swing_range

            if short_fib_0_618 >= curr_close >= short_fib_0_5:
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "short",
                        "reason": f"fib_golden_zone@{curr_close:.2f} (0.618={short_fib_0_618:.2f})",
                        "sl_price": short_fib_0_786,
                        "context": (
                            f"Fib: swing_high={sh_price:.2f} swing_low={sl_price:.2f} "
                            f"TP={sl_price:.2f}"
                        ),
                    }
                )

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 21. Fibonacci Golden Zone Entry after BOS (D3)
# ---------------------------------------------------------------------------


def _find_bos_swing(
    df: pd.DataFrame,
    swing_lookback: int,
    bos_lookback: int,
) -> tuple[float, float, str] | None:
    """Find the most recent BOS and return (swing_low, swing_high, direction).

    Two-zone approach (no-lookahead):

    - Structural zone: [win_start, bos_start) — find the anchor swing high/low.
      Uses absolute max/min to identify the dominant structural level.
    - BOS zone: [bos_start, n-1) — check whether price broke the structural level.
      The signal candle (n-1) is never included.

    Bullish BOS:
    1. Structural zone: lowest low = swing_low, highest high after swing_low = swing_high.
    2. BOS zone: any bar has close or high > swing_high → bullish BOS confirmed.

    Bearish BOS (symmetric):
    1. Structural zone: highest high = swing_high, lowest low after swing_high = swing_low.
    2. BOS zone: any bar has close or low < swing_low → bearish BOS confirmed.

    Returns None if no clear BOS is found.
    direction: 'long' (bullish BOS) | 'short' (bearish BOS).
    """
    n = len(df)
    # Need structural zone + BOS zone + signal candle
    if n < swing_lookback + bos_lookback + 1:
        return None

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)

    # BOS zone: last bos_lookback bars before the signal candle
    bos_start = n - bos_lookback - 1  # inclusive
    # Structural zone: swing_lookback bars before BOS zone
    struct_start = max(0, bos_start - swing_lookback)
    struct_end = bos_start  # exclusive

    if struct_end - struct_start < 2:
        return None

    # --- Bullish BOS ---
    # Structural swing_low = min low in structural zone
    sl_local = int(lows[struct_start:struct_end].argmin())
    sl_idx = struct_start + sl_local
    sl_price = float(lows[sl_idx])
    # Structural swing_high = max high from sl_idx forward (within structural zone)
    post_sl_end = struct_end
    if sl_idx + 1 < post_sl_end:
        sh_local = int(highs[sl_idx:post_sl_end].argmax())
        sh_idx = sl_idx + sh_local
        sh_price = float(highs[sh_idx])
        if sh_price > sl_price and sh_idx > sl_idx:
            # Check BOS zone for break above sh_price
            for conf_i in range(bos_start, n - 1):
                if closes[conf_i] > sh_price or highs[conf_i] > sh_price:
                    return (sl_price, sh_price, "long")

    # --- Bearish BOS ---
    sh_local2 = int(highs[struct_start:struct_end].argmax())
    sh_idx2 = struct_start + sh_local2
    sh_price2 = float(highs[sh_idx2])
    if sh_idx2 + 1 < struct_end:
        sl_local2 = int(lows[sh_idx2:struct_end].argmin())
        sl_idx2 = sh_idx2 + sl_local2
        sl_price2 = float(lows[sl_idx2])
        if sh_price2 > sl_price2 and sl_idx2 > sh_idx2:
            for conf_i in range(bos_start, n - 1):
                if closes[conf_i] < sl_price2 or lows[conf_i] < sl_price2:
                    return (sl_price2, sh_price2, "short")

    return None


def detect_fib_golden_zone(
    df: pd.DataFrame,
    swing_lookback: int = 20,
    bos_lookback: int = 5,
) -> pd.DataFrame:
    """Detect Fibonacci golden zone (0.5–0.618) entry after a confirmed BOS.

    Algorithm:
    1. Detect the most recent BOS within the last `swing_lookback` bars.
    2. Compute Fibonacci retracement levels from the BOS swing.
    3. Signal fires when the current candle close is inside the 0.5–0.618 band.

    LONG (bullish BOS — swing_low → swing_high → BOS above swing_high):
    - Entry zone: fib 0.618 ≤ close ≤ fib 0.5 (retracing down into golden zone).
    - SL: below the swing_low that defined the BOS leg.
    - TP: 1.618 extension above the swing_high.

    SHORT (bearish BOS — swing_high → swing_low → BOS below swing_low):
    - Entry zone: fib 0.5 ≤ close ≤ fib 0.618 (bouncing up into golden zone).
    - SL: above the swing_high that defined the BOS leg.
    - TP: 1.618 extension below the swing_low.
    """
    n = len(df)
    if n < swing_lookback + 3:
        return _empty_signals()

    signals: list[dict[str, object]] = []
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    # Only evaluate the last candle (real-time use case: does the new bar enter the zone?)
    for sig_i in range(swing_lookback + 2, n):
        bos = _find_bos_swing(df.iloc[: sig_i + 1], swing_lookback, bos_lookback)
        if bos is None:
            continue

        sl_price_bos, sh_price_bos, direction = bos
        swing_range = sh_price_bos - sl_price_bos
        if swing_range <= 0.0:
            continue

        curr_close = closes[sig_i]
        open_time = open_times[sig_i]

        if direction == "long":
            # Retracement from sh_price_bos downward
            fib_0_5 = sh_price_bos - 0.5 * swing_range
            fib_0_618 = sh_price_bos - 0.618 * swing_range
            # SL: below the swing_low (the anchor of the bullish leg)
            sl_out = sl_price_bos
            # TP: 1.618 extension above swing_high
            tp = sh_price_bos + 0.618 * swing_range
            if fib_0_5 >= curr_close >= fib_0_618:
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "long",
                        "reason": (
                            f"fib_golden_zone_bos@{curr_close:.2f} "
                            f"(0.618={fib_0_618:.2f})"
                        ),
                        "sl_price": sl_out,
                        "tp_price": tp,
                        "context": (
                            f"BOS: swing_low={sl_price_bos:.2f} "
                            f"swing_high={sh_price_bos:.2f} "
                            f"TP={tp:.2f} (1.618 ext)"
                        ),
                    }
                )

        else:  # short
            # Retracement from sl_price_bos upward
            fib_0_5 = sl_price_bos + 0.5 * swing_range
            fib_0_618 = sl_price_bos + 0.618 * swing_range
            # SL: above the swing_high
            sl_out = sh_price_bos
            # TP: 1.618 extension below swing_low
            tp = sl_price_bos - 0.618 * swing_range
            if fib_0_618 >= curr_close >= fib_0_5:
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "short",
                        "reason": (
                            f"fib_golden_zone_bos@{curr_close:.2f} "
                            f"(0.618={fib_0_618:.2f})"
                        ),
                        "sl_price": sl_out,
                        "tp_price": tp,
                        "context": (
                            f"BOS: swing_high={sh_price_bos:.2f} "
                            f"swing_low={sl_price_bos:.2f} "
                            f"TP={tp:.2f} (1.618 ext)"
                        ),
                    }
                )

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 22. OTE Entry (0.618–0.786 retracement after BOS) (D4)
# ---------------------------------------------------------------------------


def detect_ote_entry(
    df: pd.DataFrame,
    swing_lookback: int = 20,
    bos_lookback: int = 5,
) -> pd.DataFrame:
    """Detect OTE (Optimal Trade Entry) — 0.618–0.786 retracement after a confirmed BOS.

    Same structure as detect_fib_golden_zone but uses the deeper OTE zone
    (61.8%–78.6% retracement).  This is more selective and targets the
    high-probability ICT OTE level.

    LONG (bullish BOS):
    - Entry zone: fib 0.786 ≤ close ≤ fib 0.618.
    - SL: below the swing_low.
    - TP: 1.618 extension above swing_high.

    SHORT (bearish BOS):
    - Entry zone: fib 0.618 ≤ close ≤ fib 0.786 (measured from swing_low upward).
    - SL: above the swing_high.
    - TP: 1.618 extension below swing_low.
    """
    n = len(df)
    if n < swing_lookback + bos_lookback + 2:
        return _empty_signals()

    signals: list[dict[str, object]] = []
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    for sig_i in range(swing_lookback + bos_lookback + 1, n):
        bos = _find_bos_swing(df.iloc[: sig_i + 1], swing_lookback, bos_lookback)
        if bos is None:
            continue

        sl_price_bos, sh_price_bos, direction = bos
        swing_range = sh_price_bos - sl_price_bos
        if swing_range <= 0.0:
            continue

        curr_close = closes[sig_i]
        open_time = open_times[sig_i]

        if direction == "long":
            fib_0_618 = sh_price_bos - 0.618 * swing_range
            fib_0_786 = sh_price_bos - 0.786 * swing_range
            sl_out = sl_price_bos
            tp = sh_price_bos + 0.618 * swing_range
            if fib_0_618 >= curr_close >= fib_0_786:
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "long",
                        "reason": (
                            f"ote_long@{curr_close:.2f} (0.786={fib_0_786:.2f})"
                        ),
                        "sl_price": sl_out,
                        "tp_price": tp,
                        "context": (
                            f"OTE: swing_low={sl_price_bos:.2f} "
                            f"swing_high={sh_price_bos:.2f} "
                            f"TP={tp:.2f} (1.618 ext)"
                        ),
                    }
                )

        else:  # short
            fib_0_618 = sl_price_bos + 0.618 * swing_range
            fib_0_786 = sl_price_bos + 0.786 * swing_range
            sl_out = sh_price_bos
            tp = sl_price_bos - 0.618 * swing_range
            if fib_0_786 >= curr_close >= fib_0_618:
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "short",
                        "reason": (
                            f"ote_short@{curr_close:.2f} (0.786={fib_0_786:.2f})"
                        ),
                        "sl_price": sl_out,
                        "tp_price": tp,
                        "context": (
                            f"OTE: swing_high={sh_price_bos:.2f} "
                            f"swing_low={sl_price_bos:.2f} "
                            f"TP={tp:.2f} (1.618 ext)"
                        ),
                    }
                )

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# DETECTOR_REGISTRY — single source of truth for simple (OHLCV-only) detectors
# ---------------------------------------------------------------------------
# Strategies that require extra data (smt_divergence → secondary OHLCV) are
# NOT listed here; callers handle those explicitly.  seasonality is also
# excluded (returns stats, not signals).

DETECTOR_REGISTRY: dict[str, Callable[[pd.DataFrame], pd.DataFrame]] = {
    "wick_fill": detect_wick_fills,
    "marubozu": detect_marubozu_retest,
    "orb": detect_orb_breakout,
    "liquidity_sweep": detect_liquidity_sweep,
    "fvg": detect_fvg,
    "bos": detect_market_structure,
    "eqh_eql": detect_eqh_eql,
    "order_block": detect_order_block,
    "cvd_divergence": detect_cvd_divergence,
    "trend_day": detect_trend_day,
    "engulfing": detect_engulfing,
    "pin_bar": detect_pin_bar,
    "inside_bar": detect_inside_bar,
    "hammer_hanging_man": detect_hammer_hanging_man,
    "doji": detect_doji,
    "morning_evening_star": detect_morning_evening_star,
    # "fibonacci_retracement": detect_fibonacci_retracement,  # Legacy — see STRATEGY_REGISTRY comment above
    "fib_golden_zone": detect_fib_golden_zone,
    "ote_entry": detect_ote_entry,
}

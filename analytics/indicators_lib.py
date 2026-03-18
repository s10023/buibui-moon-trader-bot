"""Pure strategy signal detection functions for analytics.

All functions accept pandas DataFrames of OHLCV data and return a DataFrame
of detected signals with columns: open_time (int), direction (str), reason (str),
sl_price (float), context (str).

Seasonality returns a summary statistics DataFrame instead.
No module-level side effects.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

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
    confidence: int = (
        3  # 1–5 editorial quality score; shown as stars in Telegram alerts
    )


STRATEGY_REGISTRY: dict[str, StrategySpec] = {
    "seasonality": StrategySpec(
        name="seasonality",
        description="Day-of-week, hour-of-day, and week-of-month return statistics.",
        confidence=2,
    ),
    "wick_fill": StrategySpec(
        name="wick_fill",
        description="Signals when price re-enters a prior candle's significant wick zone.",
        params=[
            ParamSpec(
                "min_wick_body_ratio",
                "float",
                0.5,
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
        confidence=2,
    ),
    "marubozu": StrategySpec(
        name="marubozu",
        description="Signals on retests of Marubozu (wickless) candle open prices.",
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
        ],
        confidence=2,
    ),
    "orb": StrategySpec(
        name="orb",
        description="Opening Range Breakout: signals when price breaks the session open candle range.",
        params=[
            ParamSpec(
                "session_hour_utc",
                "int",
                13,
                0,
                23,
                "UTC hour of the session open candle (default 13 = NY open).",
            ),
        ],
        confidence=3,
    ),
    "liquidity_sweep": StrategySpec(
        name="liquidity_sweep",
        description="Signals when a wick sweeps the rolling high/low but the candle closes back inside.",
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
        confidence=4,
    ),
    "fvg": StrategySpec(
        name="fvg",
        description="Fair Value Gap: signals when price fills a 3-candle imbalance zone.",
        params=[
            ParamSpec(
                "lookback",
                "int",
                50,
                1,
                500,
                "Candles to watch for FVG fill after the gap forms.",
            ),
        ],
        confidence=4,
    ),
    "bos": StrategySpec(
        name="bos",
        description="Break of Structure / Change of Character: market structure shift signals.",
        params=[
            ParamSpec(
                "swing_lookback",
                "int",
                5,
                1,
                50,
                "Half-window size for swing high/low identification (window = 2×n+1).",
            ),
        ],
        confidence=3,
    ),
    "funding_reversion": StrategySpec(
        name="funding_reversion",
        description="Contrarian signals on extreme funding rates (mean reversion setup).",
        params=[
            ParamSpec(
                "threshold",
                "float",
                0.001,
                0.0001,
                0.01,
                "Absolute funding rate threshold to trigger a signal.",
            ),
        ],
        requires_funding=True,
        confidence=4,
    ),
    "smt_divergence": StrategySpec(
        name="smt_divergence",
        description="SMT divergence: primary makes new swing extreme but correlated asset does not.",
        params=[
            ParamSpec(
                "lookback",
                "int",
                10,
                2,
                200,
                "Rolling window for swing high/low comparison between assets.",
            ),
        ],
        requires_secondary=True,
        confidence=5,
    ),
    "eqh_eql": StrategySpec(
        name="eqh_eql",
        description="Equal Highs/Lows: liquidity sweep of a double-top or double-bottom level.",
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
        confidence=4,
    ),
}

SIGNAL_COLUMNS: list[str] = ["open_time", "direction", "reason", "sl_price", "context"]


_SGT = timezone(timedelta(hours=8))


def _fmt_time(ts_ms: int) -> str:
    """Format a Unix ms timestamp as a short SGT (UTC+8) string for alert context."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=_SGT).strftime("%d-%b %H:%M")


SEASONALITY_COLUMNS: list[str] = [
    "period_type",
    "period_value",
    "avg_return_pct",
    "win_rate",
    "count",
]

KNOWN_STRATEGIES: list[str] = list(STRATEGY_REGISTRY.keys())


def _empty_signals() -> pd.DataFrame:
    return pd.DataFrame(columns=SIGNAL_COLUMNS)


def _signals_to_df(signals: list[dict[str, object]]) -> pd.DataFrame:
    if not signals:
        return _empty_signals()
    return (
        pd.DataFrame(signals, columns=SIGNAL_COLUMNS)
        .drop_duplicates(subset=["open_time"])
        .reset_index(drop=True)
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
    min_wick_body_ratio: float = 0.5,
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
) -> pd.DataFrame:
    """Detect retests of Marubozu (wickless) candle open prices.

    A Marubozu is a candle where both wicks are <= max_wick_ratio × body.
    The open of a bullish Marubozu acts as support (order block).
    The open of a bearish Marubozu acts as resistance (supply zone).

    Signal fires when a later candle retests the open price zone.
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
    session_hour_utc: int = 13,
    timeframe_minutes: int = 15,
) -> pd.DataFrame:
    """Detect Opening Range Breakout signals.

    Identifies candles whose open_time falls on session_hour_utc (UTC).
    That candle's high/low defines the session range.
    If the NEXT candle closes above range_high → long signal.
    If the NEXT candle closes below range_low → short signal.

    Default session_hour_utc=13 targets the NY session open (13:00 UTC).
    Only runs on timeframes < 60 minutes; ORB is meaningless on hourly+ candles.
    """
    if timeframe_minutes >= 60:
        return _empty_signals()
    n = len(df)
    if n < 2:
        return _empty_signals()

    hours = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True).dt.hour

    signals: list[dict[str, object]] = []

    for i in range(n - 1):
        if int(hours.iloc[i]) != session_hour_utc:
            continue

        range_high = float(df.iloc[i]["high"])
        range_low = float(df.iloc[i]["low"])
        nxt = df.iloc[i + 1]
        nxt_close = float(nxt["close"])

        range_ctx = f"Range: {_fmt_time(int(df.iloc[i]['open_time']))}"
        if nxt_close > range_high:
            signals.append(
                {
                    "open_time": int(nxt["open_time"]),
                    "direction": "long",
                    "reason": f"orb_long@{range_high:.2f}",
                    "sl_price": range_low,
                    "context": range_ctx,
                }
            )
        elif nxt_close < range_low:
            signals.append(
                {
                    "open_time": int(nxt["open_time"]),
                    "direction": "short",
                    "reason": f"orb_short@{range_low:.2f}",
                    "sl_price": range_high,
                    "context": range_ctx,
                }
            )

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 5. Liquidity Sweep + Reversal
# ---------------------------------------------------------------------------


def detect_liquidity_sweep(
    df: pd.DataFrame,
    lookback: int = 20,
    min_sweep_pct: float = 0.001,
) -> pd.DataFrame:
    """Detect liquidity sweep + reversal signals.

    A sweep occurs when a candle's wick exceeds the rolling high/low of the
    lookback window by at least min_sweep_pct, but the candle CLOSES back inside.

    Short signal: wick above lookback max high by min_sweep_pct, close below it.
    Long signal: wick below lookback min low by min_sweep_pct, close above it.
    """
    n = len(df)
    if n < lookback + 1:
        return _empty_signals()

    rolling_high = df["high"].astype(float).rolling(lookback).max().shift(1)
    rolling_low = df["low"].astype(float).rolling(lookback).min().shift(1)

    signals: list[dict[str, object]] = []

    for i in range(lookback, n):
        row = df.iloc[i]
        candle_high = float(row["high"])
        candle_low = float(row["low"])
        candle_close = float(row["close"])
        swing_high = float(rolling_high.iloc[i])
        swing_low = float(rolling_low.iloc[i])

        if candle_high > swing_high * (1 + min_sweep_pct) and candle_close < swing_high:
            signals.append(
                {
                    "open_time": int(row["open_time"]),
                    "direction": "short",
                    "reason": f"sweep_high@{swing_high:.2f}",
                    "sl_price": candle_high,
                    "context": "",
                }
            )

        if candle_low < swing_low * (1 - min_sweep_pct) and candle_close > swing_low:
            signals.append(
                {
                    "open_time": int(row["open_time"]),
                    "direction": "long",
                    "reason": f"sweep_low@{swing_low:.2f}",
                    "sl_price": candle_low,
                    "context": "",
                }
            )

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 6. Fair Value Gap (FVG)
# ---------------------------------------------------------------------------


def detect_fvg(
    df: pd.DataFrame,
    lookback: int = 50,
) -> pd.DataFrame:
    """Detect Fair Value Gap (3-candle imbalance) fill signals.

    Bullish FVG: candle[i-1].high < candle[i+1].low — gap up imbalance.
    Bearish FVG: candle[i-1].low > candle[i+1].high — gap down imbalance.

    Signal fires on the first candle within lookback that enters the FVG zone.
    Long = price fills bullish FVG. Short = price fills bearish FVG.
    """
    n = len(df)
    if n < 3:
        return _empty_signals()

    signals: list[dict[str, object]] = []

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
            ce = (gap_bot + gap_top) / 2
            for j in range(i + 2, end):
                fut = df.iloc[j]
                if float(fut["low"]) <= ce and float(fut["close"]) > gap_bot:
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
            ce = (gap_bot + gap_top) / 2
            for j in range(i + 2, end):
                fut = df.iloc[j]
                if float(fut["high"]) >= ce and float(fut["close"]) < gap_top:
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
) -> pd.DataFrame:
    """Detect Break of Structure (BOS) and Change of Character (CHoCH).

    Swing highs and lows are identified using a rolling window of size
    2 × swing_lookback + 1.

    BOS long:   higher swing high in established uptrend.
    BOS short:  lower swing low in established downtrend.
    CHoCH long: first higher swing high after a downtrend (trend reversal).
    CHoCH short: first lower swing low after an uptrend (trend reversal).
    """
    n = len(df)
    if n < swing_lookback * 3:
        return _empty_signals()

    high_series = df["high"].astype(float)
    low_series = df["low"].astype(float)

    window = 2 * swing_lookback + 1
    rolling_max = high_series.rolling(
        window=window, center=False, min_periods=window
    ).max()
    rolling_min = low_series.rolling(
        window=window, center=False, min_periods=window
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
            if last_sh is not None:
                if price > last_sh:
                    label = "choch_long" if trend == "down" else "bos_long"
                    signals.append(
                        {
                            "open_time": open_time,
                            "direction": "long",
                            "reason": f"{label}@{price:.2f}",
                            "sl_price": last_sl if last_sl is not None else 0.0,
                            "context": "",
                        }
                    )
                    trend = "up"
            last_sh = price

        else:  # "L"
            if last_sl is not None:
                if price < last_sl:
                    label = "choch_short" if trend == "up" else "bos_short"
                    signals.append(
                        {
                            "open_time": open_time,
                            "direction": "short",
                            "reason": f"{label}@{price:.2f}",
                            "sl_price": last_sh if last_sh is not None else 0.0,
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
    threshold: float = 0.001,
) -> pd.DataFrame:
    """Detect extreme funding rate conditions as contrarian signals.

    Extreme positive funding (rate > threshold) → short signal.
    Extreme negative funding (rate < -threshold) → long signal.

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
    lookback: int = 10,
) -> pd.DataFrame:
    """Detect Smart Money Technique (SMT) divergence between two correlated assets.

    Bearish SMT: primary makes a new swing high but secondary does NOT →
    the primary's new high is a likely stop hunt → short signal.

    Bullish SMT: primary makes a new swing low but secondary does NOT →
    the primary's new low is a likely stop hunt → long signal.

    Signals are tagged on the primary asset's open_time.
    Both DataFrames must share open_time values (inner join used).
    """
    if df_primary.empty or df_secondary.empty:
        return _empty_signals()

    primary = df_primary.set_index("open_time")[["high", "low"]].copy()
    secondary = df_secondary.set_index("open_time")[["high", "low"]].copy()

    merged = primary.join(secondary, lsuffix="_p", rsuffix="_s", how="inner")
    if len(merged) < lookback + 1:
        return _empty_signals()

    merged = merged.reset_index()
    n = len(merged)

    roll_max_p_high = merged["high_p"].rolling(lookback).max().shift(1)
    roll_max_s_high = merged["high_s"].rolling(lookback).max().shift(1)
    roll_min_p_low = merged["low_p"].rolling(lookback).min().shift(1)
    roll_min_s_low = merged["low_s"].rolling(lookback).min().shift(1)

    signals: list[dict[str, object]] = []

    for i in range(lookback, n):
        curr_p_high = float(merged["high_p"].iloc[i])
        curr_s_high = float(merged["high_s"].iloc[i])
        curr_p_low = float(merged["low_p"].iloc[i])
        curr_s_low = float(merged["low_s"].iloc[i])
        max_p_high = float(roll_max_p_high.iloc[i])
        max_s_high = float(roll_max_s_high.iloc[i])
        min_p_low = float(roll_min_p_low.iloc[i])
        min_s_low = float(roll_min_s_low.iloc[i])
        open_time = int(merged["open_time"].iloc[i])

        if curr_p_high > max_p_high and curr_s_high <= max_s_high:
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "short",
                    "reason": f"smt_bearish@{curr_p_high:.2f}",
                    "sl_price": curr_p_high,
                    "context": "",
                }
            )

        if curr_p_low < min_p_low and curr_s_low >= min_s_low:
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "long",
                    "reason": f"smt_bullish@{curr_p_low:.2f}",
                    "sl_price": curr_p_low,
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
) -> pd.DataFrame:
    """Detect Equal Highs / Equal Lows liquidity sweep signals.

    Equal Highs (EQH): two swing highs within tolerance_pct of each other form a
    liquidity pool. When the latest candle wicks above that level (high > EQH)
    but closes below it, a liquidity raid has occurred → short signal.

    Equal Lows (EQL): two swing lows within tolerance_pct form a pool below price.
    When the latest candle wicks below that level (low < EQL) but closes above
    it → long signal.

    Swing highs/lows are identified using a 3-candle-each-side local window
    (a candle is a swing high if its high is the max of the 7-candle window
    centred on it — using a non-centred rolling window to avoid lookahead bias).

    Only signals on the last candle of df.
    """
    n = len(df)
    if n < lookback + 1:
        return _empty_signals()

    # Identify swing highs/lows in the lookback window (excluding the signal candle).
    # window_df excludes the signal candle itself, so there is no lookahead bias —
    # we can use a centered comparison within this pre-determined window.
    window_df = df.iloc[-(lookback + 1) : -1].reset_index(drop=True)
    m = len(window_df)

    swing_side = 2  # candles on each side of the pivot candidate
    high_series = window_df["high"].astype(float)
    low_series = window_df["low"].astype(float)

    swing_highs: list[tuple[int, float]] = []  # (row_idx_in_window_df, price)
    swing_lows: list[tuple[int, float]] = []

    for i in range(m):
        lo_bound = max(0, i - swing_side)
        hi_bound = min(m, i + swing_side + 1)
        h = float(high_series.iloc[i])
        neighbourhood_h = high_series.iloc[lo_bound:hi_bound]
        if h >= float(neighbourhood_h.max()):
            swing_highs.append((i, h))
        lo = float(low_series.iloc[i])
        neighbourhood_l = low_series.iloc[lo_bound:hi_bound]
        if lo <= float(neighbourhood_l.min()):
            swing_lows.append((i, lo))

    signal_row = df.iloc[-1]
    sig_high = float(signal_row["high"])
    sig_low = float(signal_row["low"])
    sig_close = float(signal_row["close"])
    sig_open_time = int(signal_row["open_time"])

    signals: list[dict[str, object]] = []

    # --- EQH: find the highest pair of swing highs within tolerance that the
    #          signal candle sweeps (wick above, close below).
    best_eqh: tuple[int, float, int, float] | None = None  # (i1, h1, i2, h2)
    for a in range(len(swing_highs)):
        for b in range(a + 1, len(swing_highs)):
            i1, h1 = swing_highs[a]
            i2, h2 = swing_highs[b]
            level = max(h1, h2)
            if abs(h1 - h2) / level <= tolerance_pct:
                # Only consider pairs that the signal candle actually sweeps
                if sig_high <= level or sig_close >= level:
                    continue
                # Among swept pairs, prefer the highest level (most significant)
                if best_eqh is None or level > max(best_eqh[1], best_eqh[3]):
                    best_eqh = (i1, h1, i2, h2)

    if best_eqh is not None:
        i1, h1, i2, h2 = best_eqh
        eqh_level = max(h1, h2)
        if sig_high > eqh_level and sig_close < eqh_level:
            sl_price = eqh_level * (1 + 0.001)  # 0.1% buffer above level
            ts1 = _fmt_time(int(window_df.iloc[i1]["open_time"]))
            ts2 = _fmt_time(int(window_df.iloc[i2]["open_time"]))
            ctx = f"EQH: {ts1} @ {h1:,.2f} · {ts2} @ {h2:,.2f}"
            signals.append(
                {
                    "open_time": sig_open_time,
                    "direction": "short",
                    "reason": f"eqh_short@{h1:.2f}-{h2:.2f}",
                    "sl_price": sl_price,
                    "context": ctx,
                }
            )

    # --- EQL: find the lowest pair of swing lows within tolerance that the
    #          signal candle sweeps (wick below, close above).
    best_eql: tuple[int, float, int, float] | None = None
    for a in range(len(swing_lows)):
        for b in range(a + 1, len(swing_lows)):
            i1, l1 = swing_lows[a]
            i2, l2 = swing_lows[b]
            level = min(l1, l2)
            if level == 0.0:
                continue
            if abs(l1 - l2) / level <= tolerance_pct:
                # Only consider pairs that the signal candle actually sweeps
                if sig_low >= level or sig_close <= level:
                    continue
                # Among swept pairs, prefer the lowest level (most significant)
                if best_eql is None or level < min(best_eql[1], best_eql[3]):
                    best_eql = (i1, l1, i2, l2)

    if best_eql is not None:
        i1, l1, i2, l2 = best_eql
        eql_level = min(l1, l2)
        if sig_low < eql_level and sig_close > eql_level:
            sl_price = eql_level * (1 - 0.001)  # 0.1% buffer below level
            ts1 = _fmt_time(int(window_df.iloc[i1]["open_time"]))
            ts2 = _fmt_time(int(window_df.iloc[i2]["open_time"]))
            ctx = f"EQL: {ts1} @ {l1:,.2f} · {ts2} @ {l2:,.2f}"
            signals.append(
                {
                    "open_time": sig_open_time,
                    "direction": "long",
                    "reason": f"eql_long@{l1:.2f}-{l2:.2f}",
                    "sl_price": sl_price,
                    "context": ctx,
                }
            )

    return _signals_to_df(signals)

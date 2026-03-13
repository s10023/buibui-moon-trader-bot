"""Pure strategy signal detection functions for analytics.

All functions accept pandas DataFrames of OHLCV data and return a DataFrame
of detected signals with columns: open_time (int), direction (str), reason (str).

Seasonality returns a summary statistics DataFrame instead.
No module-level side effects.
"""

import pandas as pd

SIGNAL_COLUMNS: list[str] = ["open_time", "direction", "reason"]

SEASONALITY_COLUMNS: list[str] = [
    "period_type",
    "period_value",
    "avg_return_pct",
    "win_rate",
    "count",
]

KNOWN_STRATEGIES: list[str] = [
    "seasonality",
    "wick_fill",
    "marubozu",
    "orb",
    "liquidity_sweep",
    "fvg",
    "bos",
    "funding_reversion",
    "smt_divergence",
]


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
            for j in range(i + 1, end):
                fut = df.iloc[j]
                if float(fut["low"]) <= zone_top:
                    signals.append(
                        {
                            "open_time": int(fut["open_time"]),
                            "direction": "long",
                            "reason": f"wick_fill_long@{zone_bot:.2f}-{zone_top:.2f}",
                        }
                    )
                    break

        if upper_wick >= min_wick_body_ratio * body:
            zone_bot = max(candle_open, candle_close)
            zone_top = candle_high
            for j in range(i + 1, end):
                fut = df.iloc[j]
                if float(fut["high"]) >= zone_bot:
                    signals.append(
                        {
                            "open_time": int(fut["open_time"]),
                            "direction": "short",
                            "reason": f"wick_fill_short@{zone_bot:.2f}-{zone_top:.2f}",
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
) -> pd.DataFrame:
    """Detect Opening Range Breakout signals.

    Identifies candles whose open_time falls on session_hour_utc (UTC).
    That candle's high/low defines the session range.
    If the NEXT candle closes above range_high → long signal.
    If the NEXT candle closes below range_low → short signal.

    Default session_hour_utc=13 targets the NY session open (13:00 UTC).
    """
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

        if nxt_close > range_high:
            signals.append(
                {
                    "open_time": int(nxt["open_time"]),
                    "direction": "long",
                    "reason": f"orb_long@{range_high:.2f}",
                }
            )
        elif nxt_close < range_low:
            signals.append(
                {
                    "open_time": int(nxt["open_time"]),
                    "direction": "short",
                    "reason": f"orb_short@{range_low:.2f}",
                }
            )

    return _signals_to_df(signals)


# ---------------------------------------------------------------------------
# 5. Liquidity Sweep + Reversal
# ---------------------------------------------------------------------------


def detect_liquidity_sweep(
    df: pd.DataFrame,
    lookback: int = 20,
) -> pd.DataFrame:
    """Detect liquidity sweep + reversal signals.

    A sweep occurs when a candle's wick exceeds the rolling high/low of the
    lookback window but the candle CLOSES back inside the range.

    Short signal: wick above lookback max high, close below it.
    Long signal: wick below lookback min low, close above it.
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

        if candle_high > swing_high and candle_close < swing_high:
            signals.append(
                {
                    "open_time": int(row["open_time"]),
                    "direction": "short",
                    "reason": f"sweep_high@{swing_high:.2f}",
                }
            )

        if candle_low < swing_low and candle_close > swing_low:
            signals.append(
                {
                    "open_time": int(row["open_time"]),
                    "direction": "long",
                    "reason": f"sweep_low@{swing_low:.2f}",
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

        if prev_high < nxt_low:
            gap_bot = prev_high
            gap_top = nxt_low
            for j in range(i + 2, end):
                fut = df.iloc[j]
                if float(fut["low"]) <= gap_top:
                    signals.append(
                        {
                            "open_time": int(fut["open_time"]),
                            "direction": "long",
                            "reason": f"fvg_long@{gap_bot:.2f}-{gap_top:.2f}",
                        }
                    )
                    break

        if prev_low > nxt_high:
            gap_top = prev_low
            gap_bot = nxt_high
            for j in range(i + 2, end):
                fut = df.iloc[j]
                if float(fut["high"]) >= gap_bot:
                    signals.append(
                        {
                            "open_time": int(fut["open_time"]),
                            "direction": "short",
                            "reason": f"fvg_short@{gap_top:.2f}-{gap_bot:.2f}",
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
    rolling_max = high_series.rolling(window=window, center=True, min_periods=1).max()
    rolling_min = low_series.rolling(window=window, center=True, min_periods=1).min()

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
    right = funding_sorted[["funding_time", "funding_rate"]].rename(
        columns={"funding_time": "ts"}
    )

    merged = pd.merge_asof(left, right, on="ts", direction="backward")
    merged["open_time"] = ohlcv_sorted["open_time"].values
    merged["rate"] = pd.to_numeric(merged["funding_rate"], errors="coerce")
    valid = merged[merged["rate"].notna()]

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
                }
            )
        elif rate < -threshold:
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "long",
                    "reason": f"funding_short_extreme@{rate:.4f}",
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
                }
            )

        if curr_p_low < min_p_low and curr_s_low >= min_s_low:
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "long",
                    "reason": f"smt_bullish@{curr_p_low:.2f}",
                }
            )

    return _signals_to_df(signals)

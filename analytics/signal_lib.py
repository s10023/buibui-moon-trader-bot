"""Pure signal scanning library.

scan_symbol(): runs requested strategies against a pre-fetched OHLCV DataFrame,
               returning SignalEvents only for the latest candle.
run_scan_cycle(): fans out across all symbols/timeframes, pre-fetches OHLCV once
                  per (symbol, timeframe), deduplicates via CooldownStore,
                  formats alerts, and optionally sends Telegram.
No module-level side effects.
"""

import datetime
import logging
import math
import os
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import duckdb
import pandas as pd

from analytics.backtest_lib import (
    BacktestResult,
    _is_low_volume,
    _is_volume_spike,
    filter_signals_by_day,
    run_backtest,
)
from analytics.cme_gap_lib import cme_gap_alert_warning, get_recent_cme_gap
from analytics.data_store import (
    BacktestSnapshot,
    _backtest_run_id,
    _make_bt_cache_key,
    get_backtest_cache,
    get_funding_rates,
    get_ohlcv,
    get_signals_history,
    put_backtest_cache,
    upsert_backtest_run,
    upsert_signal_outcome,
    upsert_signals,
)
from analytics.indicators_lib import STRATEGY_REGISTRY
from analytics.signal_config import (
    BacktestFilterConfig,
    BiasConfig,
    StrategyOverride,
    _day_filter_to_weekdays,
)
from signals.alert_formatter import (
    ConfluenceData,
    SignalEvent,
    StatsContext,
    format_confluence_alert,
)
from signals.cooldown_store import CooldownStore
from signals.registry import SIGNAL_REGISTRY

logger = logging.getLogger(__name__)

_CANDLE_CLOSE_BUFFER_SECS = 10

# Detectors only need recent candles to check the latest signal (max lookback = 100).
# Slicing to this window before scan_symbol drastically reduces Phase 2 time
# (e.g. 15m/200d = 19,200 rows → 200 rows = ~96× less data for pandas ops).
# The full OHLCV window is preserved in ohlcv_map for _compute_backtest in Phase 3.
_SCAN_WINDOW = 200

# Two-layer backtest cache: L1 (module dict, fast) backed by L2 (DuckDB, survives restarts).
# Keys are 24-char hex strings from _make_bt_cache_key(run_id, last_candle_ts).
_bt_mem_cache: dict[str, BacktestResult | BacktestSnapshot | None] = {}


def _reset_bt_cache() -> None:
    """Clear L1 memory cache. Call in test fixtures to prevent state bleed."""
    _bt_mem_cache.clear()


def _fmt_hold(hours: float) -> str:
    """Format median hold time: '~4h', '~3d'."""
    if hours >= 48:
        return f"~{hours / 24:.0f}d"
    return f"~{hours:.0f}h"


def parse_timeframe_secs(tf: str) -> int:
    """Convert a timeframe string to seconds (e.g. '4h' → 14400, '15m' → 900)."""
    units = {"m": 60, "h": 3600, "d": 86400}
    return int(tf[:-1]) * units[tf[-1]]


def secs_until_next_boundary(timeframes: list[str]) -> tuple[float, float]:
    """Return (sleep_seconds, wakeup_unix_timestamp) for the next candle close.

    Wakes at the earliest upcoming boundary + a small buffer so Binance has
    time to finalise the candle (e.g. 04:00:10, not 04:00:00).
    """
    now = time.time()
    next_wakeups = []
    for tf in timeframes:
        interval = parse_timeframe_secs(tf)
        next_close = math.ceil(now / interval) * interval
        next_wakeups.append(next_close + _CANDLE_CLOSE_BUFFER_SECS)
    wake_ts = min(next_wakeups)
    return max(0.0, wake_ts - now), wake_ts


def _filter_signals_by_adr(
    ohlcv_df: pd.DataFrame,
    signals_df: pd.DataFrame,
    threshold: float,
) -> pd.DataFrame:
    """Return signals where the ADR consumed at signal time is below threshold.

    For each signal candle, computes:
      consumed_ratio = (cumulative intraday range up to that candle) / (14-day ADR)

    Signals where consumed_ratio >= threshold are dropped — the daily move was
    already mostly done when the signal fired.  Signals whose candle is not found
    in ohlcv_df pass through untouched (safe-default: don't suppress unknown data).
    """
    if signals_df.empty or ohlcv_df.empty:
        return signals_df

    df = ohlcv_df.copy()
    df["_date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.date
    df = df.sort_values("open_time")

    # Day open = first candle's open price within each calendar day
    day_opens: pd.Series = df.groupby("_date")["open"].first()

    # Cumulative intraday high/low up to each candle (inclusive)
    df["_cum_high"] = df.groupby("_date")["high"].cummax()
    df["_cum_low"] = df.groupby("_date")["low"].cummin()
    df["_day_open"] = df["_date"].map(day_opens)

    # Today's range as a fraction of day_open (avoid div/0)
    df["_today_range"] = (df["_cum_high"] - df["_cum_low"]) / df["_day_open"].where(
        df["_day_open"] > 0
    )

    # 14-day rolling ADR from daily extremes
    daily = (
        df.groupby("_date")
        .agg(_dh=("high", "max"), _dl=("low", "min"), _do=("open", "first"))
        .sort_index()
    )
    daily["_dr"] = (daily["_dh"] - daily["_dl"]) / daily["_do"].where(daily["_do"] > 0)
    daily["_adr14"] = daily["_dr"].rolling(14, min_periods=1).mean()

    df["_adr14"] = df["_date"].map(daily["_adr14"])

    # Consumed ratio at each candle; NaN when adr_14 is zero or unknown
    df["_consumed"] = df["_today_range"] / df["_adr14"].where(df["_adr14"] > 0)

    # Direction: close in upper half of today's range → move was upward
    df["_mid"] = (df["_cum_high"] + df["_cum_low"]) / 2
    df["_move_up"] = (df["close"] > df["_mid"]).astype(float)

    consumed_map: dict[int, float] = dict(
        zip(df["open_time"].astype(int), df["_consumed"].astype(float), strict=False)
    )
    move_up_map: dict[int, float] = dict(
        zip(df["open_time"].astype(int), df["_move_up"], strict=False)
    )

    signal_ratios = signals_df["open_time"].astype(int).map(consumed_map)
    signal_move_up = signals_df["open_time"].astype(int).map(move_up_map)

    # Suppress only the chasing direction: LONGs when move was up, SHORTs when down.
    # NaN move_up (candle not found) → neither condition fires → safe pass-through.
    chasing = ((signal_move_up == 1.0) & (signals_df["direction"] == "long")) | (
        (signal_move_up == 0.0) & (signals_df["direction"] == "short")
    )
    keep = signal_ratios.isna() | (signal_ratios < threshold) | ~chasing
    return signals_df[keep].reset_index(drop=True)


def _compute_backtest(
    ohlcv_df: pd.DataFrame,
    strategy: str,
    secondary_df: pd.DataFrame | None,
    funding_df: pd.DataFrame | None,
    symbol: str,
    timeframe: str,
    sl_pct: float,
    tp_r: float,
    fee_pct: float = 0.0,
    day_filter: str = "off",
    min_sl_pct: float = 0.0,
    atr_sl_multiplier: float | None = None,
    adr_suppress_threshold: float | None = None,
    adr_exempt: bool = False,
    volume_suppress: bool = False,
    volume_spike_boost: bool = False,
    volume_suppress_long: bool | None = None,
    volume_suppress_short: bool | None = None,
    volume_spike_boost_long: bool | None = None,
    volume_spike_boost_short: bool | None = None,
    tp_r_long: float | None = None,
    tp_r_short: float | None = None,
) -> BacktestResult | None:
    """Run strategy detector on ohlcv[:-1] and backtest the resulting signals.

    Excludes the current (latest) candle to avoid lookahead bias.
    Returns None if there is insufficient data or the detector raises.

    adr_suppress_threshold: when set, filters historical signals where the ADR
    consumed at signal time exceeded the threshold — mirrors the live bias gate so
    backtested avg_r reflects only trades that would have passed the gate.
    adr_exempt: when True, skips the ADR filter regardless of threshold (for
    breakout/continuation strategies that should not be ADR-gated).
    volume_suppress: skip signal candles with volume < 1.5× rolling mean.
    volume_spike_boost: exempt spike candles (> 3× rolling mean) from suppression.
    volume_suppress_long/short: directional overrides — take precedence over volume_suppress.
    volume_spike_boost_long/short: directional overrides — take precedence over volume_spike_boost.
    """
    hist_df = ohlcv_df.iloc[:-1]
    if len(hist_df) < 3:
        return None

    plugin = SIGNAL_REGISTRY.get(strategy)
    if plugin is None:
        return None
    spec = STRATEGY_REGISTRY.get(strategy)

    try:
        if spec and spec.requires_funding:
            if funding_df is None or funding_df.empty:
                return None
            signals_df = plugin["detector"](hist_df, funding_df)
        elif spec and spec.requires_secondary:
            if secondary_df is None or secondary_df.empty:
                return None
            signals_df = plugin["detector"](hist_df, secondary_df)
        else:
            signals_df = plugin["detector"](hist_df)
    except Exception:
        logger.exception(
            "Backtest detector %s raised for %s %s", strategy, symbol, timeframe
        )
        return None

    allowed_days = _day_filter_to_weekdays(day_filter)
    if allowed_days is not None:
        signals_df = filter_signals_by_day(signals_df, allowed_days)

    if adr_suppress_threshold is not None and not adr_exempt and not signals_df.empty:
        signals_df = _filter_signals_by_adr(hist_df, signals_df, adr_suppress_threshold)

    return run_backtest(
        hist_df,
        signals_df,
        symbol,
        timeframe,
        strategy,
        sl_pct=sl_pct,
        tp_r=tp_r,
        fee_pct=fee_pct,
        min_sl_pct=min_sl_pct,
        atr_sl_multiplier=atr_sl_multiplier,
        volume_suppress=volume_suppress,
        volume_spike_boost=volume_spike_boost,
        volume_suppress_long=volume_suppress_long,
        volume_suppress_short=volume_suppress_short,
        volume_spike_boost_long=volume_spike_boost_long,
        volume_spike_boost_short=volume_spike_boost_short,
        tp_r_long=tp_r_long,
        tp_r_short=tp_r_short,
    )


def _backtest_summary(
    results: Mapping[str, BacktestResult | BacktestSnapshot | None],
    strategies: list[str],
    cfg: BacktestFilterConfig,
    tf: str = "",
    direction: str = "",
) -> str:
    """Format a one-line backtest summary for appending to an alert message.

    Shows direction-specific win rate and avg R when direction is provided.
    Falls back to overall stats when directional bucket has fewer than min_trades.

    Single strategy (long): '📊 Backtest 90d [↑]: 62% win · avg +1.4R (18 longs) · hold ~16h'
    Multiple (short):       '📊 Backtest 90d [↓]: fvg 55%·+1.1R (12) · bos n/a (3)'
    No direction:           '📊 Backtest 90d: 62% win (28 trades) · hold ~4h'
    """
    arrow = " [↑]" if direction == "long" else " [↓]" if direction == "short" else ""
    single = len(strategies) == 1
    min_trades = cfg.effective_min_trades(tf)
    parts: list[str] = []

    for s in strategies:
        result = results.get(s)
        if result is None:
            parts.append("n/a" if single else f"{s}: n/a")
            continue

        if direction == "long":
            dir_trades = result.long_closed_trades
            dir_win_rate = result.long_win_rate
            dir_avg_r = result.long_avg_r
            dir_median_h = result.long_median_duration_h
            trade_noun = "long"
        elif direction == "short":
            dir_trades = result.short_closed_trades
            dir_win_rate = result.short_win_rate
            dir_avg_r = result.short_avg_r
            dir_median_h = result.short_median_duration_h
            trade_noun = "short"
        else:
            dir_trades = result.closed_trades
            dir_win_rate = result.win_rate if result.closed_trades else None
            dir_avg_r = result.avg_r if result.closed_trades else None
            dir_median_h = result.median_duration_h
            trade_noun = "trade"

        n = len(dir_trades)
        hold_str = f" {_fmt_hold(dir_median_h)}" if dir_median_h is not None else ""
        hold_suffix = f" · hold{hold_str}" if single and hold_str else ""
        if n < min_trades or dir_win_rate is None:
            label = f"n/a ({n} {trade_noun}s)" if single else f"{s}: n/a ({n})"
        else:
            pct = f"{dir_win_rate:.0%}"
            if dir_avg_r is not None:
                avg_r_str = f"{dir_avg_r:+.1f}R"
                label = (
                    f"{pct} win · avg {avg_r_str} ({n} {trade_noun}s){hold_suffix}"
                    if single
                    else f"{s}: {pct}·{avg_r_str} ({n}){hold_str}"
                )
            else:
                label = (
                    f"{pct} win ({n} {trade_noun}s){hold_suffix}"
                    if single
                    else f"{s}: {pct} ({n}){hold_str}"
                )
        parts.append(label)

    body = " · ".join(parts)
    window_label = f"since {cfg.since}" if cfg.since else f"{cfg.days}d"
    return f"📊 Backtest {window_label}{arrow}: {body}"


def scan_symbol(
    ohlcv_df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    strategies: list[str],
    secondary_df: pd.DataFrame | None = None,
    funding_df: pd.DataFrame | None = None,
    day_filter: str = "off",
    smt_trend_filter: int = 1,
    strategy_timeframes: dict[str, list[str]] | None = None,
    confidence_override: dict[str, dict[str, int]] | None = None,
    directional_confidence_override: dict[str, dict[str, dict[str, int]]] | None = None,
) -> list[SignalEvent]:
    """Run requested strategies against a pre-fetched OHLCV DataFrame.

    Returns SignalEvents whose open_time matches the latest candle in the data.
    Only the latest candle is checked — signals on older candles are ignored
    to prevent re-alerting on historical data after a restart.

    When day_filter is "tue_thu", signals whose open_time falls on Monday (weekday 0)
    or Friday (weekday 4) in UTC are suppressed (ICT weekly cycle — lower-quality
    manipulation/distribution days). "weekdays" suppresses weekends only. "off" disables.

    strategy_timeframes: optional per-strategy TF allow-list loaded from
    [strategy_timeframes] in signal_watch.toml.  If a strategy appears in this
    mapping, it is only run when the current timeframe is in its allowed list.
    Strategies not listed run on all timeframes (no restriction).
    """
    if ohlcv_df.empty or len(ohlcv_df) < 3:
        return []

    # Exclude the currently-forming (not yet closed) candle so detectors only
    # see completed candles.  The signal runner wakes up at candle-close
    # boundaries, but Binance/the sync layer often includes the new open candle
    # in the response.  Passing it to pattern detectors (trend_day, marubozu,
    # engulfing, …) would fire on a candle with as little as a few seconds of
    # data, producing spurious 100%-body readings.
    closed_df = ohlcv_df.iloc[:-1]
    latest_open_time = int(closed_df["open_time"].iloc[-1])
    latest_close = float(closed_df["close"].iloc[-1])

    events: list[SignalEvent] = []

    _excluded_from_registry = {"seasonality", "funding_reversion"}

    for strategy_name in strategies:
        plugin = SIGNAL_REGISTRY.get(strategy_name)
        if plugin is None:
            if strategy_name not in _excluded_from_registry:
                logger.warning("Unknown strategy %s — skipping", strategy_name)
            continue

        spec = STRATEGY_REGISTRY.get(strategy_name)
        requires_funding = spec.requires_funding if spec else False
        requires_secondary = spec.requires_secondary if spec else False

        # Per-strategy timeframe allow-list from TOML [strategy_timeframes].
        # If the strategy is listed, skip it when the current TF is not allowed.
        if strategy_timeframes:
            allowed_tfs = strategy_timeframes.get(strategy_name)
            if allowed_tfs is not None and timeframe not in allowed_tfs:
                logger.debug(
                    "Skipping %s for %s %s — not in allowed TFs %s",
                    strategy_name,
                    symbol,
                    timeframe,
                    allowed_tfs,
                )
                continue

        try:
            if requires_funding:
                if funding_df is None or funding_df.empty:
                    logger.debug(
                        "Skipping %s for %s — no funding data", strategy_name, symbol
                    )
                    continue
                signals_df = plugin["detector"](closed_df, funding_df)
            elif requires_secondary:
                if secondary_df is None or secondary_df.empty:
                    logger.debug(
                        "Skipping %s for %s — no secondary data", strategy_name, symbol
                    )
                    continue
                if strategy_name == "smt_divergence":
                    signals_df = plugin["detector"](
                        closed_df, secondary_df, trend_filter=smt_trend_filter
                    )
                else:
                    signals_df = plugin["detector"](closed_df, secondary_df)
            else:
                signals_df = plugin["detector"](closed_df)
        except Exception:
            logger.exception(
                "Detector %s raised for %s %s", strategy_name, symbol, timeframe
            )
            continue

        if signals_df.empty:
            continue

        latest_signals = signals_df[signals_df["open_time"] == latest_open_time]
        for _, row in latest_signals.iterrows():
            events.append(
                SignalEvent(
                    symbol=symbol,
                    timeframe=timeframe,
                    strategy=strategy_name,
                    direction=str(row["direction"]),
                    reason=str(row["reason"]),
                    open_time=latest_open_time,
                    price=latest_close,
                    sl_price=float(row["sl_price"]),
                    tp_price=float(row["tp_price"]) if row.get("tp_price") else 0.0,
                    context=str(row["context"]),
                    confidence=(
                        (directional_confidence_override or {})
                        .get(strategy_name, {})
                        .get(timeframe, {})
                        .get(str(row["direction"]))
                        or (confidence_override or {})
                        .get(strategy_name, {})
                        .get(timeframe)
                        or STRATEGY_REGISTRY[strategy_name].get_confidence(timeframe)
                    ),
                    low_volume=bool(row.get("low_volume", False)),
                )
            )

    allowed_weekdays = _day_filter_to_weekdays(day_filter)
    if allowed_weekdays is not None and events:
        filtered: list[SignalEvent] = []
        for event in events:
            weekday = datetime.datetime.fromtimestamp(
                event.open_time / 1000, tz=datetime.UTC
            ).weekday()
            if weekday not in allowed_weekdays:
                logger.debug(
                    "Day filter suppressed %s %s %s (weekday %d)",
                    event.symbol,
                    event.timeframe,
                    event.strategy,
                    weekday,
                )
            else:
                filtered.append(event)
        return filtered

    return events


def _resolve_tp_r(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
    symbol: str,
    tf: str,
    global_tp_r: float,
    direction: str = "",
) -> float:
    """Resolve effective tp_r: symbol+TF → symbol → TF-specific → directional → strategy-wide → global."""
    if not strategy_params:
        return global_tp_r
    override = strategy_params.get(strategy)
    if override is None:
        return global_tp_r
    sym = override.per_symbol.get(symbol)
    if sym is not None:
        if tf in sym.tp_r_per_tf:
            return sym.tp_r_per_tf[tf]
        if sym.tp_r is not None:
            return sym.tp_r
    if tf in override.tp_r_per_tf:
        return override.tp_r_per_tf[tf]
    if direction == "long" and override.tp_r_long is not None:
        return override.tp_r_long
    if direction == "short" and override.tp_r_short is not None:
        return override.tp_r_short
    if override.tp_r is not None:
        return override.tp_r
    return global_tp_r


def _resolve_sl_pct(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
    symbol: str,
    tf: str,
    global_sl_pct: float,
) -> float:
    """Resolve effective sl_pct: symbol+TF → symbol → TF-specific → strategy-wide → global."""
    if not strategy_params:
        return global_sl_pct
    override = strategy_params.get(strategy)
    if override is None:
        return global_sl_pct
    sym = override.per_symbol.get(symbol)
    if sym is not None:
        if tf in sym.sl_pct_per_tf:
            return sym.sl_pct_per_tf[tf]
        if sym.sl_pct is not None:
            return sym.sl_pct
    if tf in override.sl_pct_per_tf:
        return override.sl_pct_per_tf[tf]
    if override.sl_pct is not None:
        return override.sl_pct
    return global_sl_pct


def _resolve_atr_sl_multiplier(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
    symbol: str,
    tf: str,
    global_atr_sl: float | None,
) -> float | None:
    """Resolve effective atr_sl_multiplier: symbol+TF → symbol → TF-specific → strategy-wide → global."""
    if not strategy_params:
        return global_atr_sl
    override = strategy_params.get(strategy)
    if override is None:
        return global_atr_sl
    sym = override.per_symbol.get(symbol)
    if sym is not None:
        if tf in sym.atr_sl_multiplier_per_tf:
            return sym.atr_sl_multiplier_per_tf[tf]
        if sym.atr_sl_multiplier is not None:
            return sym.atr_sl_multiplier
    if tf in override.atr_sl_multiplier_per_tf:
        return override.atr_sl_multiplier_per_tf[tf]
    if override.atr_sl_multiplier is not None:
        return override.atr_sl_multiplier
    return global_atr_sl


def _is_adr_exempt(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
) -> bool:
    if not strategy_params:
        return False
    override = strategy_params.get(strategy)
    return override.adr_exempt if override is not None else False


def _resolve_volume_suppress(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
    global_suppress: bool,
) -> bool:
    if strategy_params:
        override = strategy_params.get(strategy)
        if override is not None and override.volume_suppress is not None:
            return override.volume_suppress
    return global_suppress


def _resolve_volume_spike_boost(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
    global_boost: bool,
) -> bool:
    if strategy_params:
        override = strategy_params.get(strategy)
        if override is not None and override.volume_spike_boost is not None:
            return override.volume_spike_boost
    return global_boost


def _resolve_volume_suppress_long(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
) -> bool | None:
    if strategy_params:
        override = strategy_params.get(strategy)
        if override is not None:
            return override.volume_suppress_long
    return None


def _resolve_volume_suppress_short(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
) -> bool | None:
    if strategy_params:
        override = strategy_params.get(strategy)
        if override is not None:
            return override.volume_suppress_short
    return None


def _resolve_volume_spike_boost_long(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
) -> bool | None:
    if strategy_params:
        override = strategy_params.get(strategy)
        if override is not None:
            return override.volume_spike_boost_long
    return None


def _resolve_volume_spike_boost_short(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
) -> bool | None:
    if strategy_params:
        override = strategy_params.get(strategy)
        if override is not None:
            return override.volume_spike_boost_short
    return None


def _compute_stats_context(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    now_myt: datetime.datetime,
) -> StatsContext | None:
    """Return a StatsContext for the current symbol/DOW, or None on any error.

    Never raises — stats failure must never block signal dispatch.
    """
    try:
        from analytics.stats_lib import compute_all, compute_weekly_current_state

        bundle = compute_all(conn, symbol, days=90)
        wcs = compute_weekly_current_state(conn, symbol, bundle.adr.adr_14, days=90)
        # DOW must match the UTC-date grouping used in stats_lib (Binance daily = UTC day)
        dow_full = datetime.datetime.now(tz=datetime.UTC).strftime("%A")
        dow_short = dow_full[:3]  # e.g. "Thu"

        # P1=Low % for today's DOW
        p1_low_today = bundle.p1p2.by_dow.get(dow_short, bundle.p1p2.overall_p1_low_pct)

        # Today's ADR consumed
        adr_consumed = bundle.adr.today_consumed_pct

        # DOW row for bull% and avg return
        dow_row = next((r for r in bundle.dow.rows if r.dow == dow_short), None)
        bull_pct_today = dow_row.bull_pct if dow_row else 0.5
        avg_return_today = dow_row.avg_return_pct if dow_row else 0.0

        # Per-DOW peak hours
        peak_high_hour_dow = bundle.hourly.peak_high_hour_by_dow.get(dow_short)
        peak_low_hour_dow = bundle.hourly.peak_low_hour_by_dow.get(dow_short)

        # Weekly timing
        wk_low_still_ahead = bundle.weekly_p2_timing.low_still_ahead_by_dow.get(
            dow_short
        )
        wk_high_still_ahead = bundle.weekly_p2_timing.high_still_ahead_by_dow.get(
            dow_short
        )

        return StatsContext(
            today_dow=dow_full,
            p1_low_pct_today=p1_low_today,
            adr_14=bundle.adr.adr_14,
            adr_consumed_pct=adr_consumed,
            peak_high_hour_myt=bundle.hourly.peak_high_hour,
            peak_low_hour_myt=bundle.hourly.peak_low_hour,
            bull_pct_today=bull_pct_today,
            avg_return_today=avg_return_today,
            peak_high_hour_dow=peak_high_hour_dow,
            peak_low_hour_dow=peak_low_hour_dow,
            wk_low_still_ahead_pct=wk_low_still_ahead,
            wk_high_still_ahead_pct=wk_high_still_ahead,
            adr_move_up=bundle.adr.today_move_up,
            wk_low_still_ahead_conditioned_pct=wcs.low_still_ahead_conditioned
            if wcs
            else None,
            wk_high_still_ahead_conditioned_pct=wcs.high_still_ahead_conditioned
            if wcs
            else None,
            wk_move_bucket=wcs.move_bucket if wcs else None,
        )
    except Exception:
        logger.debug("_compute_stats_context failed for %s — skipping", symbol)
        return None


def _find_live_cofire(
    events: list[SignalEvent],
    ohlcv: pd.DataFrame,
    conn: duckdb.DuckDBPyConnection,
    combo_lookup: "dict[tuple[str, str, frozenset[str]], Any]",
    symbol: str,
    tf: str,
    window: int,
    min_avg_r: float,
) -> "ConfluenceData | None":
    """Return ConfluenceData for the best co-firing pair, or None.

    Checks two signal sources in order:
    1. Same-cycle events (candles_ago=0): two strategies fired this candle.
    2. Cross-cycle: recent signals stored in the DB for the past `window` candles.

    Returns the pair with the highest backtest avg_r that meets min_avg_r.
    Design note: keyed by (symbol, tf, frozenset) so cross-TF extension (step 4)
    can query a different tf key without restructuring this function.
    """
    if not events or not combo_lookup:
        return None

    # Build candle-index map for O(1) candles_ago computation.
    times: list[int] = ohlcv["open_time"].astype("int64").tolist()
    time_to_idx: dict[int, int] = {t: i for i, t in enumerate(times)}

    current_open_time = int(events[0].open_time)
    current_idx = time_to_idx.get(current_open_time, len(times) - 1)
    current_direction = events[0].direction
    current_strats = {e.strategy for e in events}

    best_r: float = -1.0
    best: ConfluenceData | None = None

    def _consider(primary_strat: str, co_strat: str, co_open_time: int) -> None:
        nonlocal best_r, best
        key: tuple[str, str, frozenset[str]] = (
            symbol,
            tf,
            frozenset({primary_strat, co_strat}),
        )
        row = combo_lookup.get(key)
        if row is None or row["avg_r"] < min_avg_r:
            return
        co_idx = time_to_idx.get(co_open_time, -1)
        if co_idx < 0:
            return
        candles_ago = current_idx - co_idx
        if candles_ago < 0 or candles_ago > window:
            return
        if row["avg_r"] <= best_r:
            return
        co_spec = STRATEGY_REGISTRY.get(co_strat)
        primary_spec = STRATEGY_REGISTRY.get(primary_strat)
        best_r = row["avg_r"]
        best = ConfluenceData(
            co_strategy=co_strat,
            candles_ago=candles_ago,
            avg_r=row["avg_r"],
            trades=int(row["closed_trades"]),
            win_rate=float(row["win_rate"]),
            type_a=co_spec.strategy_type if co_spec else "",
            type_b=primary_spec.strategy_type if primary_spec else "",
        )

    # 1. Same-cycle: pairs within dir_events (all at current_open_time).
    strat_list = list(current_strats)
    for i, sa in enumerate(strat_list):
        for sb in strat_list[i + 1 :]:
            _consider(sa, sb, current_open_time)

    # 2. Cross-cycle: query DB signals within the window.
    candle_ms = parse_timeframe_secs(tf) * 1000
    window_start_ms = current_open_time - window * candle_ms
    # Exclude the current candle (already covered by same-cycle check above).
    window_end_ms = current_open_time - candle_ms
    if window_end_ms >= window_start_ms:
        try:
            hist = get_signals_history(conn, symbol, tf, window_start_ms, window_end_ms)
        except Exception:
            hist = pd.DataFrame()
        if not hist.empty:
            for _, db_row in hist.iterrows():
                hist_strategy = str(db_row["strategy"])
                if str(db_row["direction"]) != current_direction:
                    continue
                hist_open_time = int(db_row["open_time"])
                for current_strat in current_strats:
                    if hist_strategy == current_strat:
                        continue
                    _consider(current_strat, hist_strategy, hist_open_time)

    return best


def _parse_htf_ltf_pairs(
    cross_tf_pairs: list[str],
) -> list[tuple[str, str]]:
    """Parse ["4h:15m", "4h:1h"] → [("4h", "15m"), ("4h", "1h")]."""
    result: list[tuple[str, str]] = []
    for entry in cross_tf_pairs:
        parts = entry.split(":")
        if len(parts) == 2:
            result.append((parts[0].strip(), parts[1].strip()))
    return result


def _find_cross_tf_cofire(
    events: list["SignalEvent"],
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    tf_ltf: str,
    cross_tf_lookup: "dict[tuple[str, str, str, str, str], Any]",
    cross_tf_pairs: list[tuple[str, str]],
    window_hours: float,
    min_avg_r: float,
) -> "ConfluenceData | None":
    """Return ConfluenceData for the best cross-TF co-firing pair, or None.

    For each (tf_htf, tf_ltf) pair where tf_ltf matches the current TF:
    1. Query the signals DB for recent HTF signals in the same direction.
    2. Look up (symbol, tf_htf, tf_ltf, strategy_htf, strategy_ltf) in the lookup.
    3. Return the best match (highest avg_r ≥ min_avg_r).

    candles_ago is expressed in LTF candles for display consistency with same-TF.
    """
    from analytics.indicators_lib import STRATEGY_REGISTRY

    if not events or not cross_tf_lookup:
        return None

    current_open_time = int(events[0].open_time)
    current_direction = events[0].direction
    current_strats = {e.strategy for e in events}

    window_ms = int(window_hours * 3600 * 1000)
    ltf_candle_ms = parse_timeframe_secs(tf_ltf) * 1000

    best_r: float = -1.0
    best: ConfluenceData | None = None

    # Only check pairs where LTF matches current TF.
    relevant_htfs = [htf for htf, ltf in cross_tf_pairs if ltf == tf_ltf]
    if not relevant_htfs:
        return None

    for tf_htf in relevant_htfs:
        window_start_ms = current_open_time - window_ms
        try:
            hist = get_signals_history(
                conn, symbol, tf_htf, window_start_ms, current_open_time
            )
        except Exception:
            continue
        if hist.empty:
            continue

        for _, db_row in hist.iterrows():
            htf_strat = str(db_row["strategy"])
            if str(db_row["direction"]) != current_direction:
                continue
            htf_open_time = int(db_row["open_time"])

            for ltf_strat in current_strats:
                key: tuple[str, str, str, str, str] = (
                    symbol,
                    tf_htf,
                    tf_ltf,
                    htf_strat,
                    ltf_strat,
                )
                row = cross_tf_lookup.get(key)
                if row is None or row["avg_r"] < min_avg_r:
                    continue
                if row["avg_r"] <= best_r:
                    continue

                # Express candles_ago in LTF candles.
                elapsed_ms = current_open_time - htf_open_time
                candles_ago = max(0, int(elapsed_ms / ltf_candle_ms))

                htf_spec = STRATEGY_REGISTRY.get(htf_strat)
                ltf_spec = STRATEGY_REGISTRY.get(ltf_strat)
                best_r = row["avg_r"]
                best = ConfluenceData(
                    co_strategy=htf_strat,
                    candles_ago=candles_ago,
                    avg_r=row["avg_r"],
                    trades=int(row["closed_trades"]),
                    win_rate=float(row["win_rate"]),
                    type_a=htf_spec.strategy_type if htf_spec else "",
                    type_b=ltf_spec.strategy_type if ltf_spec else "",
                    htf_tf=tf_htf,
                    ltf_tf=tf_ltf,
                )

    return best


def run_scan_cycle(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
    timeframes: list[str],
    strategies: list[str],
    store: CooldownStore,
    tp_r: float = 2.0,
    sl_pct: float = 0.02,
    min_sl_pct: float = 0.0,
    send_telegram: bool = False,
    secondary_map: dict[str, str] | None = None,
    days: int = 90,
    backtest_cfg: BacktestFilterConfig | None = None,
    day_filter: str = "off",
    smt_trend_filter: int = 1,
    strategy_timeframes: dict[str, list[str]] | None = None,
    strategy_params: dict[str, StrategyOverride] | None = None,
    atr_sl_multiplier: float | None = None,
    confidence_override: dict[str, dict[str, int]] | None = None,
    directional_confidence_override: dict[str, dict[str, dict[str, int]]] | None = None,
    bias_cfg: BiasConfig | None = None,
    combo_lookup: "dict[tuple[str, str, frozenset[str]], Any] | None" = None,
    combo_window: int = 5,
    combo_min_avg_r: float = 1.0,
    cross_tf_lookup: "dict[tuple[str, str, str, str, str], Any] | None" = None,
    cross_tf_pairs: list[tuple[str, str]] | None = None,
    cross_tf_window_hours: float = 4.0,
    cross_tf_min_avg_r: float = 1.0,
    ohlcv_cache: "dict[tuple[str, str], pd.DataFrame] | None" = None,
) -> list[str]:
    """Scan all symbol+timeframe combinations and return formatted alert strings.

    Pre-fetches OHLCV once per (symbol, timeframe) and passes the DataFrame into
    scan_symbol, avoiding redundant DB reads across strategies.
    Uses CooldownStore to suppress duplicate alerts. Optionally sends via Telegram.
    Returns list of formatted alert strings for logging/testing regardless of
    whether Telegram is enabled.

    secondary_map: per-symbol mapping of primary → secondary symbol for smt_divergence.
    Secondaries are fetched once per (secondary_symbol, timeframe) even if shared by
    multiple primaries.
    day_filter: "off" | "weekdays" | "tue_thu" — suppress signals by weekday.
    strategy_timeframes: optional per-strategy TF allow-list from [strategy_timeframes] TOML.
    """
    from utils.telegram import send_telegram_message

    now_ms = int(time.time() * 1000)
    if backtest_cfg and backtest_cfg.since:
        import datetime as _dt

        start_ms = int(
            _dt.datetime.strptime(backtest_cfg.since, "%Y-%m-%d")
            .replace(tzinfo=_dt.UTC)
            .timestamp()
            * 1000
        )
    else:
        start_ms = now_ms - days * 24 * 3600 * 1000

    # Freshly computed BacktestResult objects this cycle, keyed by (symbol, tf, strategy).
    # Cache hits (BacktestSnapshot) are excluded — only full BacktestResult objects
    # can be persisted to backtest_runs at end of cycle.
    bt_to_save: dict[tuple[str, str, str], BacktestResult | None] = {}

    # Per-cycle stats context cache: symbol → StatsContext | None
    # Computed once per symbol (not per TF) to avoid redundant DB queries.
    stats_ctx_cache: dict[str, StatsContext | None] = {}
    now_myt = datetime.datetime.now(tz=datetime.timezone(datetime.timedelta(hours=8)))

    needs_funding = any(
        STRATEGY_REGISTRY[s].requires_funding
        for s in strategies
        if s in SIGNAL_REGISTRY and s in STRATEGY_REGISTRY
    )
    needs_secondary = any(
        STRATEGY_REGISTRY[s].requires_secondary
        for s in strategies
        if s in SIGNAL_REGISTRY and s in STRATEGY_REGISTRY
    )

    # Pre-fetch secondary OHLCV keyed by (secondary_symbol, tf) to avoid duplicate
    # DB queries when multiple primaries share the same secondary.
    # Uses ohlcv_cache when available (daemon hot path) to avoid full DB reads.
    secondary_dfs: dict[tuple[str, str], pd.DataFrame] = {}
    if needs_secondary and secondary_map:
        for symbol in symbols:
            sec = secondary_map.get(symbol)
            if sec:
                for tf in timeframes:
                    key = (sec, tf)
                    if key not in secondary_dfs:
                        if ohlcv_cache and key in ohlcv_cache:
                            secondary_dfs[key] = ohlcv_cache[key]
                        else:
                            secondary_dfs[key] = get_ohlcv(
                                conn, sec, tf, start_ms, now_ms
                            )

    alerts: list[str] = []

    # --- Phase 1: Pre-fetch all DB data sequentially ---
    # Funding and stats are per-symbol; OHLCV is per (symbol, tf).
    # Isolating all DB reads before the parallel scan phase ensures no DuckDB
    # connection is accessed from multiple threads simultaneously.
    funding_map: dict[str, pd.DataFrame | None] = {}
    ohlcv_map: dict[tuple[str, str], pd.DataFrame] = {}
    for symbol in symbols:
        funding_map[symbol] = (
            get_funding_rates(conn, symbol, start_ms, now_ms) if needs_funding else None
        )
        if symbol not in stats_ctx_cache:
            stats_ctx_cache[symbol] = _compute_stats_context(conn, symbol, now_myt)
        for tf in timeframes:
            _key = (symbol, tf)
            ohlcv_map[_key] = (
                ohlcv_cache[_key]
                if ohlcv_cache and _key in ohlcv_cache
                else get_ohlcv(conn, symbol, tf, start_ms, now_ms)
            )

    # --- Phase 2: Fan-out scan_symbol via ThreadPoolExecutor ---
    # scan_symbol is pure Python/pandas — no DB access, no shared mutable state.
    # pandas rolling/shift/boolean masks release the GIL → real concurrency.
    # Closures are safe here: ThreadPoolExecutor shares memory, no pickling needed.
    def _scan_task(_sym: str, _tf: str) -> "tuple[str, str, list[SignalEvent], Any]":
        _ohlcv = ohlcv_map[(_sym, _tf)]
        _sec_key = ((secondary_map or {}).get(_sym, ""), _tf)
        _sec = secondary_dfs.get(_sec_key) if needs_secondary else None
        _funding = funding_map.get(_sym)
        _gap = get_recent_cme_gap(_ohlcv)
        # Slice to _SCAN_WINDOW for detectors — they only need recent candles
        # (max lookback = 100). Full window stays in ohlcv_map for Phase 3 backtest.
        _ohlcv_scan = (
            _ohlcv.iloc[-_SCAN_WINDOW:] if len(_ohlcv) > _SCAN_WINDOW else _ohlcv
        )
        _sec_scan = (
            _sec.iloc[-_SCAN_WINDOW:]
            if _sec is not None and len(_sec) > _SCAN_WINDOW
            else _sec
        )
        _events = scan_symbol(
            ohlcv_df=_ohlcv_scan,
            symbol=_sym,
            timeframe=_tf,
            strategies=strategies,
            secondary_df=_sec_scan,
            funding_df=_funding,
            day_filter=day_filter,
            smt_trend_filter=smt_trend_filter,
            strategy_timeframes=strategy_timeframes,
            confidence_override=confidence_override,
            directional_confidence_override=directional_confidence_override,
        )
        return _sym, _tf, _events, _gap

    _pairs = [(sym, tf) for sym in symbols for tf in timeframes]
    _n_workers = max(1, min((os.cpu_count() or 2) - 1, len(_pairs)))
    scan_results: list[Any] = []
    if _n_workers > 1 and len(_pairs) > 1:
        with ThreadPoolExecutor(max_workers=_n_workers) as _pool:
            _futs = {_pool.submit(_scan_task, sym, tf): (sym, tf) for sym, tf in _pairs}
            for _fut in as_completed(_futs):
                scan_results.append(_fut.result())
        # Sort HTF before LTF so Phase 3 writes HTF signals to DB first.
        # Cross-TF co-fire checks query the DB — if LTF is processed first,
        # the HTF signal from the same cycle isn't in DB yet and confluence
        # is silently missed.
        _sym_idx = {s: i for i, s in enumerate(symbols)}
        _tf_idx = {t: i for i, t in enumerate(timeframes)}
        scan_results.sort(key=lambda r: (_sym_idx[r[0]], -_tf_idx[r[1]]))
    else:
        # Single-worker: scan pairs in HTF-first order for the same reason.
        _tf_idx_single = {t: i for i, t in enumerate(timeframes)}
        _pairs_htf_first = sorted(
            _pairs, key=lambda p: (symbols.index(p[0]), -_tf_idx_single[p[1]])
        )
        for sym, tf in _pairs_htf_first:
            scan_results.append(_scan_task(sym, tf))

    # --- Phase 3: Fan-in — sequential processing of scan results ---
    # All shared-state operations happen here: CooldownStore reads/writes,
    # bt_cache updates, DB writes (upsert_signals, upsert_backtest_run).
    for symbol, tf, events, cme_gap in scan_results:
        ohlcv_df = ohlcv_map[(symbol, tf)]
        sec_key = ((secondary_map or {}).get(symbol, ""), tf)
        sec_df = secondary_dfs.get(sec_key) if needs_secondary else None
        funding_df = funding_map.get(symbol)

        # Conflict resolution: opposite directions on same symbol/tf
        # Pick the side with higher max confidence; on a tie, send both sides
        # (each signal's reason will have "⚠️ conflict" appended).
        long_events = [e for e in events if e.direction == "long"]
        short_events = [e for e in events if e.direction == "short"]
        if long_events and short_events:
            long_conf = max(e.confidence for e in long_events)
            short_conf = max(e.confidence for e in short_events)
            if long_conf > short_conf:
                direction_events = long_events
                logger.info(
                    "Conflict: %s %s — LONG wins (conf %d > %d), SHORT dropped (%s)",
                    symbol,
                    tf,
                    long_conf,
                    short_conf,
                    [e.strategy for e in short_events],
                )
            elif short_conf > long_conf:
                direction_events = short_events
                logger.info(
                    "Conflict: %s %s — SHORT wins (conf %d > %d), LONG dropped (%s)",
                    symbol,
                    tf,
                    short_conf,
                    long_conf,
                    [e.strategy for e in long_events],
                )
            else:
                direction_events = long_events + short_events
                logger.info(
                    "Conflict tie: %s %s conf %d — sending both LONG (%s) and SHORT (%s)",
                    symbol,
                    tf,
                    long_conf,
                    [e.strategy for e in long_events],
                    [e.strategy for e in short_events],
                )
            for e in direction_events:
                e.conflict = True
        else:
            direction_events = long_events or short_events
        if not direction_events:
            continue

        # Filter each strategy independently by candle watermark
        passing_events = [
            e
            for e in direction_events
            if store.is_new_candle(symbol, tf, e.strategy, e.open_time)
        ]
        if not passing_events:
            continue

        # Backtest filter — L1 (module dict) → L2 (DuckDB) → full compute.
        # Per-strategy tp_r/sl_pct overrides are applied here so the filter
        # uses the same parameters the strategy was calibrated against.
        bt_results: dict[str, BacktestResult | BacktestSnapshot | None] = {}
        if backtest_cfg and backtest_cfg.mode != "off":
            for event in passing_events:
                bt_key = (symbol, tf, event.strategy)
                eff_tp_r = _resolve_tp_r(
                    strategy_params, event.strategy, symbol, tf, tp_r
                )
                eff_sl_pct = _resolve_sl_pct(
                    strategy_params, event.strategy, symbol, tf, sl_pct
                )
                eff_atr_sl = _resolve_atr_sl_multiplier(
                    strategy_params,
                    event.strategy,
                    symbol,
                    tf,
                    atr_sl_multiplier,
                )
                _tp_r_long = _resolve_tp_r(
                    strategy_params, event.strategy, symbol, tf, tp_r, "long"
                )
                _tp_r_short = _resolve_tp_r(
                    strategy_params, event.strategy, symbol, tf, tp_r, "short"
                )
                tp_r_long_eff = _tp_r_long if _tp_r_long != eff_tp_r else None
                tp_r_short_eff = _tp_r_short if _tp_r_short != eff_tp_r else None
                eff_vs_long = _resolve_volume_suppress_long(
                    strategy_params, event.strategy
                )
                eff_vs_short = _resolve_volume_suppress_short(
                    strategy_params, event.strategy
                )
                eff_vsb_long = _resolve_volume_spike_boost_long(
                    strategy_params, event.strategy
                )
                eff_vsb_short = _resolve_volume_spike_boost_short(
                    strategy_params, event.strategy
                )
                eff_adr_exempt = _is_adr_exempt(strategy_params, event.strategy)
                eff_vs = _resolve_volume_suppress(
                    strategy_params, event.strategy, backtest_cfg.volume_suppress
                )
                eff_vsb = _resolve_volume_spike_boost(
                    strategy_params, event.strategy, backtest_cfg.volume_spike_boost
                )
                secondary_sym = (
                    (secondary_map or {}).get(symbol)
                    if event.strategy == "smt_divergence"
                    else None
                )

                if backtest_cfg.cache_enabled:
                    run_id = _backtest_run_id(
                        symbol,
                        tf,
                        event.strategy,
                        days,
                        eff_sl_pct,
                        eff_tp_r,
                        backtest_cfg.fee_pct,
                        day_filter,
                        smt_trend_filter,
                        secondary_sym,
                        bias_cfg.adr_suppress_threshold if bias_cfg else None,
                        eff_vs or None,
                        backtest_cfg.min_sl_pct,
                        eff_atr_sl,
                        tp_r_long_eff,
                        tp_r_short_eff,
                        eff_vs_long or None,
                        eff_vs_short or None,
                        eff_vsb_long or None,
                        eff_vsb_short or None,
                        eff_adr_exempt,
                    )
                    last_candle_ts = int(ohlcv_df["open_time"].iloc[-2])
                    cache_key = _make_bt_cache_key(run_id, last_candle_ts)

                    if cache_key in _bt_mem_cache:
                        bt_result: BacktestResult | BacktestSnapshot | None = (
                            _bt_mem_cache[cache_key]
                        )
                    else:
                        bt_result = get_backtest_cache(conn, cache_key)
                        if bt_result is None:
                            bt_result = _compute_backtest(
                                ohlcv_df=ohlcv_df,
                                strategy=event.strategy,
                                secondary_df=sec_df,
                                funding_df=funding_df,
                                symbol=symbol,
                                timeframe=tf,
                                sl_pct=eff_sl_pct,
                                tp_r=eff_tp_r,
                                fee_pct=backtest_cfg.fee_pct,
                                day_filter=day_filter,
                                min_sl_pct=backtest_cfg.min_sl_pct,
                                atr_sl_multiplier=eff_atr_sl,
                                adr_suppress_threshold=bias_cfg.adr_suppress_threshold
                                if bias_cfg
                                else None,
                                adr_exempt=eff_adr_exempt,
                                volume_suppress=eff_vs,
                                volume_spike_boost=eff_vsb,
                                volume_suppress_long=eff_vs_long,
                                volume_suppress_short=eff_vs_short,
                                volume_spike_boost_long=eff_vsb_long,
                                volume_spike_boost_short=eff_vsb_short,
                                tp_r_long=tp_r_long_eff,
                                tp_r_short=tp_r_short_eff,
                            )
                            if bt_result is not None:
                                put_backtest_cache(
                                    conn,
                                    cache_key,
                                    run_id,
                                    last_candle_ts,
                                    bt_result,
                                )
                                bt_to_save[bt_key] = bt_result
                        _bt_mem_cache[cache_key] = bt_result
                else:
                    bt_result = _compute_backtest(
                        ohlcv_df=ohlcv_df,
                        strategy=event.strategy,
                        secondary_df=sec_df,
                        funding_df=funding_df,
                        symbol=symbol,
                        timeframe=tf,
                        sl_pct=eff_sl_pct,
                        tp_r=eff_tp_r,
                        fee_pct=backtest_cfg.fee_pct,
                        day_filter=day_filter,
                        min_sl_pct=backtest_cfg.min_sl_pct,
                        atr_sl_multiplier=eff_atr_sl,
                        adr_suppress_threshold=bias_cfg.adr_suppress_threshold
                        if bias_cfg
                        else None,
                        adr_exempt=eff_adr_exempt,
                        volume_suppress=eff_vs,
                        volume_spike_boost=eff_vsb,
                        volume_suppress_long=eff_vs_long,
                        volume_suppress_short=eff_vs_short,
                        volume_spike_boost_long=eff_vsb_long,
                        volume_spike_boost_short=eff_vsb_short,
                        tp_r_long=tp_r_long_eff,
                        tp_r_short=tp_r_short_eff,
                    )
                    bt_to_save[bt_key] = bt_result
                bt_results[event.strategy] = bt_result

            if backtest_cfg.mode == "hard":

                def _passes_ev_gate(e: SignalEvent) -> bool:
                    result = bt_results.get(e.strategy)  # noqa: B023 — called inline below
                    if result is None:
                        return True  # no data — don't suppress
                    if len(result.closed_trades) < backtest_cfg.effective_min_trades(
                        tf  # noqa: B023 — called inline below
                    ):
                        return True  # not enough trades — noise
                    if e.direction == "long":
                        avg_r = result.long_avg_r
                        threshold = (
                            backtest_cfg.min_avg_r_long
                            if backtest_cfg.min_avg_r_long is not None
                            else backtest_cfg.min_avg_r
                        )
                    elif e.direction == "short":
                        avg_r = result.short_avg_r
                        threshold = (
                            backtest_cfg.min_avg_r_short
                            if backtest_cfg.min_avg_r_short is not None
                            else backtest_cfg.min_avg_r
                        )
                    else:
                        avg_r = result.avg_r
                        threshold = backtest_cfg.min_avg_r
                    if avg_r is None:
                        return True  # no directional data — don't suppress
                    return avg_r >= threshold

                passing_events = [e for e in passing_events if _passes_ev_gate(e)]
                if not passing_events:
                    logger.info("Backtest hard filter suppressed %s %s", symbol, tf)
                    continue

        # Volume gate — per-strategy, handles both suppression and spike tagging.
        # Suppression: drop low-volume signal candles when the strategy has
        #   volume_suppress enabled. Exception: spike candles bypass suppression
        #   when volume_spike_boost is on (spike > 3× rolling mean = conviction).
        # Spike tagging: always tag SignalEvent.volume_spike regardless of suppress
        #   so alert_formatter can show ⚡ even when suppression is off.
        if backtest_cfg:
            vol_time_to_idx: dict[int, int] | None = None
            vol_filtered: list[SignalEvent] = []
            for _e in passing_events:
                if vol_time_to_idx is None:
                    vol_time_to_idx = {
                        int(t): i
                        for i, t in enumerate(ohlcv_df["open_time"].astype("int64"))
                    }
                _idx = vol_time_to_idx.get(int(_e.open_time), 0)
                _is_spike = _is_volume_spike(ohlcv_df, _idx)
                if _is_spike:
                    _e.volume_spike = True
                # Direction-aware suppress: directional fields take precedence over symmetric.
                _dir = _e.direction
                _suppress_long = _resolve_volume_suppress_long(
                    strategy_params, _e.strategy
                )
                _suppress_short = _resolve_volume_suppress_short(
                    strategy_params, _e.strategy
                )
                _suppress = (
                    _suppress_long
                    if _dir == "long" and _suppress_long is not None
                    else _suppress_short
                    if _dir == "short" and _suppress_short is not None
                    else _resolve_volume_suppress(
                        strategy_params, _e.strategy, backtest_cfg.volume_suppress
                    )
                )
                _boost_long = _resolve_volume_spike_boost_long(
                    strategy_params, _e.strategy
                )
                _boost_short = _resolve_volume_spike_boost_short(
                    strategy_params, _e.strategy
                )
                _boost = (
                    _boost_long
                    if _dir == "long" and _boost_long is not None
                    else _boost_short
                    if _dir == "short" and _boost_short is not None
                    else _resolve_volume_spike_boost(
                        strategy_params,
                        _e.strategy,
                        backtest_cfg.volume_spike_boost,
                    )
                )
                if _suppress and _is_low_volume(ohlcv_df, _idx):
                    # Spike boost: exempt high-conviction candles from suppress.
                    if _is_spike and _boost:
                        logger.info(
                            "Volume spike exempted %s %s — %s %s",
                            symbol,
                            tf,
                            _e.direction.upper(),
                            _e.strategy,
                        )
                    else:
                        logger.info(
                            "Volume filter suppressed %s %s — %s %s",
                            symbol,
                            tf,
                            _e.direction.upper(),
                            _e.strategy,
                        )
                        continue
                vol_filtered.append(_e)
            passing_events = vol_filtered
            if not passing_events:
                continue

        # Bias gate — ADR progress and DOW context filters (F8).
        # Never raises — stats failures must not block signal dispatch.
        if bias_cfg is not None:
            bias_ctx = stats_ctx_cache.get(symbol)
            if bias_ctx is not None:
                # Step 1: ADR directional suppress — remove signals chasing the
                # already-consumed direction. LONGs suppressed when move was up
                # (price near day high); SHORTs when move was down. Reversal signals
                # in the opposing direction are kept — fading an extreme is valid
                # even when ADR is consumed. Falls back to blanket suppress when
                # move direction is unknown.
                if (
                    bias_cfg.adr_suppress_threshold is not None
                    and bias_ctx.adr_consumed_pct is not None
                    and bias_ctx.adr_consumed_pct >= bias_cfg.adr_suppress_threshold
                ):
                    if bias_ctx.adr_move_up is None:
                        logger.info(
                            "ADR bias gate suppressed %s %s — %.0f%% consumed "
                            "(direction unknown)",
                            symbol,
                            tf,
                            bias_ctx.adr_consumed_pct * 100,
                        )
                        continue
                    suppress_dir = "long" if bias_ctx.adr_move_up else "short"
                    n_before = len(passing_events)
                    passing_events = [
                        e
                        for e in passing_events
                        if e.direction != suppress_dir
                        or _is_adr_exempt(strategy_params, e.strategy)
                    ]
                    if len(passing_events) < n_before:
                        logger.info(
                            "ADR bias gate removed %d %s signal(s) for %s %s "
                            "— %.0f%% consumed, chasing %s",
                            n_before - len(passing_events),
                            suppress_dir,
                            symbol,
                            tf,
                            bias_ctx.adr_consumed_pct * 100,
                            suppress_dir,
                        )
                    if not passing_events:
                        continue

                # Step 2: DOW soft suppress — reduce confidence by 1 star
                # when signal direction opposes today's historical avg return.
                if bias_cfg.dow_soft_suppress:
                    avg_ret = bias_ctx.avg_return_today
                    if abs(avg_ret) >= bias_cfg.dow_suppress_min_abs_return:
                        for event in passing_events:
                            if (event.direction == "long" and avg_ret < 0) or (
                                event.direction == "short" and avg_ret > 0
                            ):
                                event.confidence = max(1, event.confidence - 1)
                                logger.debug(
                                    "DOW bias: %s %s %s confidence → %d (avg_ret %.3f)",
                                    symbol,
                                    tf,
                                    event.strategy,
                                    event.confidence,
                                    avg_ret,
                                )

        for event in passing_events:
            store.mark_candle(symbol, tf, event.strategy, event.open_time)

        # Persist passing signals to DB so the Signal Feed can read from DB
        # instead of re-scanning on every page load.
        now_fired_ms = int(time.time() * 1000)
        signals_rows = [
            {
                "symbol": e.symbol,
                "timeframe": e.timeframe,
                "strategy": e.strategy,
                "open_time": e.open_time,
                "direction": e.direction,
                "entry_price": e.price,
                "sl_price": e.sl_price,
                "reason": e.reason,
                "confidence": e.confidence,
                "fired_at": now_fired_ms,
            }
            for e in passing_events
        ]
        signals_df = pd.DataFrame(signals_rows)
        try:
            upsert_signals(conn, signals_df)
        except Exception:
            logger.exception("Failed to persist signals to DB for %s %s", symbol, tf)

        # Persist outcome rows so win/loss can be backfilled later (A4 P1).
        for e in passing_events:
            signal_id = (
                f"{e.symbol}-{e.timeframe}-{e.strategy}-{e.open_time}-{e.direction}"
            )
            try:
                upsert_signal_outcome(
                    conn,
                    {
                        "signal_id": signal_id,
                        "symbol": e.symbol,
                        "tf": e.timeframe,
                        "strategy": e.strategy,
                        "direction": e.direction,
                        "fired_at_ms": now_fired_ms,
                        "candle_ts_ms": e.open_time,
                        "entry_price": e.price,
                        "sl_price": e.sl_price or None,
                        "confidence_at_fire": e.confidence,
                        "tags": e.reason,
                    },
                )
            except Exception:
                logger.exception("Failed to persist signal outcome for %s", signal_id)

        # In a tied conflict, passing_events may contain both directions —
        # split by direction so each confluence alert is direction-homogeneous.
        directions_present = list(dict.fromkeys(e.direction for e in passing_events))
        for direction in directions_present:
            dir_events = [e for e in passing_events if e.direction == direction]
            # Scope backtest summary to this direction's strategies only.
            # Avoids showing SHORT strategy stats on a LONG alert (and vice versa)
            # in tied-conflict scenarios where both directions pass.
            dir_summary: str | None = None
            if backtest_cfg and backtest_cfg.mode != "off" and bt_results:
                dir_summary = _backtest_summary(
                    bt_results,
                    [e.strategy for e in dir_events],
                    backtest_cfg,
                    tf=tf,
                    direction=direction,
                )
            # Resolve effective tp_r for this alert — use the max across all
            # strategies in the confluence group (most optimistic target wins;
            # individual strategies already filtered to have edge at that level).
            # Direction-aware: long uses tp_r_long, short uses tp_r_short when set.
            eff_alert_tp_r = max(
                _resolve_tp_r(strategy_params, e.strategy, symbol, tf, tp_r, direction)
                for e in dir_events
            )
            # Compute CME gap warning for this direction.
            # Rough TP mirrors the formatter's own SL/TP math so the gap
            # overlap check uses the same target price shown in the alert.
            _first = dir_events[0]
            _entry = _first.price
            if direction == "long":
                _valid_sls = [e.sl_price for e in dir_events if 0 < e.sl_price < _entry]
                _sl_dist = _entry - (
                    min(_valid_sls) if _valid_sls else _entry * (1 - sl_pct)
                )
                _sl_dist = max(_sl_dist, _entry * min_sl_pct)
                _rough_tp = (
                    _first.tp_price
                    if _first.tp_price > _entry
                    else _entry + _sl_dist * eff_alert_tp_r
                )
            else:
                _valid_sls = [e.sl_price for e in dir_events if e.sl_price > _entry]
                _sl_dist = (
                    max(_valid_sls) if _valid_sls else _entry * (1 + sl_pct)
                ) - _entry
                _sl_dist = max(_sl_dist, _entry * min_sl_pct)
                _rough_tp = (
                    _first.tp_price
                    if 0 < _first.tp_price < _entry
                    else _entry - _sl_dist * eff_alert_tp_r
                )
            _gap_warning = cme_gap_alert_warning(cme_gap, direction, _entry, _rough_tp)

            # Co-fire confluence tagging (D10 step 3): check if a known-good
            # strategy pair from backtest_combos co-fired within combo_window
            # candles. Attaches ConfluenceData to each event so the formatter
            # can append the blockquote section.
            # Same-TF confluence (step 3): check known-good same-TF pairs.
            _best_cofire: ConfluenceData | None = None
            if combo_lookup:
                _same_tf = _find_live_cofire(
                    dir_events,
                    ohlcv_df,
                    conn,
                    combo_lookup,
                    symbol,
                    tf,
                    combo_window,
                    combo_min_avg_r,
                )
                if _same_tf is not None:
                    _best_cofire = _same_tf

            # Cross-TF confluence (step 4): HTF context + LTF entry.
            if cross_tf_lookup and cross_tf_pairs:
                _cross = _find_cross_tf_cofire(
                    dir_events,
                    conn,
                    symbol,
                    tf,
                    cross_tf_lookup,
                    cross_tf_pairs,
                    cross_tf_window_hours,
                    cross_tf_min_avg_r,
                )
                # Tag whichever has the higher avg_r.
                if _cross is not None and (
                    _best_cofire is None or _cross.avg_r > _best_cofire.avg_r
                ):
                    _best_cofire = _cross

            if _best_cofire is not None:
                for _e in dir_events:
                    _e.confluence_combo = _best_cofire
                _tf_label = (
                    f"{_best_cofire.htf_tf}→{_best_cofire.ltf_tf}"
                    if _best_cofire.htf_tf
                    else tf
                )
                logger.info(
                    "Co-fire: %s %s %s+%s [%s] (avg_r %.2f, %d candles ago)",
                    symbol,
                    tf,
                    dir_events[0].strategy,
                    _best_cofire.co_strategy,
                    _tf_label,
                    _best_cofire.avg_r,
                    _best_cofire.candles_ago,
                )

            # Stack all passing strategies into one confluence alert
            msg = format_confluence_alert(
                dir_events,
                sl_pct=sl_pct,
                tp_r=eff_alert_tp_r,
                min_sl_pct=min_sl_pct,
                backtest_summary=dir_summary,
                stats_context=stats_ctx_cache.get(symbol),
                cme_gap_warning=_gap_warning,
                ohlcv_df=ohlcv_df,
            )
            alerts.append(msg)
            logger.info(
                "Signal: %s %s %s %s (confluence: %d)",
                symbol,
                tf,
                direction,
                [e.strategy for e in dir_events],
                len(dir_events),
            )

            if send_telegram:
                try:
                    send_telegram_message(msg)
                except Exception:
                    logger.exception("Telegram send failed for %s", symbol)
    # Persist freshly computed backtest results to backtest_runs so win-rate data
    # accumulates passively. Cache hits (BacktestSnapshot) are excluded — only
    # full BacktestResult objects land here. Covers combos that fired this cycle.
    if backtest_cfg and backtest_cfg.save_results and bt_to_save:
        for (sym, tf, strategy), bt_result in bt_to_save.items():
            if bt_result is None:
                continue
            secondary_symbol = (
                (secondary_map or {}).get(sym) if strategy == "smt_divergence" else None
            )
            try:
                upsert_backtest_run(
                    conn,
                    bt_result,
                    days=backtest_cfg.days,
                    data_start_ms=start_ms,
                    data_end_ms=now_ms,
                    sl_pct=_resolve_sl_pct(strategy_params, strategy, sym, tf, sl_pct),
                    tp_r=_resolve_tp_r(strategy_params, strategy, sym, tf, tp_r),
                    fee_pct=backtest_cfg.fee_pct,
                    day_filter=day_filter,
                    smt_trend_filter=smt_trend_filter,
                    secondary_symbol=secondary_symbol,
                    adr_suppress_threshold=bias_cfg.adr_suppress_threshold
                    if bias_cfg
                    else None,
                    volume_suppress=_resolve_volume_suppress(
                        strategy_params, strategy, backtest_cfg.volume_suppress
                    )
                    or None,
                )
            except Exception:
                logger.exception(
                    "Failed to persist backtest run for %s %s %s", sym, tf, strategy
                )

    return alerts

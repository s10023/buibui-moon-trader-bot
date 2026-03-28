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
import time
from collections.abc import Mapping

import duckdb
import pandas as pd

from analytics.backtest_lib import (
    BacktestResult,
    _is_low_volume,
    filter_signals_by_day,
    run_backtest,
)
from analytics.data_store import (
    get_funding_rates,
    get_ohlcv,
    upsert_backtest_run,
    upsert_signal_outcome,
    upsert_signals,
)
from analytics.indicators_lib import STRATEGY_REGISTRY
from analytics.signal_config import (
    BacktestFilterConfig,
    StrategyOverride,
    _day_filter_to_weekdays,
)
from signals.alert_formatter import SignalEvent, StatsContext, format_confluence_alert
from signals.cooldown_store import CooldownStore
from signals.registry import SIGNAL_REGISTRY

logger = logging.getLogger(__name__)

_CANDLE_CLOSE_BUFFER_SECS = 10


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
) -> BacktestResult | None:
    """Run strategy detector on ohlcv[:-1] and backtest the resulting signals.

    Excludes the current (latest) candle to avoid lookahead bias.
    Returns None if there is insufficient data or the detector raises.
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
    )


def _backtest_summary(
    results: Mapping[str, BacktestResult | None],
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
        hold_suffix = (
            f" · hold {_fmt_hold(dir_median_h)}"
            if (single and dir_median_h is not None)
            else ""
        )
        if n < min_trades or dir_win_rate is None:
            label = f"n/a ({n} {trade_noun}s)" if single else f"{s}: n/a ({n})"
        else:
            pct = f"{dir_win_rate:.0%}"
            if dir_avg_r is not None:
                avg_r_str = f"{dir_avg_r:+.1f}R"
                label = (
                    f"{pct} win · avg {avg_r_str} ({n} {trade_noun}s){hold_suffix}"
                    if single
                    else f"{s}: {pct}·{avg_r_str} ({n})"
                )
            else:
                label = (
                    f"{pct} win ({n} {trade_noun}s){hold_suffix}"
                    if single
                    else f"{s}: {pct} ({n})"
                )
        parts.append(label)

    body = " · ".join(parts)
    return f"📊 Backtest {cfg.days}d{arrow}: {body}"


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

    for strategy_name in strategies:
        plugin = SIGNAL_REGISTRY.get(strategy_name)
        if plugin is None:
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
                    context=str(row["context"]),
                    confidence=SIGNAL_REGISTRY[strategy_name]["confidence"],
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
    tf: str,
    global_tp_r: float,
) -> float:
    """Resolve effective tp_r: TF-specific → strategy-wide → global fallback."""
    if not strategy_params:
        return global_tp_r
    override = strategy_params.get(strategy)
    if override is None:
        return global_tp_r
    if tf in override.tp_r_per_tf:
        return override.tp_r_per_tf[tf]
    if override.tp_r is not None:
        return override.tp_r
    return global_tp_r


def _resolve_sl_pct(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
    tf: str,
    global_sl_pct: float,
) -> float:
    """Resolve effective sl_pct: TF-specific → strategy-wide → global fallback."""
    if not strategy_params:
        return global_sl_pct
    override = strategy_params.get(strategy)
    if override is None:
        return global_sl_pct
    if tf in override.sl_pct_per_tf:
        return override.sl_pct_per_tf[tf]
    if override.sl_pct is not None:
        return override.sl_pct
    return global_sl_pct


def _resolve_atr_sl_multiplier(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
    tf: str,
    global_atr_sl: float | None,
) -> float | None:
    """Resolve effective atr_sl_multiplier: TF-specific → strategy-wide → global fallback."""
    if not strategy_params:
        return global_atr_sl
    override = strategy_params.get(strategy)
    if override is None:
        return global_atr_sl
    if tf in override.atr_sl_multiplier_per_tf:
        return override.atr_sl_multiplier_per_tf[tf]
    if override.atr_sl_multiplier is not None:
        return override.atr_sl_multiplier
    return global_atr_sl


def _compute_stats_context(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    now_myt: datetime.datetime,
) -> StatsContext | None:
    """Return a StatsContext for the current symbol/DOW, or None on any error.

    Never raises — stats failure must never block signal dispatch.
    """
    try:
        from analytics.stats_lib import compute_all

        bundle = compute_all(conn, symbol, days=90)
        dow_full = now_myt.strftime("%A")  # e.g. "Thursday"
        dow_short = dow_full[:3]  # e.g. "Thu"

        # P1=Low % for today's DOW
        p1_low_today = bundle.p1p2.by_dow.get(dow_short, bundle.p1p2.overall_p1_low_pct)

        # Today's ADR consumed
        adr_consumed = bundle.adr.today_consumed_pct

        return StatsContext(
            today_dow=dow_full,
            p1_low_pct_today=p1_low_today,
            adr_14=bundle.adr.adr_14,
            adr_consumed_pct=adr_consumed,
            peak_high_hour_myt=bundle.hourly.peak_high_hour,
            peak_low_hour_myt=bundle.hourly.peak_low_hour,
        )
    except Exception:
        logger.debug("_compute_stats_context failed for %s — skipping", symbol)
        return None


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
    start_ms = now_ms - days * 24 * 3600 * 1000

    # Per-cycle backtest cache: (symbol, tf, strategy) → BacktestResult | None
    # Avoids recomputing the same strategy twice if it fires on multiple symbols.
    bt_cache: dict[tuple[str, str, str], BacktestResult | None] = {}

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
    secondary_dfs: dict[tuple[str, str], pd.DataFrame] = {}
    if needs_secondary and secondary_map:
        for symbol in symbols:
            sec = secondary_map.get(symbol)
            if sec:
                for tf in timeframes:
                    key = (sec, tf)
                    if key not in secondary_dfs:
                        secondary_dfs[key] = get_ohlcv(conn, sec, tf, start_ms, now_ms)

    alerts: list[str] = []

    for symbol in symbols:
        funding_df: pd.DataFrame | None = None
        if needs_funding:
            funding_df = get_funding_rates(conn, symbol, start_ms, now_ms)

        # Compute stats context once per symbol (cached within the cycle)
        if symbol not in stats_ctx_cache:
            stats_ctx_cache[symbol] = _compute_stats_context(conn, symbol, now_myt)

        for tf in timeframes:
            ohlcv_df = get_ohlcv(conn, symbol, tf, start_ms, now_ms)
            sec_key = ((secondary_map or {}).get(symbol, ""), tf)
            sec_df = secondary_dfs.get(sec_key) if needs_secondary else None

            events = scan_symbol(
                ohlcv_df=ohlcv_df,
                symbol=symbol,
                timeframe=tf,
                strategies=strategies,
                secondary_df=sec_df,
                funding_df=funding_df,
                day_filter=day_filter,
                smt_trend_filter=smt_trend_filter,
                strategy_timeframes=strategy_timeframes,
            )

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

            # Backtest filter — runs per strategy, caches results within the cycle.
            # Per-strategy tp_r/sl_pct overrides are applied here so the filter
            # uses the same parameters the strategy was calibrated against.
            bt_results: dict[str, BacktestResult | None] = {}
            if backtest_cfg and backtest_cfg.mode != "off":
                for event in passing_events:
                    bt_key = (symbol, tf, event.strategy)
                    if bt_key not in bt_cache:
                        eff_tp_r = _resolve_tp_r(
                            strategy_params, event.strategy, tf, tp_r
                        )
                        eff_sl_pct = _resolve_sl_pct(
                            strategy_params, event.strategy, tf, sl_pct
                        )
                        eff_atr_sl = _resolve_atr_sl_multiplier(
                            strategy_params, event.strategy, tf, atr_sl_multiplier
                        )
                        bt_cache[bt_key] = _compute_backtest(
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
                        )
                    bt_results[event.strategy] = bt_cache[bt_key]

                if backtest_cfg.mode == "hard":
                    passing_events = [
                        e
                        for e in passing_events
                        if (
                            bt_results.get(e.strategy) is None
                            or len(bt_results[e.strategy].closed_trades)  # type: ignore[union-attr]
                            < backtest_cfg.effective_min_trades(tf)
                            or bt_results[e.strategy].win_rate  # type: ignore[union-attr]
                            >= backtest_cfg.filter_threshold
                        )
                    ]
                    if not passing_events:
                        logger.info("Backtest hard filter suppressed %s %s", symbol, tf)
                        continue

            # Volume suppression gate — drop low-volume signals when enabled.
            # Enable [backtest].volume_suppress after confirming via sweep that
            # low-vol trades underperform (run `make buibui-backtest` and read the
            # Volume Impact table).
            if backtest_cfg and backtest_cfg.volume_suppress:
                time_to_idx = {
                    int(t): i
                    for i, t in enumerate(ohlcv_df["open_time"].astype("int64"))
                }
                passing_events = [
                    e
                    for e in passing_events
                    if not _is_low_volume(
                        ohlcv_df, time_to_idx.get(int(e.open_time), 0)
                    )
                ]
                if not passing_events:
                    logger.info("Volume filter suppressed %s %s", symbol, tf)
                    continue

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
                logger.exception(
                    "Failed to persist signals to DB for %s %s", symbol, tf
                )

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
                    logger.exception(
                        "Failed to persist signal outcome for %s", signal_id
                    )

            # In a tied conflict, passing_events may contain both directions —
            # split by direction so each confluence alert is direction-homogeneous.
            directions_present = list(
                dict.fromkeys(e.direction for e in passing_events)
            )
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
                eff_alert_tp_r = max(
                    _resolve_tp_r(strategy_params, e.strategy, tf, tp_r)
                    for e in dir_events
                )
                # Stack all passing strategies into one confluence alert
                msg = format_confluence_alert(
                    dir_events,
                    sl_pct=sl_pct,
                    tp_r=eff_alert_tp_r,
                    min_sl_pct=min_sl_pct,
                    backtest_summary=dir_summary,
                    stats_context=stats_ctx_cache.get(symbol),
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

    # Persist bt_cache results to backtest_runs so win-rate data accumulates
    # passively over time. Only runs if the backtest filter is active and
    # save_results is enabled (default True). Covers only combos that fired a
    # signal this cycle — for a full-sweep snapshot use `buibui backtest --save`.
    if backtest_cfg and backtest_cfg.save_results and bt_cache:
        for (sym, tf, strategy), bt_result in bt_cache.items():
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
                    sl_pct=_resolve_sl_pct(strategy_params, strategy, tf, sl_pct),
                    tp_r=_resolve_tp_r(strategy_params, strategy, tf, tp_r),
                    fee_pct=backtest_cfg.fee_pct,
                    day_filter=day_filter,
                    smt_trend_filter=smt_trend_filter,
                    secondary_symbol=secondary_symbol,
                )
            except Exception:
                logger.exception(
                    "Failed to persist backtest run for %s %s %s", sym, tf, strategy
                )

    return alerts

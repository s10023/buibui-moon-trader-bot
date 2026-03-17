"""Pure signal scanning library.

scan_symbol(): runs requested strategies against a pre-fetched OHLCV DataFrame,
               returning SignalEvents only for the latest candle.
run_scan_cycle(): fans out across all symbols/timeframes, pre-fetches OHLCV once
                  per (symbol, timeframe), deduplicates via CooldownStore,
                  formats alerts, and optionally sends Telegram.
No module-level side effects.
"""

import logging
import time

import duckdb
import pandas as pd

from analytics.data_store import get_funding_rates, get_ohlcv
from analytics.indicators_lib import STRATEGY_REGISTRY
from signals.alert_formatter import SignalEvent, format_confluence_alert
from signals.cooldown_store import CooldownStore
from signals.registry import SIGNAL_REGISTRY

logger = logging.getLogger(__name__)


def scan_symbol(
    ohlcv_df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    strategies: list[str],
    secondary_df: pd.DataFrame | None = None,
    funding_df: pd.DataFrame | None = None,
) -> list[SignalEvent]:
    """Run requested strategies against a pre-fetched OHLCV DataFrame.

    Returns SignalEvents whose open_time matches the latest candle in the data.
    Only the latest candle is checked — signals on older candles are ignored
    to prevent re-alerting on historical data after a restart.
    """
    if ohlcv_df.empty or len(ohlcv_df) < 3:
        return []

    latest_open_time = int(ohlcv_df["open_time"].iloc[-1])
    latest_close = float(ohlcv_df["close"].iloc[-1])

    events: list[SignalEvent] = []

    for strategy_name in strategies:
        plugin = SIGNAL_REGISTRY.get(strategy_name)
        if plugin is None:
            logger.warning("Unknown strategy %s — skipping", strategy_name)
            continue

        spec = STRATEGY_REGISTRY.get(strategy_name)
        requires_funding = spec.requires_funding if spec else False
        requires_secondary = spec.requires_secondary if spec else False

        try:
            if requires_funding:
                if funding_df is None or funding_df.empty:
                    logger.debug(
                        "Skipping %s for %s — no funding data", strategy_name, symbol
                    )
                    continue
                signals_df = plugin["detector"](ohlcv_df, funding_df)
            elif requires_secondary:
                if secondary_df is None or secondary_df.empty:
                    logger.debug(
                        "Skipping %s for %s — no secondary data", strategy_name, symbol
                    )
                    continue
                signals_df = plugin["detector"](ohlcv_df, secondary_df)
            else:
                signals_df = plugin["detector"](ohlcv_df)
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
                )
            )

    return events


def run_scan_cycle(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
    timeframes: list[str],
    strategies: list[str],
    store: CooldownStore,
    cooldown_seconds: float = 3600.0,
    tp_r: float = 2.0,
    sl_pct: float = 0.02,
    send_telegram: bool = False,
    secondary_symbol: str | None = None,
    days: int = 90,
) -> list[str]:
    """Scan all symbol+timeframe combinations and return formatted alert strings.

    Pre-fetches OHLCV once per (symbol, timeframe) and passes the DataFrame into
    scan_symbol, avoiding redundant DB reads across strategies.
    Uses CooldownStore to suppress duplicate alerts. Optionally sends via Telegram.
    Returns list of formatted alert strings for logging/testing regardless of
    whether Telegram is enabled.
    """
    from utils.telegram import send_telegram_message

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 24 * 3600 * 1000

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

    # Pre-fetch secondary OHLCV per timeframe (shared across all primary symbols)
    secondary_dfs: dict[str, pd.DataFrame] = {}
    if needs_secondary and secondary_symbol:
        for tf in timeframes:
            secondary_dfs[tf] = get_ohlcv(conn, secondary_symbol, tf, start_ms, now_ms)

    alerts: list[str] = []

    for symbol in symbols:
        funding_df: pd.DataFrame | None = None
        if needs_funding:
            funding_df = get_funding_rates(conn, symbol, start_ms, now_ms)

        for tf in timeframes:
            ohlcv_df = get_ohlcv(conn, symbol, tf, start_ms, now_ms)
            sec_df = secondary_dfs.get(tf) if needs_secondary else None

            events = scan_symbol(
                ohlcv_df=ohlcv_df,
                symbol=symbol,
                timeframe=tf,
                strategies=strategies,
                secondary_df=sec_df,
                funding_df=funding_df,
            )

            # Conflict suppression: opposite directions on same symbol/tf → suppress all
            long_events = [e for e in events if e.direction == "long"]
            short_events = [e for e in events if e.direction == "short"]
            if long_events and short_events:
                logger.info(
                    "Conflict suppressed: %s %s has both LONG (%s) and SHORT (%s) signals",
                    symbol,
                    tf,
                    [e.strategy for e in long_events],
                    [e.strategy for e in short_events],
                )
                continue

            direction_events = long_events or short_events
            if not direction_events:
                continue

            # Filter each strategy independently by cooldown
            passing_events = [
                e
                for e in direction_events
                if store.is_new_candle(symbol, tf, e.strategy, e.open_time)
                and store.is_off_cooldown(symbol, e.strategy, e.direction)
            ]
            if not passing_events:
                continue

            for event in passing_events:
                store.record_alert(
                    symbol,
                    tf,
                    event.strategy,
                    event.direction,
                    event.open_time,
                    cooldown_seconds,
                )

            # Stack all passing strategies into one confluence alert
            msg = format_confluence_alert(passing_events, sl_pct=sl_pct, tp_r=tp_r)
            alerts.append(msg)
            logger.info(
                "Signal: %s %s %s %s (confluence: %d)",
                symbol,
                tf,
                passing_events[0].direction,
                [e.strategy for e in passing_events],
                len(passing_events),
            )

            if send_telegram:
                try:
                    send_telegram_message(msg)
                except Exception:
                    logger.exception("Telegram send failed for %s", symbol)

    return alerts

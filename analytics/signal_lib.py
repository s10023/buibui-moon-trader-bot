"""Pure signal scanning library.

scan_symbol(): runs requested strategies against a single symbol+timeframe,
               returning SignalEvents only for the latest candle.
run_scan_cycle(): fans out across all symbols/timeframes, deduplicates via
                  CooldownStore, formats alerts, and optionally sends Telegram.
No module-level side effects.
"""

import logging
import time

import duckdb
import pandas as pd

from analytics.data_store import get_funding_rates, get_ohlcv
from signals.alert_formatter import SignalEvent, format_signal_alert
from signals.cooldown_store import CooldownStore
from signals.registry import SIGNAL_REGISTRY

logger = logging.getLogger(__name__)


def scan_symbol(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    strategies: list[str],
    secondary_df: pd.DataFrame | None = None,
    funding_df: pd.DataFrame | None = None,
    days: int = 90,
) -> list[SignalEvent]:
    """Run requested strategies against symbol+timeframe.

    Returns SignalEvents whose open_time matches the latest candle in the DB.
    Only the latest candle is checked — signals on older candles are ignored
    to prevent re-alerting on historical data after a restart.
    """
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 24 * 3600 * 1000

    ohlcv_df = get_ohlcv(conn, symbol, timeframe, start_ms, now_ms)
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

        try:
            if plugin["requires_funding"]:
                if funding_df is None or funding_df.empty:
                    logger.debug(
                        "Skipping %s for %s — no funding data", strategy_name, symbol
                    )
                    continue
                signals_df = plugin["detector"](ohlcv_df, funding_df)
            elif plugin["requires_secondary"]:
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

    Uses CooldownStore to suppress duplicate alerts. Optionally sends via Telegram.
    Returns list of formatted alert strings for logging/testing regardless of
    whether Telegram is enabled.
    """
    from utils.telegram import send_telegram_message

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 24 * 3600 * 1000

    needs_funding = any(
        SIGNAL_REGISTRY[s]["requires_funding"]
        for s in strategies
        if s in SIGNAL_REGISTRY
    )
    needs_secondary = any(
        SIGNAL_REGISTRY[s]["requires_secondary"]
        for s in strategies
        if s in SIGNAL_REGISTRY
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
            sec_df = secondary_dfs.get(tf) if needs_secondary else None

            events = scan_symbol(
                conn=conn,
                symbol=symbol,
                timeframe=tf,
                strategies=strategies,
                secondary_df=sec_df,
                funding_df=funding_df,
                days=days,
            )

            for event in events:
                if not store.is_new_candle(symbol, tf, event.strategy, event.open_time):
                    continue
                if not store.is_off_cooldown(symbol, event.strategy, event.direction):
                    continue

                store.mark_candle(symbol, tf, event.strategy, event.open_time)
                store.set_cooldown(
                    symbol, event.strategy, event.direction, cooldown_seconds
                )

                msg = format_signal_alert(event, sl_pct=sl_pct, tp_r=tp_r)
                alerts.append(msg)
                logger.info(
                    "Signal: %s %s %s %s", symbol, tf, event.strategy, event.direction
                )

                if send_telegram:
                    try:
                        send_telegram_message(msg)
                    except Exception:
                        logger.exception("Telegram send failed for %s", symbol)

    return alerts

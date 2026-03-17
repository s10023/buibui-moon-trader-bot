"""Signal daemon runner — thin wrapper over signal_lib.

Creates the Binance client, opens the DuckDB connection, syncs data,
then polls on clock-aligned candle boundaries calling run_scan_cycle each time.
"""

import logging
import math
import signal
import time
import types
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from analytics.data_store import DEFAULT_DB_PATH, init_schema
from analytics.data_sync import backfill, sync
from analytics.signal_lib import run_scan_cycle
from signals.cooldown_store import CooldownStore
from utils.binance_client import create_client, load_coins_config

logger = logging.getLogger(__name__)

_DEFAULT_BACKFILL_DAYS = 90
_CANDLE_CLOSE_BUFFER_SECS = 10


def _parse_timeframe_secs(tf: str) -> int:
    """Convert a timeframe string to seconds (e.g. '4h' → 14400, '15m' → 900)."""
    units = {"m": 60, "h": 3600, "d": 86400}
    return int(tf[:-1]) * units[tf[-1]]


def _secs_until_next_boundary(timeframes: list[str]) -> tuple[float, float]:
    """Return (sleep_seconds, wakeup_unix_timestamp) for the next candle close.

    Wakes at the earliest upcoming boundary + a small buffer so Binance has
    time to finalise the candle (e.g. 04:00:10, not 04:00:00).
    """
    now = time.time()
    next_wakeups = []
    for tf in timeframes:
        interval = _parse_timeframe_secs(tf)
        next_close = math.ceil(now / interval) * interval
        next_wakeups.append(next_close + _CANDLE_CLOSE_BUFFER_SECS)
    wake_ts = min(next_wakeups)
    return max(0.0, wake_ts - now), wake_ts


def run_signal_watch(
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
    strategies: list[str] | None = None,
    tp_r: float = 2.0,
    send_telegram: bool = False,
    state_file: str = "signal_state.json",
    secondary_symbol: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Run the signal detection daemon loop.

    On each cycle: syncs new candles from Binance, scans for signals,
    sends Telegram alerts if enabled, then sleeps until the next candle
    boundary across all watched timeframes.
    """
    from analytics.indicators_lib import KNOWN_STRATEGIES

    client = create_client()
    coins_config = load_coins_config()

    resolved_symbols = symbols or list(coins_config.keys())
    resolved_timeframes = timeframes or ["4h"]
    resolved_strategies = strategies or [
        s for s in KNOWN_STRATEGIES if s != "seasonality"
    ]

    store = CooldownStore(state_file)

    logger.info(
        "Signal daemon starting — symbols=%s timeframes=%s strategies=%s",
        resolved_symbols,
        resolved_timeframes,
        resolved_strategies,
    )

    # Graceful shutdown: first Ctrl+C sets the flag; second Ctrl+C forces exit.
    # Using a flag instead of letting KeyboardInterrupt propagate prevents DuckDB's
    # native heap from being corrupted when conn.close() is called mid-operation.
    shutdown_requested = [False]

    def _handle_sigint(signum: int, frame: types.FrameType | None) -> None:
        if shutdown_requested[0]:
            raise KeyboardInterrupt  # second Ctrl+C: force exit
        shutdown_requested[0] = True
        logger.info(
            "Shutdown requested — finishing current operation and exiting cleanly"
        )

    prev_handler = signal.signal(signal.SIGINT, _handle_sigint)
    try:
        conn = duckdb.connect(str(db_path))
        init_schema(conn)
        try:
            while not shutdown_requested[0]:
                logger.info("--- scan cycle start ---")

                # Sync each symbol+timeframe; fall back to backfill for new symbols
                start_ms = (
                    int(time.time() * 1000) - _DEFAULT_BACKFILL_DAYS * 24 * 3600 * 1000
                )
                for symbol in resolved_symbols:
                    for tf in resolved_timeframes:
                        try:
                            sync(conn, client, symbol, tf)
                        except ValueError:
                            logger.info(
                                "No data for %s/%s — running initial backfill",
                                symbol,
                                tf,
                            )
                            backfill(conn, client, symbol, tf, start_ms)
                        except duckdb.IOException as exc:
                            logger.warning(
                                "DB sync failed for %s/%s (will retry): %s",
                                symbol,
                                tf,
                                exc,
                            )

                alerts = run_scan_cycle(
                    conn=conn,
                    symbols=resolved_symbols,
                    timeframes=resolved_timeframes,
                    strategies=resolved_strategies,
                    store=store,
                    tp_r=tp_r,
                    send_telegram=send_telegram,
                    secondary_symbol=secondary_symbol,
                )

                if alerts:
                    logger.info("%d alert(s) sent this cycle", len(alerts))
                else:
                    logger.info("No new signals this cycle")

                sleep_secs, wake_ts = _secs_until_next_boundary(resolved_timeframes)
                next_dt = datetime.fromtimestamp(wake_ts, tz=UTC).strftime(
                    "%H:%M:%S UTC"
                )
                logger.info("--- sleeping %.0fs until %s ---", sleep_secs, next_dt)

                # Interruptible sleep: 1s chunks so Ctrl+C exits within ~1s
                elapsed = 0.0
                while elapsed < sleep_secs and not shutdown_requested[0]:
                    chunk = min(1.0, sleep_secs - elapsed)
                    time.sleep(chunk)
                    elapsed += chunk
        finally:
            conn.close()
            logger.info("DB connection closed cleanly")
    finally:
        signal.signal(signal.SIGINT, prev_handler)

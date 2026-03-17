"""Signal daemon runner — thin wrapper over signal_lib.

Creates the Binance client, opens the DuckDB connection, syncs data,
then polls on a fixed interval calling run_scan_cycle each time.
"""

import logging
import time
from pathlib import Path

import duckdb

from analytics.data_store import DEFAULT_DB_PATH, init_schema
from analytics.data_sync import backfill, sync
from analytics.signal_lib import run_scan_cycle
from signals.cooldown_store import CooldownStore
from utils.binance_client import create_client, load_coins_config

logger = logging.getLogger(__name__)

_DEFAULT_BACKFILL_DAYS = 90


def run_signal_watch(
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
    strategies: list[str] | None = None,
    poll_interval: int = 300,
    tp_r: float = 2.0,
    send_telegram: bool = False,
    state_file: str = "signal_state.json",
    secondary_symbol: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Run the signal detection daemon loop.

    On each cycle: syncs new candles from Binance, scans for signals,
    sends Telegram alerts if enabled, then sleeps until the next cycle.
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
        "Signal daemon starting — symbols=%s timeframes=%s strategies=%s interval=%ds",
        resolved_symbols,
        resolved_timeframes,
        resolved_strategies,
        poll_interval,
    )

    conn = duckdb.connect(str(db_path))
    init_schema(conn)

    try:
        while True:
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
                            "No data for %s/%s — running initial backfill", symbol, tf
                        )
                        backfill(conn, client, symbol, tf, start_ms)
                    except duckdb.IOException as exc:
                        logger.warning(
                            "DB sync failed for %s/%s (will retry): %s", symbol, tf, exc
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

            logger.info("--- sleeping %ds ---", poll_interval)
            time.sleep(poll_interval)
    finally:
        conn.close()

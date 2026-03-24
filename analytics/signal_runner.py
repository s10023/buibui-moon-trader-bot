"""Signal daemon runner — thin wrapper over signal_lib.

Creates the Binance client, opens a short-lived DuckDB connection per scan cycle,
syncs data, scans for signals, then closes the connection before sleeping.

Each cycle: open conn → sync → scan → upsert signals → close conn → sleep.
This releases the write lock during the sleep window so the web API's read-only
connections can access the DB between cycles.
"""

import logging
import signal
import time
import types
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from analytics.data_store import DEFAULT_DB_PATH, init_schema
from analytics.data_sync import backfill, sync
from analytics.signal_config import BacktestFilterConfig
from analytics.signal_lib import (
    run_scan_cycle,
    secs_until_next_boundary,
)
from signals.cooldown_store import CooldownStore
from utils.binance_client import create_client, load_coins_config

logger = logging.getLogger(__name__)

_MYT = ZoneInfo("Asia/Kuala_Lumpur")
_DEFAULT_BACKFILL_DAYS = 90


def run_signal_watch(
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
    strategies: list[str] | None = None,
    tp_r: float = 2.0,
    min_sl_pct: float = 0.0,
    send_telegram: bool = False,
    state_file: str = "signal_state.json",
    secondary_symbol: str | None = None,
    smt_pairs: dict[str, str] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    backtest_cfg: BacktestFilterConfig | None = None,
    day_filter: bool = False,
    smt_trend_filter: int = 1,
    strategy_timeframes: dict[str, list[str]] | None = None,
) -> None:
    """Run the signal detection daemon loop.

    On each cycle: syncs new candles from Binance, scans for signals,
    sends Telegram alerts if enabled, then sleeps until the next candle
    boundary across all watched timeframes.
    """
    from analytics.data_store import get_ohlcv
    from analytics.indicators_lib import KNOWN_STRATEGIES

    client = create_client()
    coins_config = load_coins_config()

    resolved_symbols = symbols or list(coins_config.keys())
    resolved_timeframes = timeframes or ["4h"]
    resolved_strategies = strategies or [
        s for s in KNOWN_STRATEGIES if s != "seasonality"
    ]

    # Build secondary_map from coins.json, then overlay CLI --smt-pairs (CLI wins).
    coins_secondary_map: dict[str, str] = {
        sym: cfg["smt_secondary"]
        for sym, cfg in coins_config.items()
        if sym in resolved_symbols and "smt_secondary" in cfg
    }
    # Expand deprecated --secondary-symbol into a map if --smt-pairs not provided.
    if secondary_symbol and not smt_pairs:
        smt_pairs = {s: secondary_symbol for s in resolved_symbols}
    # CLI smt_pairs takes precedence over coins.json entries.
    secondary_map: dict[str, str] = {**coins_secondary_map, **(smt_pairs or {})}
    if not secondary_map:
        secondary_map_arg: dict[str, str] | None = None
    else:
        secondary_map_arg = secondary_map

    store = CooldownStore(state_file)

    logger.info("Signal daemon starting")
    logger.info("  symbols    = %s", resolved_symbols)
    logger.info("  timeframes = %s", resolved_timeframes)
    logger.info("  strategies = %s", resolved_strategies)

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
        # Init schema once at startup (short-lived connection).
        with duckdb.connect(str(db_path)) as init_conn:
            init_schema(init_conn)

        # Startup probe: warn (don't abort) for secondaries with no OHLCV yet.
        # Uses a short-lived connection so the write lock is released immediately.
        if secondary_map_arg:
            now_probe_ms = int(time.time() * 1000)
            start_probe_ms = now_probe_ms - _DEFAULT_BACKFILL_DAYS * 24 * 3600 * 1000
            seen_secondaries: set[str] = set()
            with duckdb.connect(str(db_path)) as probe_conn:
                for sym in resolved_symbols:
                    sec = secondary_map_arg.get(sym)
                    if sec and sec not in seen_secondaries:
                        seen_secondaries.add(sec)
                        probe_df = get_ohlcv(
                            probe_conn,
                            sec,
                            resolved_timeframes[0],
                            start_probe_ms,
                            now_probe_ms,
                        )
                        if probe_df.empty:
                            logger.warning(
                                "Secondary symbol %s has no OHLCV data yet — "
                                "run 'analytics backfill --symbols %s' to populate it",
                                sec,
                                sec,
                            )

        while not shutdown_requested[0]:
            logger.info("--- scan cycle start ---")

            # Open a short-lived connection for this cycle only.
            # Closing before the sleep window releases the write lock so the
            # web API's read-only connections can access the DB between cycles.
            with duckdb.connect(str(db_path)) as conn:
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
                    min_sl_pct=min_sl_pct,
                    send_telegram=send_telegram,
                    secondary_map=secondary_map_arg,
                    backtest_cfg=backtest_cfg,
                    day_filter=day_filter,
                    smt_trend_filter=smt_trend_filter,
                    strategy_timeframes=strategy_timeframes,
                )
            # Connection is now closed — web API can read the DB during the sleep.

            if alerts:
                logger.info("%d alert(s) sent this cycle", len(alerts))
            else:
                logger.info("No new signals this cycle")

            sleep_secs, wake_ts = secs_until_next_boundary(resolved_timeframes)
            next_dt = datetime.fromtimestamp(wake_ts, tz=_MYT).strftime("%H:%M:%S MYT")
            logger.info("--- sleeping %.0fs until %s ---", sleep_secs, next_dt)

            # Interruptible sleep: 1s chunks so Ctrl+C exits within ~1s
            elapsed = 0.0
            while elapsed < sleep_secs and not shutdown_requested[0]:
                chunk = min(1.0, sleep_secs - elapsed)
                time.sleep(chunk)
                elapsed += chunk
    finally:
        signal.signal(signal.SIGINT, prev_handler)

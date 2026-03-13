"""Backtest runner — thin wrapper: opens DB, loads data, calls strategy + backtest libs."""

import datetime
import logging
import sys
from pathlib import Path

import duckdb

from analytics.backtest_lib import format_result, format_seasonality, run_backtest
from analytics.data_store import get_funding_rates, get_ohlcv, init_schema
from analytics.indicators_lib import (
    KNOWN_STRATEGIES,
    detect_funding_extreme,
    detect_fvg,
    detect_liquidity_sweep,
    detect_market_structure,
    detect_marubozu_retest,
    detect_orb_breakout,
    detect_smt_divergence,
    detect_wick_fills,
    seasonality_stats,
)

_DEFAULT_DB_PATH: Path = Path("analytics.db")


def run_backtest_cmd(
    symbol: str,
    strategy: str,
    timeframe: str,
    days: int,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
    secondary_symbol: str | None = None,
    db_path: Path = _DEFAULT_DB_PATH,
) -> None:
    """Open DB, load OHLCV, detect signals, run backtest, print results."""
    if strategy not in KNOWN_STRATEGIES:
        logging.error(
            "Unknown strategy '%s'. Choose from: %s",
            strategy,
            ", ".join(KNOWN_STRATEGIES),
        )
        sys.exit(1)

    end_ms = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    start_ms = end_ms - days * 24 * 3_600 * 1_000

    conn: duckdb.DuckDBPyConnection = duckdb.connect(str(db_path))
    try:
        init_schema(conn)

        ohlcv = get_ohlcv(conn, symbol, timeframe, start_ms, end_ms)
        if ohlcv.empty:
            logging.error(
                "No OHLCV data for %s %s. Run 'analytics backfill' first.",
                symbol,
                timeframe,
            )
            sys.exit(1)

        if strategy == "seasonality":
            stats = seasonality_stats(ohlcv)
            print(format_seasonality(stats))
            return

        if strategy == "funding_reversion":
            funding = get_funding_rates(conn, symbol, start_ms, end_ms)
            if funding.empty:
                logging.error(
                    "No funding rate data for %s. Run 'analytics backfill' first.",
                    symbol,
                )
                sys.exit(1)
            signals = detect_funding_extreme(ohlcv, funding)

        elif strategy == "smt_divergence":
            if secondary_symbol is None:
                logging.error(
                    "--secondary-symbol required for smt_divergence strategy."
                )
                sys.exit(1)
            ohlcv_sec = get_ohlcv(conn, secondary_symbol, timeframe, start_ms, end_ms)
            if ohlcv_sec.empty:
                logging.error(
                    "No OHLCV data for secondary symbol %s %s.",
                    secondary_symbol,
                    timeframe,
                )
                sys.exit(1)
            signals = detect_smt_divergence(ohlcv, ohlcv_sec)

        elif strategy == "wick_fill":
            signals = detect_wick_fills(ohlcv)

        elif strategy == "marubozu":
            signals = detect_marubozu_retest(ohlcv)

        elif strategy == "orb":
            signals = detect_orb_breakout(ohlcv)

        elif strategy == "liquidity_sweep":
            signals = detect_liquidity_sweep(ohlcv)

        elif strategy == "fvg":
            signals = detect_fvg(ohlcv)

        else:  # "bos"
            signals = detect_market_structure(ohlcv)

        bt_result = run_backtest(
            ohlcv, signals, symbol, timeframe, strategy, sl_pct, tp_r
        )
        print(format_result(bt_result))

    finally:
        conn.close()

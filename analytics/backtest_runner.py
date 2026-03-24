"""Backtest runner — thin wrapper: opens DB, loads data, calls strategy + backtest libs."""

import datetime
import itertools
import logging
import sys
from pathlib import Path

import duckdb
import pandas as pd

from analytics.backtest_config import BacktestSweepConfig
from analytics.backtest_lib import (
    BacktestResult,
    filter_signals_by_day,
    format_result,
    format_seasonality,
    format_sweep_table,
    run_backtest,
)
from analytics.data_store import (
    DEFAULT_DB_PATH,
    get_funding_rates,
    get_ohlcv,
)
from analytics.indicators_lib import (
    DETECTOR_REGISTRY,
    KNOWN_STRATEGIES,
    detect_funding_extreme,
    detect_smt_divergence,
    seasonality_stats,
)
from utils.binance_client import load_coins_config

_SIMPLE_DETECTORS = DETECTOR_REGISTRY

_SWEEP_STRATEGIES: list[str] = [s for s in KNOWN_STRATEGIES if s != "seasonality"]


def detect_signals_for_strategy(
    conn: duckdb.DuckDBPyConnection,
    ohlcv: pd.DataFrame,
    symbol: str,
    timeframe: str,
    strategy: str,
    start_ms: int,
    end_ms: int,
    secondary_symbol: str | None = None,
    smt_trend_filter: int = 1,
) -> pd.DataFrame | None:
    """Return signals DataFrame, or None when required data is absent.

    ohlcv must already be fetched and non-empty by the caller.
    Returns None only when funding or secondary OHLCV data is missing.
    """
    if strategy == "funding_reversion":
        funding = get_funding_rates(conn, symbol, start_ms, end_ms)
        if funding.empty:
            return None
        return detect_funding_extreme(ohlcv, funding)

    if strategy == "smt_divergence":
        if secondary_symbol is None:
            return None
        ohlcv_sec = get_ohlcv(conn, secondary_symbol, timeframe, start_ms, end_ms)
        if ohlcv_sec.empty:
            return None
        return detect_smt_divergence(ohlcv, ohlcv_sec, trend_filter=smt_trend_filter)

    return _SIMPLE_DETECTORS[strategy](ohlcv)


def run_backtest_sweep(
    cfg: BacktestSweepConfig,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Run all symbol × timeframe × strategy combos and print a ranked table."""
    end_ms = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    start_ms = end_ms - cfg.days * 24 * 3_600 * 1_000

    symbols = cfg.symbols
    if symbols is None:
        coins = load_coins_config()
        symbols = list(coins.keys())

    strategies = cfg.strategies if cfg.strategies is not None else _SWEEP_STRATEGIES

    n_strats = len(strategies)
    strat_word = "strategy" if n_strats == 1 else "strategies"
    n_tfs = len(cfg.timeframes)
    tf_word = "timeframe" if n_tfs == 1 else "timeframes"
    n_syms = len(symbols)
    sym_word = "symbol" if n_syms == 1 else "symbols"
    print(
        f"Backtest Sweep — {n_syms} {sym_word} × "
        f"{n_tfs} {tf_word} × "
        f"{n_strats} {strat_word} ({cfg.days}d)"
    )

    results: list[BacktestResult] = []
    skipped: list[str] = []

    conn: duckdb.DuckDBPyConnection = duckdb.connect(str(db_path), read_only=True)
    try:
        for symbol, timeframe, strategy in itertools.product(
            symbols, cfg.timeframes, strategies
        ):
            if strategy == "seasonality":
                continue

            secondary = (
                cfg.smt_pairs.get(symbol) if strategy == "smt_divergence" else None
            )
            if strategy == "smt_divergence" and secondary is None:
                skipped.append(
                    f"{symbol}/{timeframe}/{strategy} (no smt_pair configured)"
                )
                continue

            ohlcv = get_ohlcv(conn, symbol, timeframe, start_ms, end_ms)
            if ohlcv.empty:
                skipped.append(f"{symbol}/{timeframe}/{strategy} (no data)")
                continue

            signals = detect_signals_for_strategy(
                conn,
                ohlcv,
                symbol,
                timeframe,
                strategy,
                start_ms,
                end_ms,
                secondary,
                smt_trend_filter=cfg.smt_trend_filter,
            )
            if signals is None:
                skipped.append(
                    f"{symbol}/{timeframe}/{strategy} (missing funding/secondary data)"
                )
                continue

            if cfg.day_filter:
                signals = filter_signals_by_day(signals)

            bt = run_backtest(
                ohlcv,
                signals,
                symbol,
                timeframe,
                strategy,
                cfg.sl_pct,
                cfg.tp_r,
                cfg.fee_pct,
            )
            results.append(bt)

    finally:
        conn.close()

    print(format_sweep_table(results, cfg.min_trades))

    if skipped:
        print(f"\n  Skipped {len(skipped)} combo(s):")
        for s in skipped:
            print(f"    • {s}")


def run_backtest_cmd(
    symbol: str,
    strategy: str,
    timeframe: str,
    days: int,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
    fee_pct: float = 0.0,
    secondary_symbol: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
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

    conn: duckdb.DuckDBPyConnection = duckdb.connect(str(db_path), read_only=True)
    try:
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

        signals = detect_signals_for_strategy(
            conn, ohlcv, symbol, timeframe, strategy, start_ms, end_ms, secondary_symbol
        )
        if signals is None:
            if strategy == "funding_reversion":
                logging.error(
                    "No funding rate data for %s. Run 'analytics backfill' first.",
                    symbol,
                )
            elif strategy == "smt_divergence":
                logging.error(
                    "--secondary-symbol required for smt_divergence strategy."
                )
            sys.exit(1)

        bt_result = run_backtest(
            ohlcv, signals, symbol, timeframe, strategy, sl_pct, tp_r, fee_pct
        )
        print(format_result(bt_result))

    finally:
        conn.close()

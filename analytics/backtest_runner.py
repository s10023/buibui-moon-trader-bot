"""Backtest runner — thin wrapper: opens DB, loads data, calls strategy + backtest libs."""

import datetime
import itertools
import logging
import sys
from collections.abc import Callable
from pathlib import Path

import duckdb
import pandas as pd

from analytics.backtest_config import BacktestSweepConfig
from analytics.backtest_lib import (
    BacktestResult,
    format_result,
    format_seasonality,
    run_backtest,
)
from analytics.data_store import (
    DEFAULT_DB_PATH,
    get_funding_rates,
    get_ohlcv,
    init_schema,
)
from analytics.indicators_lib import (
    KNOWN_STRATEGIES,
    detect_cvd_divergence,
    detect_eqh_eql,
    detect_funding_extreme,
    detect_fvg,
    detect_liquidity_sweep,
    detect_market_structure,
    detect_marubozu_retest,
    detect_orb_breakout,
    detect_order_block,
    detect_smt_divergence,
    detect_wick_fills,
    seasonality_stats,
)
from utils.binance_client import load_coins_config

_SIMPLE_DETECTORS: dict[str, Callable[[pd.DataFrame], pd.DataFrame]] = {
    "wick_fill": detect_wick_fills,
    "marubozu": detect_marubozu_retest,
    "orb": detect_orb_breakout,
    "liquidity_sweep": detect_liquidity_sweep,
    "fvg": detect_fvg,
    "bos": detect_market_structure,
    "eqh_eql": detect_eqh_eql,
    "order_block": detect_order_block,
    "cvd_divergence": detect_cvd_divergence,
}

_SWEEP_STRATEGIES: list[str] = [s for s in KNOWN_STRATEGIES if s != "seasonality"]


def _detect_signals_for_strategy(
    conn: duckdb.DuckDBPyConnection,
    ohlcv: pd.DataFrame,
    symbol: str,
    timeframe: str,
    strategy: str,
    start_ms: int,
    end_ms: int,
    secondary_symbol: str | None = None,
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
        return detect_smt_divergence(ohlcv, ohlcv_sec)

    return _SIMPLE_DETECTORS[strategy](ohlcv)


def format_sweep_table(
    results: list[BacktestResult],
    min_trades: int = 20,
) -> str:
    """Format a ranked backtest sweep table as a string.

    Rows with fewer than min_trades closed trades are excluded and counted in the footer.
    Results are sorted by avg_r descending.
    """
    qualifying = [r for r in results if len(r.closed_trades) >= min_trades]
    hidden = len(results) - len(qualifying)

    qualifying.sort(key=lambda r: r.avg_r, reverse=True)

    col_w = (14, 6, 18, 8, 8, 8)
    header = (
        f"{'Symbol':<{col_w[0]}}"
        f"{'TF':<{col_w[1]}}"
        f"{'Strategy':<{col_w[2]}}"
        f"{'Win%':>{col_w[3]}}"
        f"{'Trades':>{col_w[4]}}"
        f"{'Avg R':>{col_w[5]}}"
    )
    sep = "─" * sum(col_w)
    thick_sep = "═" * sum(col_w)

    lines = [thick_sep, header, sep]

    if not qualifying:
        lines.append(f"  No results with ≥ {min_trades} trades.")
    else:
        for r in qualifying:
            win_pct = f"{r.win_rate * 100:.1f}%"
            avg_r = f"{r.avg_r:+.2f}R"
            lines.append(
                f"{r.symbol:<{col_w[0]}}"
                f"{r.timeframe:<{col_w[1]}}"
                f"{r.strategy:<{col_w[2]}}"
                f"{win_pct:>{col_w[3]}}"
                f"{len(r.closed_trades):>{col_w[4]}}"
                f"{avg_r:>{col_w[5]}}"
            )

    lines.append(sep)
    if hidden > 0:
        lines.append(f"  Hidden: {hidden} combo(s) with < {min_trades} trades")

    return "\n".join(lines)


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

    print(
        f"Backtest Sweep — {len(symbols)} symbol(s) × "
        f"{len(cfg.timeframes)} timeframe(s) × "
        f"{len(strategies)} strategy/ies ({cfg.days}d)"
    )

    results: list[BacktestResult] = []
    skipped: list[str] = []

    conn: duckdb.DuckDBPyConnection = duckdb.connect(str(db_path))
    try:
        init_schema(conn)

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

            signals = _detect_signals_for_strategy(
                conn, ohlcv, symbol, timeframe, strategy, start_ms, end_ms, secondary
            )
            if signals is None:
                skipped.append(
                    f"{symbol}/{timeframe}/{strategy} (missing funding/secondary data)"
                )
                continue

            bt = run_backtest(
                ohlcv, signals, symbol, timeframe, strategy, cfg.sl_pct, cfg.tp_r
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

        signals = _detect_signals_for_strategy(
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
            ohlcv, signals, symbol, timeframe, strategy, sl_pct, tp_r
        )
        print(format_result(bt_result))

    finally:
        conn.close()

"""Backtest runner — thin wrapper: opens DB, loads data, calls strategy + backtest libs."""

import datetime
import itertools
import logging
import sys
import uuid
from pathlib import Path

import duckdb
import pandas as pd

from analytics.backtest_config import BacktestSweepConfig
from analytics.backtest_lib import (
    BacktestResult,
    filter_signals_by_day,
    format_duration_table,
    format_result,
    format_seasonality,
    format_sweep_table,
    format_tp_sweep_table,
    format_volume_split,
    run_backtest,
)
from analytics.data_store import (
    DEFAULT_DB_PATH,
    get_funding_rates,
    get_ohlcv,
    upsert_backtest_run,
    upsert_backtest_trades,
)
from analytics.indicators_lib import (
    DETECTOR_REGISTRY,
    KNOWN_STRATEGIES,
    detect_funding_extreme,
    detect_liquidity_sweep,
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
    liq_sweep_use_fib: bool = True,
    liq_sweep_fib_range_close: bool = False,
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

    if strategy == "liquidity_sweep":
        return detect_liquidity_sweep(
            ohlcv,
            use_fib_extension=liq_sweep_use_fib,
            fib_require_range_close=liq_sweep_fib_range_close,
        )

    return _SIMPLE_DETECTORS[strategy](ohlcv)


def _collect_signals_map(
    conn: duckdb.DuckDBPyConnection,
    cfg: BacktestSweepConfig,
    symbols: list[str],
    strategies: list[str],
    start_ms: int,
    end_ms: int,
) -> tuple[
    dict[tuple[str, str, str], tuple[pd.DataFrame, pd.DataFrame, str | None]], list[str]
]:
    """Detect signals once for all symbol × TF × strategy combos.

    Returns a map keyed by (symbol, timeframe, strategy) →
    (ohlcv, filtered_signals, secondary_symbol) plus a skipped list.
    Used by tp_r sweep mode to avoid re-running detection for each tp_r value.
    """
    from analytics.signal_config import _day_filter_to_weekdays

    allowed_days = _day_filter_to_weekdays(cfg.day_filter)
    signals_map: dict[
        tuple[str, str, str], tuple[pd.DataFrame, pd.DataFrame, str | None]
    ] = {}
    skipped: list[str] = []
    # OHLCV cache: all strategies share the same candle data for a given symbol+TF.
    # Avoids N_strategies redundant DB reads per (symbol, timeframe) pair.
    ohlcv_cache: dict[tuple[str, str], pd.DataFrame] = {}

    for symbol, timeframe, strategy in itertools.product(
        symbols, cfg.timeframes, strategies
    ):
        if strategy == "seasonality":
            continue

        secondary = cfg.smt_pairs.get(symbol) if strategy == "smt_divergence" else None
        if strategy == "smt_divergence" and secondary is None:
            skipped.append(f"{symbol}/{timeframe}/{strategy} (no smt_pair configured)")
            continue

        ohlcv_key = (symbol, timeframe)
        if ohlcv_key not in ohlcv_cache:
            ohlcv_cache[ohlcv_key] = get_ohlcv(
                conn, symbol, timeframe, start_ms, end_ms
            )
        ohlcv = ohlcv_cache[ohlcv_key]
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
            liq_sweep_use_fib=cfg.liq_sweep_use_fib,
            liq_sweep_fib_range_close=cfg.liq_sweep_fib_range_close,
        )
        if signals is None:
            skipped.append(
                f"{symbol}/{timeframe}/{strategy} (missing funding/secondary data)"
            )
            continue

        if allowed_days is not None:
            signals = filter_signals_by_day(signals, allowed_days)

        signals_map[(symbol, timeframe, strategy)] = (ohlcv, signals, secondary)

    return signals_map, skipped


def _collect_sweep_results(
    conn: duckdb.DuckDBPyConnection,
    cfg: BacktestSweepConfig,
    tp_r: float,
    symbols: list[str],
    strategies: list[str],
    start_ms: int,
    end_ms: int,
    sweep_id: str | None = None,
) -> tuple[list[BacktestResult], list[str]]:
    """Run one full symbol × TF × strategy grid for a given tp_r value."""
    from analytics.signal_config import _day_filter_to_weekdays

    allowed_days = _day_filter_to_weekdays(cfg.day_filter)
    results: list[BacktestResult] = []
    skipped: list[str] = []

    for symbol, timeframe, strategy in itertools.product(
        symbols, cfg.timeframes, strategies
    ):
        if strategy == "seasonality":
            continue

        secondary = cfg.smt_pairs.get(symbol) if strategy == "smt_divergence" else None
        if strategy == "smt_divergence" and secondary is None:
            skipped.append(f"{symbol}/{timeframe}/{strategy} (no smt_pair configured)")
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
            liq_sweep_use_fib=cfg.liq_sweep_use_fib,
            liq_sweep_fib_range_close=cfg.liq_sweep_fib_range_close,
        )
        if signals is None:
            skipped.append(
                f"{symbol}/{timeframe}/{strategy} (missing funding/secondary data)"
            )
            continue

        if allowed_days is not None:
            signals = filter_signals_by_day(signals, allowed_days)

        eff_tp_r = cfg.effective_tp_r(strategy, timeframe)
        eff_sl_pct = cfg.effective_sl_pct(strategy, timeframe)
        eff_atr_sl = cfg.effective_atr_sl_multiplier(strategy, timeframe)
        bt = run_backtest(
            ohlcv,
            signals,
            symbol,
            timeframe,
            strategy,
            eff_sl_pct,
            eff_tp_r,
            cfg.fee_pct,
            min_sl_pct=cfg.min_sl_pct,
            atr_sl_multiplier=eff_atr_sl,
        )
        results.append(bt)

        if cfg.save_results and sweep_id is not None:
            run_id = upsert_backtest_run(
                conn,
                bt,
                days=cfg.days,
                data_start_ms=start_ms,
                data_end_ms=end_ms,
                sl_pct=eff_sl_pct,
                tp_r=eff_tp_r,
                fee_pct=cfg.fee_pct,
                day_filter=cfg.day_filter,
                smt_trend_filter=cfg.smt_trend_filter,
                secondary_symbol=secondary,
                sweep_id=sweep_id,
            )
            upsert_backtest_trades(conn, bt, run_id)

    return results, skipped


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

    tp_sweep_mode = bool(cfg.tp_r_values)

    if tp_sweep_mode:
        print(
            f"Backtest TP Sweep — {n_syms} {sym_word} × "
            f"{n_tfs} {tf_word} × "
            f"{n_strats} {strat_word} ({cfg.days}d) "
            f"× tp_r {cfg.tp_r_values}"
        )
    else:
        print(
            f"Backtest Sweep — {n_syms} {sym_word} × "
            f"{n_tfs} {tf_word} × "
            f"{n_strats} {strat_word} ({cfg.days}d)"
        )

    sweep_id = str(uuid.uuid4()) if cfg.save_results and not tp_sweep_mode else None
    conn: duckdb.DuckDBPyConnection = duckdb.connect(
        str(db_path), read_only=not (cfg.save_results and not tp_sweep_mode)
    )

    try:
        if tp_sweep_mode:
            # Detect signals once — tp_r does not affect signal detection
            signals_map, skipped = _collect_signals_map(
                conn, cfg, symbols, strategies, start_ms, end_ms
            )
            results_by_tp: dict[float, list[BacktestResult]] = {}
            for tp_r in cfg.tp_r_values:
                tp_results: list[BacktestResult] = []
                for (sym, tf, strat), (ohlcv, sigs, _sec) in signals_map.items():
                    # tp_r is swept globally; per-strategy sl_pct/atr_sl overrides still apply.
                    bt = run_backtest(
                        ohlcv,
                        sigs,
                        sym,
                        tf,
                        strat,
                        cfg.effective_sl_pct(strat, tf),
                        tp_r,
                        cfg.fee_pct,
                        min_sl_pct=cfg.min_sl_pct,
                        atr_sl_multiplier=cfg.effective_atr_sl_multiplier(strat, tf),
                    )
                    tp_results.append(bt)
                results_by_tp[tp_r] = tp_results
            # Duration uses results from default tp_r (or first value)
            duration_results = results_by_tp.get(
                cfg.tp_r, next(iter(results_by_tp.values()))
            )
            print(format_tp_sweep_table(results_by_tp))
            print(format_duration_table(duration_results))
        else:
            results, skipped = _collect_sweep_results(
                conn, cfg, cfg.tp_r, symbols, strategies, start_ms, end_ms, sweep_id
            )
            print(
                format_sweep_table(
                    results, cfg.min_trades, cfg.min_trades_per_tf or None
                )
            )
            print(format_volume_split(results))
            print(format_duration_table(results))
            if cfg.save_results:
                print(f"\n  Results saved to DB (sweep_id={sweep_id})")

    finally:
        conn.close()

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
    min_sl_pct: float = 0.0,
    atr_sl_multiplier: float | None = None,
    secondary_symbol: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    save_results: bool = False,
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

    conn: duckdb.DuckDBPyConnection = duckdb.connect(
        str(db_path), read_only=not save_results
    )
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
            ohlcv,
            signals,
            symbol,
            timeframe,
            strategy,
            sl_pct,
            tp_r,
            fee_pct,
            min_sl_pct=min_sl_pct,
            atr_sl_multiplier=atr_sl_multiplier,
        )
        print(format_result(bt_result))

        if save_results:
            run_id = upsert_backtest_run(
                conn,
                bt_result,
                days=days,
                data_start_ms=start_ms,
                data_end_ms=end_ms,
                sl_pct=sl_pct,
                tp_r=tp_r,
                fee_pct=fee_pct,
                day_filter="off",
                smt_trend_filter=1,
                secondary_symbol=secondary_symbol,
            )
            upsert_backtest_trades(conn, bt_result, run_id)
            print(f"\n  Results saved to DB (run_id={run_id})")

    finally:
        conn.close()

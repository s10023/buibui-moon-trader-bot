import argparse
import datetime
import logging

from dotenv import load_dotenv

from analytics import (
    analytics_runner,
    backtest_runner,
    recalibrate_runner,
    signal_runner,
)
from analytics.backtest_config import BacktestSweepConfig, load_backtest_config
from analytics.digest_lib import QUERY_NAMES
from analytics.indicators_lib import KNOWN_STRATEGIES
from monitor import position_monitor, price_monitor


def run_price_monitor(args: argparse.Namespace) -> None:
    price_monitor.main(live=args.live, telegram=args.telegram, sort=args.sort)


def _parse_since_to_ms(since: str) -> int:
    """Parse ISO date string 'YYYY-MM-DD' to Unix milliseconds."""
    d = datetime.datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=datetime.UTC)
    return int(d.timestamp() * 1000)


def run_analytics_backfill(args: argparse.Namespace) -> None:
    analytics_runner.run_backfill(
        symbols=args.symbols,
        timeframes=args.timeframes,
        since_ms=_parse_since_to_ms(args.since),
    )


def run_analytics_sync(args: argparse.Namespace) -> None:
    analytics_runner.run_sync(
        symbols=args.symbols,
        timeframes=args.timeframes,
    )


def run_digest_cmd(args: argparse.Namespace) -> None:
    backtest_runner.run_digest_cmd(args.query, args.min_trades, args.top_n)


def run_backtest(args: argparse.Namespace) -> None:
    # Sweep mode: --config or --symbols (or --timeframes / --strategies alone)
    if args.config or args.symbols or args.timeframes or args.strategies:
        cfg = (
            load_backtest_config(args.config) if args.config else BacktestSweepConfig()
        )
        # CLI flags override TOML values
        if args.symbols:
            cfg.symbols = args.symbols
        if args.timeframes:
            cfg.timeframes = args.timeframes
        if args.strategies:
            cfg.strategies = args.strategies
        if args.days != 90:
            cfg.days = args.days
        if args.sl_pct != 0.02:
            cfg.sl_pct = args.sl_pct
        if args.tp_r != 2.0:
            cfg.tp_r = args.tp_r
        if args.fee_pct != 0.0:
            cfg.fee_pct = args.fee_pct
        if args.min_trades is not None:
            cfg.min_trades = args.min_trades
        if args.day_filter:
            cfg.day_filter = "tue_thu"
        if args.save:
            cfg.save_results = True
        if args.atr_sl_multiplier is not None:
            cfg.atr_sl_multiplier = args.atr_sl_multiplier
        if args.atr_sl_multiplier_values:
            cfg.atr_sl_multiplier_values = args.atr_sl_multiplier_values
        backtest_runner.run_backtest_sweep(cfg)
        return

    # Single-combo mode: --symbol + --strategy (backward-compatible)
    if not args.symbol or not args.strategy:
        raise SystemExit(
            "error: --symbol and --strategy are required in single-combo mode"
        )
    backtest_runner.run_backtest_cmd(
        symbol=args.symbol,
        strategy=args.strategy,
        timeframe=args.interval,
        days=args.days,
        sl_pct=args.sl_pct,
        tp_r=args.tp_r,
        fee_pct=args.fee_pct,
        min_sl_pct=args.min_sl_pct
        if hasattr(args, "min_sl_pct") and args.min_sl_pct is not None
        else 0.0,
        atr_sl_multiplier=args.atr_sl_multiplier,
        secondary_symbol=args.secondary_symbol,
        save_results=args.save,
    )


def _parse_smt_pairs(value: str) -> dict[str, str]:
    """Parse comma-separated PRIMARY:SECONDARY tokens into a dict.

    Example: 'BTCUSDT:ETHUSDT,ETHUSDT:BTCUSDT' → {'BTCUSDT': 'ETHUSDT', 'ETHUSDT': 'BTCUSDT'}
    """
    result: dict[str, str] = {}
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        parts = token.split(":", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise argparse.ArgumentTypeError(
                f"Invalid smt-pairs token '{token}' — expected PRIMARY:SECONDARY"
            )
        result[parts[0].strip()] = parts[1].strip()
    return result


def run_signal_test(args: argparse.Namespace) -> None:
    import pathlib

    from analytics.indicators_lib import KNOWN_STRATEGIES
    from analytics.signal_config import SignalWatchConfig, load_signal_config
    from analytics.signal_test_runner import run_signal_test as _run

    cfg = SignalWatchConfig()
    if getattr(args, "config", None):
        cfg = load_signal_config(args.config)

    from utils.binance_client import load_coins_config

    coins_config = load_coins_config()

    # CLI flags narrow down; config provides defaults; daemon-matching fallbacks.
    # Symbols: CLI → config → coins.json (mirrors signal_runner.py behaviour).
    symbols = args.symbol or cfg.symbols or list(coins_config.keys())
    timeframes = args.timeframe or cfg.timeframes or ["4h"]
    strategies = (
        args.strategy
        or cfg.strategies
        or [s for s in KNOWN_STRATEGIES if s != "seasonality"]
    )

    if not symbols:
        raise SystemExit(
            "error: no symbols found — pass --symbol or add symbols to your config/coins.json"
        )

    tp_r = args.tp_r if args.tp_r is not None else cfg.tp_r
    min_sl_pct = args.min_sl_pct if args.min_sl_pct is not None else cfg.min_sl_pct

    at_ms: int | None = None
    if args.at:
        try:
            dt = datetime.datetime.fromisoformat(args.at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.UTC)
            at_ms = int(dt.timestamp() * 1000)
        except ValueError:
            try:
                at_ms = int(args.at)
            except ValueError:
                raise SystemExit(
                    f"error: --at '{args.at}' is not a valid ISO datetime or Unix ms timestamp"
                )

    # Build secondary_map from coins.json (same as signal_runner.py).
    secondary_map: dict[str, str] = {
        sym: coins_config[sym]["smt_secondary"]
        for sym in symbols
        if sym in coins_config and "smt_secondary" in coins_config[sym]
    }

    kwargs: dict[str, object] = dict(
        symbols=symbols,
        timeframes=timeframes,
        strategies=strategies,
        at_ms=at_ms,
        lookback=args.lookback,
        tp_r=tp_r,
        sl_pct=cfg.sl_pct,
        min_sl_pct=min_sl_pct,
        direction_filter=args.direction,
        send_telegram=args.telegram,
        backtest_cfg=cfg.backtest,
        day_filter=cfg.day_filter,
        secondary_map=secondary_map or None,
    )
    if getattr(args, "db_path", None):
        kwargs["db_path"] = pathlib.Path(args.db_path)
    _run(**kwargs)  # type: ignore[arg-type]


def run_signal_watch(args: argparse.Namespace) -> None:
    import pathlib

    from analytics.signal_config import SignalWatchConfig, load_signal_config

    cfg = SignalWatchConfig()
    if getattr(args, "config", None):
        cfg = load_signal_config(args.config)

    # CLI flags override config file values; None means "not provided by user"
    symbols = args.symbols if args.symbols is not None else cfg.symbols
    timeframes = args.timeframes if args.timeframes is not None else cfg.timeframes
    strategies = args.strategies if args.strategies is not None else cfg.strategies
    tp_r = args.tp_r if args.tp_r is not None else cfg.tp_r
    min_sl_pct = args.min_sl_pct if args.min_sl_pct is not None else cfg.min_sl_pct
    telegram = args.telegram or cfg.telegram  # either source can enable
    state_file = (
        args.state_file if args.state_file != "signal_state.json" else cfg.state_file
    )

    cli_smt_pairs: dict[str, str] | None = getattr(args, "smt_pairs", None)
    # CLI --smt-pairs overrides config [smt_pairs] table entirely when provided
    smt_pairs = cli_smt_pairs if cli_smt_pairs is not None else (cfg.smt_pairs or None)

    config_name = (
        pathlib.Path(args.config).stem if getattr(args, "config", None) else None
    )
    signal_runner.run_signal_watch(
        symbols=symbols,
        timeframes=timeframes,
        strategies=strategies,
        tp_r=tp_r,
        sl_pct=cfg.sl_pct,
        min_sl_pct=min_sl_pct,
        send_telegram=telegram,
        state_file=state_file,
        secondary_symbol=args.secondary_symbol,
        smt_pairs=smt_pairs,
        backtest_cfg=cfg.backtest,
        day_filter=cfg.day_filter,
        smt_trend_filter=cfg.smt_trend_filter,
        strategy_timeframes=cfg.strategy_timeframes or None,
        strategy_params=cfg.strategy_params or None,
        atr_sl_multiplier=cfg.atr_sl_multiplier,
        config_name=config_name,
        bias_cfg=cfg.bias,
    )


def run_position_monitor(args: argparse.Namespace) -> None:
    position_monitor.main(
        sort=args.sort,
        telegram=args.telegram,
        hide_empty=args.hide_empty,
        compact=args.compact,
        live=args.live,
    )


def run_param_sweep(args: argparse.Namespace) -> None:
    import duckdb

    from analytics.data_store import DEFAULT_DB_PATH
    from analytics.param_sweep import (
        ParamRange,
        _parse_param_spec,
        format_sweep_results,
    )
    from analytics.param_sweep import run_param_sweep as _run

    param_ranges: list[ParamRange] | None = None
    if args.params:
        try:
            param_ranges = [_parse_param_spec(s) for s in args.params]
        except ValueError as e:
            raise SystemExit(f"error: {e}") from e

    if param_ranges is None:
        from analytics.param_sweep import _default_param_ranges

        param_ranges = _default_param_ranges(args.strategy)

    _tf_defaults = {"15m": 20, "1h": 12, "4h": 5, "1d": 2}
    min_trades = (
        args.min_trades if args.min_trades else _tf_defaults.get(args.timeframe, 8)
    )

    grid_size = 1
    for r in param_ranges:
        grid_size *= len(r.values)

    print(f"\nParam sweep  {args.strategy} / {args.symbol} / {args.timeframe}")
    print(
        f"Days: {args.days}  WFO split: {args.wfo_split:.0%} IS / {1 - args.wfo_split:.0%} OOS"
    )
    print(f"Grid: {grid_size} combos  Min trades: {min_trades}  Top-N: {args.top_n}")
    print(f"Params: {', '.join(r.name for r in param_ranges)}")

    if grid_size > 5000:
        print(f"\n  WARNING: Grid has {grid_size} combos — this may take a while.")

    db_path = args.db or DEFAULT_DB_PATH
    conn: duckdb.DuckDBPyConnection = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = _run(
            conn=conn,
            strategy=args.strategy,
            symbol=args.symbol,
            timeframe=args.timeframe,
            days=args.days,
            param_ranges=param_ranges,
            wfo_split=args.wfo_split,
            min_trades=min_trades,
            fee_pct=args.fee_pct,
            top_n=args.top_n,
            adr_suppress_threshold=args.adr_suppress_threshold,
        )
    finally:
        conn.close()

    print(format_sweep_results(rows, args.strategy, args.symbol, args.timeframe))


def run_param_audit(args: argparse.Namespace) -> None:
    import duckdb

    from analytics.data_store import DEFAULT_DB_PATH
    from analytics.indicators_lib import KNOWN_STRATEGIES
    from analytics.param_sweep import (
        format_audit_results,
        run_strategy_audit,
    )

    strategies = (
        args.strategies
        if args.strategies
        else [s for s in KNOWN_STRATEGIES if s != "seasonality"]
    )
    _tf_defaults = {"15m": 20, "1h": 12, "4h": 5, "1d": 2}
    min_trades = (
        args.min_trades if args.min_trades else _tf_defaults.get(args.timeframe, 8)
    )

    print(f"\nStrategy audit  {args.symbol} / {args.timeframe} / {args.days}d")
    print(f"Strategies: {len(strategies)}  WFO split: {args.wfo_split:.0%} IS")

    db_path = args.db or DEFAULT_DB_PATH
    conn: duckdb.DuckDBPyConnection = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = run_strategy_audit(
            conn=conn,
            symbol=args.symbol,
            timeframe=args.timeframe,
            days=args.days,
            strategies=strategies,
            wfo_split=args.wfo_split,
            min_trades=min_trades,
            fee_pct=args.fee_pct,
            adr_suppress_threshold=args.adr_suppress_threshold,
        )
    finally:
        conn.close()

    print(format_audit_results(rows, args.symbol, args.timeframe, args.days))


def run_recalibrate(args: argparse.Namespace) -> None:
    recalibrate_runner.run(args)


def run_web_server(args: argparse.Namespace) -> None:
    import os

    import uvicorn

    if getattr(args, "config", None):
        os.environ["BUIBUI_CONFIG"] = args.config

    uvicorn.run(
        "web.api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(description="Buibui Moon Trader CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Top-level 'monitor' command
    monitor_parser = subparsers.add_parser("monitor", help="Monitoring tools")
    monitor_subparsers = monitor_parser.add_subparsers(
        dest="monitor_command", required=True
    )

    # 'price' subcommand
    price_parser = monitor_subparsers.add_parser("price", help="Run price monitor")
    price_parser.add_argument("--live", action="store_true", help="Live refresh mode")
    price_parser.add_argument(
        "--sort",
        default="default",
        help="Sort table by column[:asc|desc]. Options: change_15m, change_1h, change_4h, change_asia, change_24h. Example: --sort change_15m:desc",
    )
    price_parser.add_argument(
        "--telegram", action="store_true", help="Send output to Telegram"
    )
    price_parser.set_defaults(func=run_price_monitor)

    # 'position' subcommand
    position_parser = monitor_subparsers.add_parser(
        "position", help="Run position monitor"
    )
    position_parser.add_argument(
        "--live", action="store_true", help="Live refresh mode"
    )
    position_parser.add_argument("--sort", default="default", help="Sort order")
    position_parser.add_argument(
        "--telegram", action="store_true", help="Send output to Telegram"
    )
    position_parser.add_argument(
        "--hide-empty", action="store_true", help="Hide symbols with no open positions"
    )
    position_parser.add_argument(
        "--compact", action="store_true", help="Show compact information"
    )
    position_parser.set_defaults(func=run_position_monitor)

    # Top-level 'signal' command
    signal_parser = subparsers.add_parser("signal", help="Signal detection tools")
    signal_subparsers = signal_parser.add_subparsers(
        dest="signal_command", required=True
    )

    # 'watch' subcommand
    watch_parser = signal_subparsers.add_parser(
        "watch", help="Run 24/7 signal detection daemon"
    )
    watch_parser.add_argument(
        "--config",
        default=None,
        metavar="FILE",
        help="Path to TOML config file (e.g. config/signal_watch.toml). CLI flags override file values.",
    )
    watch_parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Symbols to scan (default: all from coins.json)",
    )
    watch_parser.add_argument(
        "--timeframes",
        nargs="+",
        default=None,
        help="Timeframes to scan (default: 4h, or from --config)",
    )
    watch_parser.add_argument(
        "--strategies",
        nargs="+",
        default=None,
        help="Strategies to run (default: all except seasonality)",
    )
    watch_parser.add_argument(
        "--tp-r",
        type=float,
        default=None,
        dest="tp_r",
        help="Take-profit risk:reward ratio for alert formatting (default: 2.0, or from --config)",
    )
    watch_parser.add_argument(
        "--telegram",
        action="store_true",
        help="Send alerts via Telegram",
    )
    watch_parser.add_argument(
        "--state-file",
        default="signal_state.json",
        dest="state_file",
        help="Path to candle watermark state file (default: signal_state.json)",
    )
    watch_parser.add_argument(
        "--secondary-symbol",
        default=None,
        dest="secondary_symbol",
        help="Secondary symbol for SMT divergence strategy (e.g. ETHUSDT) (deprecated — use --smt-pairs)",
    )
    watch_parser.add_argument(
        "--smt-pairs",
        default=None,
        dest="smt_pairs",
        type=_parse_smt_pairs,
        help=(
            "Per-symbol SMT secondary mappings as comma-separated PRIMARY:SECONDARY tokens "
            "(e.g. BTCUSDT:ETHUSDT,ETHUSDT:BTCUSDT). Overrides smt_secondary in coins.json."
        ),
    )
    watch_parser.add_argument(
        "--min-sl-pct",
        type=float,
        default=None,
        dest="min_sl_pct",
        help="Minimum SL distance as a fraction of price (e.g. 0.005 = 0.5%%; default: 0 = disabled, or from --config)",
    )
    watch_parser.set_defaults(func=run_signal_watch)

    # 'test' subcommand
    test_parser = signal_subparsers.add_parser(
        "test",
        help="Fire a detector against historical data and print/send the formatted alert",
    )
    test_parser.add_argument(
        "--config",
        default=None,
        metavar="FILE",
        help="TOML config to inherit symbol/TF/tp_r/sl_pct defaults from.",
    )
    test_parser.add_argument(
        "--symbol", nargs="+", default=None, help="Symbol(s), e.g. BTCUSDT ETHUSDT"
    )
    test_parser.add_argument(
        "--timeframe", nargs="+", default=None, help="Timeframe(s), e.g. 1h 4h"
    )
    test_parser.add_argument(
        "--strategy",
        nargs="+",
        default=None,
        help="Strategy/strategies to test, e.g. bos fvg (default: all from --config or all known)",
    )
    test_parser.add_argument(
        "--at",
        default=None,
        metavar="TIMESTAMP",
        help=(
            "Pin to a specific candle. Accepts ISO datetime (e.g. 2026-04-07T02:00:00, "
            "treated as UTC unless offset given) or Unix ms integer. "
            "Defaults to latest available candle."
        ),
    )
    test_parser.add_argument(
        "--lookback",
        type=int,
        default=200,
        help="Number of candles to load (default: 200).",
    )
    test_parser.add_argument(
        "--direction",
        default=None,
        choices=["long", "short"],
        help="Only show signals in this direction.",
    )
    test_parser.add_argument(
        "--tp-r",
        type=float,
        default=None,
        dest="tp_r",
        help="TP risk:reward for alert formatting (default: 2.0 or from --config).",
    )
    test_parser.add_argument(
        "--min-sl-pct",
        type=float,
        default=None,
        dest="min_sl_pct",
        help="Minimum SL distance as fraction of price (default: 0 or from --config).",
    )
    test_parser.add_argument(
        "--telegram",
        action="store_true",
        help="Send the alert via Telegram in addition to printing it.",
    )
    test_parser.add_argument(
        "--db-path",
        default=None,
        dest="db_path",
        help="Path to analytics.db (default: project default).",
    )
    test_parser.set_defaults(func=run_signal_test)

    # Top-level 'analytics' command
    analytics_parser = subparsers.add_parser("analytics", help="Analytics data tools")
    analytics_subparsers = analytics_parser.add_subparsers(
        dest="analytics_command", required=True
    )

    # 'backfill' subcommand
    backfill_parser = analytics_subparsers.add_parser(
        "backfill", help="Full history backfill from Binance"
    )
    backfill_parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Symbols to backfill (default: all from coins.json)",
    )
    backfill_parser.add_argument(
        "--timeframes",
        nargs="+",
        default=["1h", "4h"],
        help="Timeframes to backfill (default: 1h 4h)",
    )
    backfill_parser.add_argument(
        "--since",
        default="2023-01-01",
        help="Start date in YYYY-MM-DD format (default: 2023-01-01)",
    )
    backfill_parser.set_defaults(func=run_analytics_backfill)

    # 'sync' subcommand
    sync_parser = analytics_subparsers.add_parser(
        "sync", help="Incremental sync since last stored candle"
    )
    sync_parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Symbols to sync (default: all from coins.json)",
    )
    sync_parser.add_argument(
        "--timeframes",
        nargs="+",
        default=["1h", "4h"],
        help="Timeframes to sync (default: 1h 4h)",
    )
    sync_parser.set_defaults(func=run_analytics_sync)

    # Top-level 'backtest' command
    backtest_parser = subparsers.add_parser(
        "backtest", help="Backtest a trading strategy on historical data"
    )
    # Single-combo flags
    backtest_parser.add_argument(
        "--symbol",
        default=None,
        help="Primary symbol for single-combo mode (e.g., BTCUSDT)",
    )
    backtest_parser.add_argument(
        "--strategy",
        default=None,
        choices=KNOWN_STRATEGIES,
        help="Strategy for single-combo mode: " + ", ".join(KNOWN_STRATEGIES),
    )
    backtest_parser.add_argument(
        "--interval",
        default="4h",
        help="Candle timeframe for single-combo mode (default: 4h)",
    )
    backtest_parser.add_argument(
        "--secondary-symbol",
        default=None,
        dest="secondary_symbol",
        help="Secondary symbol for smt_divergence strategy (e.g., ETHUSDT)",
    )
    # Shared / sweep flags
    backtest_parser.add_argument(
        "--config",
        default=None,
        metavar="FILE",
        help="TOML config file for sweep mode",
    )
    backtest_parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Symbols to sweep (overrides --config)",
    )
    backtest_parser.add_argument(
        "--strategies",
        nargs="+",
        default=None,
        choices=KNOWN_STRATEGIES,
        help="Strategies to sweep (overrides --config)",
    )
    backtest_parser.add_argument(
        "--timeframes",
        nargs="+",
        default=None,
        help="Timeframes to sweep (overrides --config)",
    )
    backtest_parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Lookback period in days (default: 90)",
    )
    backtest_parser.add_argument(
        "--sl-pct",
        type=float,
        default=0.02,
        dest="sl_pct",
        help="Stop loss as a decimal fraction (default: 0.02 = 2%%)",
    )
    backtest_parser.add_argument(
        "--tp-r",
        type=float,
        default=2.0,
        dest="tp_r",
        help="Take profit in R multiples (default: 2.0)",
    )
    backtest_parser.add_argument(
        "--fee-pct",
        type=float,
        default=0.0,
        dest="fee_pct",
        help="Taker fee as a decimal fraction applied on entry+exit (default: 0.0; e.g. 0.0005 for 0.05%%)",
    )
    backtest_parser.add_argument(
        "--day-filter",
        action="store_true",
        default=False,
        dest="day_filter",
        help="Suppress Monday and Friday signals (ICT weekly cycle) before backtesting",
    )
    backtest_parser.add_argument(
        "--save",
        action="store_true",
        default=False,
        help="Persist aggregate results to backtest_runs table in DB",
    )
    backtest_parser.add_argument(
        "--atr-sl-multiplier",
        type=float,
        default=None,
        dest="atr_sl_multiplier",
        help="ATR-based SL multiplier: SL = N × ATR14 (overrides --sl-pct when set)",
    )
    backtest_parser.add_argument(
        "--atr-sl-values",
        nargs="+",
        type=float,
        default=None,
        dest="atr_sl_multiplier_values",
        help="ATR SL multiplier sweep: comparison table across values (e.g. 0.5 1.0 1.5 2.0 2.5)",
    )
    backtest_parser.add_argument(
        "--min-trades",
        type=int,
        default=None,
        dest="min_trades",
        help="Hide combos below this trade count in sweep table (default: 20)",
    )
    backtest_parser.set_defaults(func=run_backtest)

    # Top-level 'backtest digest' command
    digest_parser = subparsers.add_parser(
        "digest",
        help="Aggregated backtest analysis: leaderboards, A/B comparisons, breadth stats",
    )
    digest_parser.add_argument(
        "--query",
        default="strategy",
        choices=QUERY_NAMES,
        help=(
            "Which analysis to run (default: strategy). "
            "Options: " + ", ".join(QUERY_NAMES)
        ),
    )
    digest_parser.add_argument(
        "--min-trades",
        type=int,
        default=5,
        dest="min_trades",
        help="Minimum closed trades to include a run (default: 5)",
    )
    digest_parser.add_argument(
        "--top-n",
        type=int,
        default=20,
        dest="top_n",
        help="Max rows returned for combos query (default: 20)",
    )
    digest_parser.set_defaults(func=run_digest_cmd)

    # Top-level 'param-sweep' command
    param_sweep_parser = subparsers.add_parser(
        "param-sweep",
        help="WFO parameter sweep: grid search + walk-forward validation for one strategy",
    )
    param_sweep_parser.add_argument(
        "--strategy",
        required=True,
        choices=KNOWN_STRATEGIES,
        help="Strategy to sweep: " + ", ".join(KNOWN_STRATEGIES),
    )
    param_sweep_parser.add_argument(
        "--symbol",
        required=True,
        help="Symbol (e.g. BTCUSDT)",
    )
    param_sweep_parser.add_argument(
        "--timeframe",
        required=True,
        help="Timeframe (e.g. 1h)",
    )
    param_sweep_parser.add_argument(
        "--param",
        action="append",
        dest="params",
        metavar="NAME=MIN:MAX:STEP",
        help="Param range override. Repeatable. E.g. --param tp_r=1.0:5.0:0.5",
    )
    param_sweep_parser.add_argument(
        "--wfo-split",
        type=float,
        default=0.7,
        dest="wfo_split",
        help="In-sample fraction for walk-forward split (default: 0.7)",
    )
    param_sweep_parser.add_argument(
        "--min-trades",
        type=int,
        default=0,
        dest="min_trades",
        help="Min closed trades in IS to score a config (default: auto by TF)",
    )
    param_sweep_parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        dest="top_n",
        help="Number of top configs to display (default: 10)",
    )
    param_sweep_parser.add_argument(
        "--days",
        type=int,
        default=180,
        help="Days of history to load (default: 180)",
    )
    param_sweep_parser.add_argument(
        "--fee-pct",
        type=float,
        default=0.0005,
        dest="fee_pct",
        help="Taker fee fraction (default: 0.0005 = 0.05%%)",
    )
    param_sweep_parser.add_argument(
        "--adr-suppress-threshold",
        type=float,
        default=None,
        dest="adr_suppress_threshold",
        help="ADR suppress threshold (e.g. 0.80) — filter signals when today's range >= N × ADR-14",
    )
    param_sweep_parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Path to DuckDB database (default: analytics.db)",
    )
    param_sweep_parser.set_defaults(func=run_param_sweep)

    # Top-level 'param-audit' command
    param_audit_parser = subparsers.add_parser(
        "param-audit",
        help="Quick tp_r sweep across all strategies — verdict table showing which have edge",
    )
    param_audit_parser.add_argument(
        "--symbol", required=True, help="Symbol (e.g. BTCUSDT)"
    )
    param_audit_parser.add_argument(
        "--timeframe", required=True, help="Timeframe (e.g. 1h)"
    )
    param_audit_parser.add_argument(
        "--strategies",
        nargs="+",
        default=None,
        choices=KNOWN_STRATEGIES,
        help="Strategies to audit (default: all except seasonality)",
    )
    param_audit_parser.add_argument(
        "--days", type=int, default=180, help="Days of history (default: 180)"
    )
    param_audit_parser.add_argument(
        "--wfo-split",
        type=float,
        default=0.7,
        dest="wfo_split",
        help="In-sample fraction (default: 0.7)",
    )
    param_audit_parser.add_argument(
        "--min-trades",
        type=int,
        default=0,
        dest="min_trades",
        help="Min IS trades to score (default: auto by TF)",
    )
    param_audit_parser.add_argument(
        "--fee-pct",
        type=float,
        default=0.0005,
        dest="fee_pct",
        help="Taker fee fraction (default: 0.0005)",
    )
    param_audit_parser.add_argument(
        "--adr-suppress-threshold",
        type=float,
        default=None,
        dest="adr_suppress_threshold",
        help="ADR suppress threshold (e.g. 0.80) — filter signals when today's range >= N × ADR-14",
    )
    param_audit_parser.add_argument(
        "--db", type=str, default=None, help="DuckDB path (default: analytics.db)"
    )
    param_audit_parser.set_defaults(func=run_param_audit)

    # Top-level 'recalibrate' command
    recalibrate_parser = subparsers.add_parser(
        "recalibrate",
        help="Recalibrate strategy confidence star ratings from backtest_runs data",
    )
    recalibrate_parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply the new ratings to STRATEGY_REGISTRY (default: dry-run only)",
    )
    recalibrate_parser.add_argument(
        "--min-trades",
        type=int,
        default=10,
        dest="min_trades",
        help="Minimum total closed trades required to recalibrate a strategy (default: 10)",
    )
    recalibrate_parser.add_argument(
        "--config",
        type=str,
        default=None,
        dest="config",
        help="TOML config path to calibrate for (derives day_filter + config_name from file; "
        "--apply writes to confidence_ratings DB table instead of indicators_lib.py)",
    )
    recalibrate_parser.add_argument(
        "--day-filter",
        type=str,
        default=None,
        dest="day_filter",
        help="Only use backtest runs saved with this day_filter value (e.g. tue_thu); "
        "overridden by --config when both are provided",
    )
    recalibrate_parser.set_defaults(func=run_recalibrate)

    # Top-level 'web' command
    web_parser = subparsers.add_parser("web", help="Run FastAPI web backend")
    web_parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)"
    )
    web_parser.add_argument(
        "--port", type=int, default=8000, help="Bind port (default: 8000)"
    )
    web_parser.add_argument(
        "--reload", action="store_true", help="Enable auto-reload (dev mode)"
    )
    web_parser.add_argument(
        "--config",
        default=None,
        metavar="FILE",
        help="Path to signal-watch TOML config (e.g. config/signal_watch.toml). "
        "Exposes config defaults to the UI via GET /api/active-config.",
    )
    web_parser.set_defaults(func=run_web_server)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

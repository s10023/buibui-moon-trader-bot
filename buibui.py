import argparse
import datetime
import logging

from dotenv import load_dotenv

from analytics import analytics_runner, backtest_runner, signal_runner
from analytics.backtest_config import BacktestSweepConfig, load_backtest_config
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
        secondary_symbol=args.secondary_symbol,
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


def run_signal_watch(args: argparse.Namespace) -> None:
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

    signal_runner.run_signal_watch(
        symbols=symbols,
        timeframes=timeframes,
        strategies=strategies,
        tp_r=tp_r,
        min_sl_pct=min_sl_pct,
        send_telegram=telegram,
        state_file=state_file,
        secondary_symbol=args.secondary_symbol,
        smt_pairs=smt_pairs,
        backtest_cfg=cfg.backtest,
        day_filter=cfg.day_filter,
    )


def run_position_monitor(args: argparse.Namespace) -> None:
    position_monitor.main(
        sort=args.sort,
        telegram=args.telegram,
        hide_empty=args.hide_empty,
        compact=args.compact,
        live=args.live,
    )


def run_web_server(args: argparse.Namespace) -> None:
    import uvicorn

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
        help="Path to cooldown/watermark state file (default: signal_state.json)",
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
        "--min-trades",
        type=int,
        default=None,
        dest="min_trades",
        help="Hide combos below this trade count in sweep table (default: 20)",
    )
    backtest_parser.set_defaults(func=run_backtest)

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
    web_parser.set_defaults(func=run_web_server)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

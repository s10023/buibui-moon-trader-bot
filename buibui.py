import argparse
from monitor import price_monitor, position_monitor


def run_price_monitor(args: argparse.Namespace) -> None:
    price_monitor.main(live=args.live, telegram=args.telegram)


def run_position_monitor(args: argparse.Namespace) -> None:
    position_monitor.main(
        sort=args.sort, telegram=args.telegram, hide_empty=args.hide_empty
    )


def main() -> None:
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
        "--telegram", action="store_true", help="Send output to Telegram"
    )
    price_parser.set_defaults(func=run_price_monitor)

    # 'position' subcommand
    position_parser = monitor_subparsers.add_parser(
        "position", help="Run position monitor"
    )
    position_parser.add_argument("--sort", default="default", help="Sort order")
    position_parser.add_argument(
        "--telegram", action="store_true", help="Send output to Telegram"
    )
    position_parser.add_argument(
        "--hide-empty", action="store_true", help="Hide symbols with no open positions"
    )
    position_parser.set_defaults(func=run_position_monitor)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

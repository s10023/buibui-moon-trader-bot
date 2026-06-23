"""XS-solo order-routing executor CLI (P3 sub-project #3 slice 2 + #4).

Builds the overlay-gated order plan from the local read-only `analytics.db` plus
live Binance account state and either prints it (dry-run, the default) or submits
it on testnet. Mainnet (`--mode live`) is double-gated by `--i-understand-live`
AND `BINANCE_ALLOW_LIVE=1`. `--kill` / `--resume` toggle the kill-switch.

Run `buibui analytics sync --universe` first to refresh the 1d bars.

Usage::

    PYTHONPATH=. poetry run python tools/xsmom_execute.py            # dry-run
    PYTHONPATH=. poetry run python tools/xsmom_execute.py --mode testnet
"""

from __future__ import annotations

import argparse
import dataclasses
import os
from pathlib import Path

import duckdb

from analytics.forecast.config import ForecastConfig
from analytics.store import DEFAULT_DB_PATH
from analytics.universe import load_universe
from trade.binance_futures import BinanceFuturesAdapter
from trade.overlay import RiskLimits
from trade.xsmom_executor import (
    ExecutionResult,
    load_state,
    run_once,
    save_state,
)

_DEFAULT_STATE_DIR = Path("docs/plans/xsmom_targets")


def _fmt_price(mark: float | None) -> str:
    if mark is None or mark <= 0:
        return "—"
    if mark >= 1000:
        return f"{mark:,.0f}"
    if mark >= 1:
        return f"{mark:,.2f}"
    return f"{mark:.5f}"


def check_live_gate(
    mode: str, *, i_understand_live: bool, allow_live_env: str | None
) -> str | None:
    """Return an error message if live mode is requested but not double-gated."""
    if mode != "live":
        return None
    if not i_understand_live:
        return "live mode requires --i-understand-live"
    if allow_live_env != "1":
        return "live mode requires BINANCE_ALLOW_LIVE=1 in the environment"
    return None


def format_result(res: ExecutionResult) -> str:
    lines = [
        f"XS execute [{res.mode}] — hold {res.book.next_period_date}   "
        f"equity ${res.equity:,.2f}   governor {res.book.governor:.2f}",
    ]
    if not res.verdict.allowed:
        lines.append("ORDER PLAN BLOCKED by overlay:")
        lines.extend(f"  abort: {a}" for a in res.verdict.aborts)
        return "\n".join(lines)
    lines.append(
        f"{'SYM':<12}{'SIDE':<6}{'QTY':>12}{'RED':>5}{'Δ$NOTIONAL':>14}  reason"
    )
    for o in res.plan.intents:
        lines.append(
            f"{o.symbol:<12}{o.side:<6}{o.qty:>12.4f}"
            f"{('Y' if o.reduce_only else 'N'):>5}{o.delta_notional:>+14,.0f}  {o.reason}"
        )
    lines.append(
        f"submitted={len(res.submitted)}  skipped={len(res.plan.skipped)}  "
        f"failed={len(res.failed)}"
    )
    for intent, err in res.failed:
        lines.append(f"  FAILED {intent.symbol} {intent.side} {intent.qty}: {err}")
    return "\n".join(lines)


def _build_client(mode: str):  # type: ignore[no-untyped-def]
    from utils.binance_client import create_client

    if mode == "testnet":
        from binance.client import Client

        key = os.environ["BINANCE_TESTNET_API_KEY"]
        secret = os.environ["BINANCE_TESTNET_API_SECRET"]
        return Client(key, secret, testnet=True)
    return create_client()  # mainnet (reads only in dry_run)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--symbols", type=str, default=None)
    parser.add_argument(
        "--mode", choices=("dry_run", "testnet", "live"), default="dry_run"
    )
    parser.add_argument("--no-trade-band", type=float, default=0.005)
    parser.add_argument("--exchange-leverage", type=int, default=5)
    parser.add_argument(
        "--vol-target",
        type=float,
        default=0.20,
        help="Portfolio vol target (validated 0.20; deploy first live cycles at 0.10)",
    )
    parser.add_argument("--max-gross-leverage", type=float, default=4.5)
    parser.add_argument("--max-position-notional-frac", type=float, default=0.5)
    parser.add_argument("--max-drawdown-frac", type=float, default=0.25)
    parser.add_argument("--max-run-turnover-frac", type=float, default=1.0)
    parser.add_argument("--max-data-staleness-hours", type=float, default=36.0)
    parser.add_argument("--min-active-positions", type=int, default=15)
    parser.add_argument("--i-understand-live", action="store_true")
    parser.add_argument("--kill", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--state-dir", type=Path, default=_DEFAULT_STATE_DIR)
    return parser


def main() -> None:
    args = build_parser().parse_args()

    state_path = args.state_dir / f"execution_state_{args.mode}.json"

    if args.kill or args.resume:
        state = load_state(state_path)
        state["kill_switch"] = bool(args.kill)
        save_state(state_path, state)
        print(f"kill_switch = {state['kill_switch']} ({state_path})")
        return

    gate_err = check_live_gate(
        args.mode,
        i_understand_live=args.i_understand_live,
        allow_live_env=os.environ.get("BINANCE_ALLOW_LIVE"),
    )
    if gate_err:
        raise SystemExit(gate_err)

    cfg = ForecastConfig.from_toml(args.config) if args.config else ForecastConfig()
    cfg = dataclasses.replace(cfg, vol_target_annual=args.vol_target)
    symbols = args.symbols.split(",") if args.symbols else load_universe()
    limits = RiskLimits(
        max_gross_leverage=args.max_gross_leverage,
        max_position_notional_frac=args.max_position_notional_frac,
        max_drawdown_frac=args.max_drawdown_frac,
        max_run_turnover_frac=args.max_run_turnover_frac,
        max_data_staleness_hours=args.max_data_staleness_hours,
        min_active_positions=args.min_active_positions,
    )

    adapter = BinanceFuturesAdapter(_build_client(args.mode), mode=args.mode)
    with duckdb.connect(str(args.db), read_only=True) as conn:
        res = run_once(
            conn,
            adapter,
            cfg,
            symbols,
            limits,
            no_trade_band_frac=args.no_trade_band,
            exchange_leverage=args.exchange_leverage,
            state_path=state_path,
        )
    print(format_result(res))


if __name__ == "__main__":
    main()

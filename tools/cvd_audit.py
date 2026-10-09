"""D1 spot-perp CVD sleeve — backfill the spot leg and run the gate.

``backfill`` is the ONLY write path this tool has, and it writes only into the
additive ``spot_ohlcv`` table — never ``ohlcv``, never anything on the live
signal path. ``run`` is read-only.

Spec: docs/superpowers/specs/2026-08-11-d1-spot-perp-cvd-design.md
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from pathlib import Path

import duckdb

from analytics.cvd.fetch import (
    fetch_spot_ohlcv_daily,
    fetch_trading_spot_symbols,
    spot_symbol_for,
)
from analytics.cvd.replay import (
    cvd_universe,
    replay_cvd_ts,
    replay_cvd_ts_trials,
    replay_cvd_xs,
    replay_cvd_xs_trials,
    replay_xsmom_benchmark,
)
from analytics.cvd.report import CVDReport, cvd_gate_verdict, evaluate_cvd
from analytics.forecast.config import ForecastConfig
from analytics.store._common import DEFAULT_DB_PATH
from analytics.store.schema import init_schema
from analytics.store.spot_data import upsert_spot_ohlcv
from analytics.venue_fetch import Getter, http_get_json

_START_MS = 1_546_300_800_000  # 2019-01-01, before the earliest perp listing.


def backfill(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbols: list[str],
    start_ms: int,
    get: Getter = http_get_json,
    sleep: Callable[[float], None] = time.sleep,
    trading: frozenset[str] | None = None,
) -> dict[str, int]:
    """Fetch and upsert daily spot bars. Returns rows written per perp symbol.

    ``trading`` is the set of spot symbols whose exchangeInfo status is TRADING;
    ``None`` fetches it. **Filtering on it is load-bearing, not defensive.**
    Kline availability is NOT proof a pair is live: probed 2026-08-11, TONUSDT
    returns HTTP 200 daily klines while its spot status is ``BREAK``. Without
    this filter a halted market's bars enter the panel and look like data.
    """
    live = fetch_trading_spot_symbols(get=get) if trading is None else trading
    written: dict[str, int] = {}
    skipped_not_trading: list[str] = []
    for sym in symbols:
        spot_sym = spot_symbol_for(sym)
        if spot_sym is None:
            continue
        if spot_sym not in live:
            skipped_not_trading.append(sym)
            continue
        df = fetch_spot_ohlcv_daily(spot_sym, start_ms, get=get)
        if df.empty:
            continue
        # Stored under the PERP symbol so joins against `ohlcv` need no mapping.
        df = df.assign(symbol=sym)
        upsert_spot_ohlcv(
            conn,
            df[
                [
                    "symbol",
                    "open_time",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "taker_buy_volume",
                ]
            ],
        )
        written[sym] = len(df)
        sleep(0.25)
    if skipped_not_trading:
        print(f"[backfill] skipped, spot not TRADING: {', '.join(skipped_not_trading)}")
    return written


def _print_report(label: str, report: CVDReport) -> None:
    verdict = "PASS" if cvd_gate_verdict(report) else "FAIL"
    print(f"\n=== {label} ===")
    print(f"  Sharpe (ann)      {report.sharpe_annual:+.3f}")
    print(f"  max drawdown      {report.max_dd:+.3f}")
    print(f"  n_obs             {report.n_obs}")
    print(f"  DSR               {report.dsr:.4f}")
    print(f"  PBO               {report.pbo:.4f}")
    print(f"  boot CI           [{report.boot_lo:+.3f}, {report.boot_hi:+.3f}]")
    print(f"  GATE (3 legs)     {verdict}")
    print("  -- stamps, not legs --")
    print(f"  MinTRL            {report.min_trl:.0f}")
    print(f"  corr_to_xsmom     {report.corr_to_xsmom:+.3f}")
    print(f"  xsmom Sharpe/23   {report.xsmom_sharpe:+.3f}")
    if report.inverted:
        print(
            "  NOTE: returns inverted (testing the pre-registered sign "
            "convention's inverse, negative-direction rule)."
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    bf = sub.add_parser("backfill", help="fetch daily spot bars (the only write path)")
    bf.add_argument("--start-ms", type=int, default=_START_MS)

    rn = sub.add_parser("run", help="run both book shapes and print the gate")
    rn.add_argument(
        "--invert",
        action="store_true",
        help=(
            "negative-direction rule: test the pre-registered sign "
            "convention's inverse by negating portfolio + trial returns "
            "before any metric is computed (not a Sharpe fold)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = ForecastConfig()

    if args.command == "backfill":
        with duckdb.connect(str(args.db)) as conn:
            init_schema(conn)
            counts = backfill(conn, symbols=cvd_universe(), start_ms=args.start_ms)
        print(f"[backfill] {sum(counts.values())} rows across {len(counts)} symbols")
        return 0

    with duckdb.connect(str(args.db), read_only=True) as conn:
        bench = replay_xsmom_benchmark(conn, cfg)
        cvd_xs = replay_cvd_xs(conn, cfg)
        _print_report(
            "Shape A - cross-sectional",
            evaluate_cvd(
                cvd_xs.portfolio_return,
                cfg,
                replay_cvd_xs_trials(conn, cfg),
                bench.portfolio_return,
                portfolio_index=cvd_xs.daily_index,
                xsmom_index=bench.daily_index,
                invert=args.invert,
            ),
        )
        cvd_ts = replay_cvd_ts(conn, cfg)
        _print_report(
            "Shape B - time-series",
            evaluate_cvd(
                cvd_ts.portfolio_return,
                cfg,
                replay_cvd_ts_trials(conn, cfg),
                bench.portfolio_return,
                portfolio_index=cvd_ts.daily_index,
                xsmom_index=bench.daily_index,
                invert=args.invert,
            ),
        )
    print("\n10 trials declared before any result: 5 per shape x 2 shapes.")
    return 0


if __name__ == "__main__":
    from utils.stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())

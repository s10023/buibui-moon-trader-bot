"""XS-solo daily target-position generator (P3 sub-project #3, slice 1) — read-only.

Computes today's governor-scaled XS target positions (side, leverage, $notional)
from the local `analytics.db`, prints a table, and appends a gitignored JSON
snapshot. The emitted targets equal the validated +1.375 book's next-bar
positions. No order routing.

Run `buibui analytics sync --universe` first to refresh the 1d bars.

Usage::

    PYTHONPATH=. poetry run python tools/xsmom_targets.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb

from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs
from analytics.store import DEFAULT_DB_PATH
from analytics.universe import load_universe
from analytics.xsmom.live import (
    TargetBook,
    position_deltas,
    reconcile,
    target_book_to_dict,
)
from analytics.xsmom.replay import replay_targets

_DEFAULT_SNAPSHOT_DIR = Path("docs/plans/xsmom_targets")


def format_target_table(book: TargetBook, deltas: dict[str, float]) -> str:
    """Render the target book as a fixed-width terminal table (pure)."""
    lines = [
        f"XS target positions — as_of {book.as_of_date} → hold "
        f"{book.next_period_date}   capital ${book.capital:,.0f}",
        f"{'SYM':<12}{'SIDE':<7}{'LEV':>8}{'$NOTIONAL':>14}{'Δ$ vs prev':>14}",
    ]
    for p in sorted(book.positions, key=lambda x: -abs(x.leverage)):
        lines.append(
            f"{p.symbol:<12}{p.side:<7}{p.leverage:>+8.3f}"
            f"{p.notional_usd:>+14,.0f}{deltas.get(p.symbol, 0.0):>+14,.0f}"
        )
    lines.append(
        f"governor g={book.governor:.2f}   active={book.active_count}   "
        f"gross={book.gross_leverage:.2f}   net={book.net_leverage:+.2f}"
    )
    return "\n".join(lines)


def write_snapshot(book: TargetBook, snapshot_dir: Path) -> Path:
    """Write the book to `<snapshot_dir>/<next_period_date>.json`."""
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_dir / f"{book.next_period_date}.json"
    path.write_text(json.dumps(target_book_to_dict(book), indent=2))
    return path


def load_latest_snapshot(snapshot_dir: Path) -> dict[str, Any] | None:
    """Most recent snapshot dict by filename, or None if the dir is empty."""
    if not snapshot_dir.exists():
        return None
    files = sorted(snapshot_dir.glob("*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text())  # type: ignore[no-any-return]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    parser.add_argument(
        "--capital", type=float, default=10_000.0, help="Account capital USD"
    )
    parser.add_argument(
        "--config", type=Path, default=None, help="Optional TOML for ForecastConfig"
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Comma-separated override of the universe",
    )
    parser.add_argument("--snapshot-dir", type=Path, default=_DEFAULT_SNAPSHOT_DIR)
    parser.add_argument(
        "--no-snapshot", action="store_true", help="Skip writing the snapshot"
    )
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="Print frozen-clock reconcile diff and exit",
    )
    args = parser.parse_args()

    cfg = ForecastConfig.from_toml(args.config) if args.config else ForecastConfig()
    symbols = args.symbols.split(",") if args.symbols else load_universe()
    conn = duckdb.connect(str(args.db), read_only=True)

    if args.reconcile:
        closes, _ = load_daily_inputs(conn, symbols)
        union = next(iter(closes.values())).index
        cutoff = union[-5]
        diff = reconcile(closes, cfg, cutoff)
        print(f"reconcile @ {cutoff.date().isoformat()}: max abs diff = {diff:.3e}")
        return

    book = replay_targets(conn, cfg, args.capital, symbols=symbols)
    prev = load_latest_snapshot(args.snapshot_dir)
    print(format_target_table(book, position_deltas(book, prev)))
    if not args.no_snapshot:
        path = write_snapshot(book, args.snapshot_dir)
        print(f"\nsnapshot: {path}")


if __name__ == "__main__":
    main()

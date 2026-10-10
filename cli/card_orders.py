"""CLI for the post-card GTX order helper: `card-place` and `card-orders`.

Sibling commands rather than `card place`: `buibui card` owns a positional
SYMBOL, so a nested subcommand would parse as a symbol. The existing card
invocation stays byte-identical.
"""

from __future__ import annotations

import argparse
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from card.config import CardConfig
from card.orders import (
    DEFAULT_ORDERS_PATH,
    XS_LIVE_MARKER,
    check_placement,
    pick_interactive,
    place_orders,
    read_jsonl,
    read_jsonl_counted,
    refresh_orders,
    scan_candidates,
)
from trade.binance_futures import NEVER_PLACED_AFTER_MISSES, BinanceFuturesAdapter


def _universe_symbols(path: Path = Path("config/universe.toml")) -> set[str]:
    """Symbols in the committed research universe, for the XS collision guard.

    `config/universe.toml` nests its list under a `[universe]` table -- the
    top level carries no `symbols` key. Reading the top level returns an
    empty set with no error, which silently disables `check_placement`'s XS
    managed-set guard for every symbol (see `test_universe_symbols_reads_the_
    nested_universe_table`).
    """
    with path.open("rb") as f:
        return set(tomllib.load(f).get("universe", {}).get("symbols", []))


def _now_ms() -> int:
    return int(datetime.now(tz=UTC).timestamp() * 1000)


def run_place(args: argparse.Namespace) -> None:
    from utils.binance_client import create_client

    now_ms = _now_ms()
    order_rows, dropped = read_jsonl_counted(Path(args.ledger))
    if dropped:
        # A dropped ledger line can be a lost PLACEMENT row, and the anti-join
        # below is the only thing stopping a card being placed twice. Loud,
        # before the picklist: an already-placed card may re-present here and
        # look new.
        print(
            f"!! {dropped} unparseable line(s) in {args.ledger} - a lost "
            "placement row means an ALREADY-PLACED card can re-present below. "
            "Check the ledger tail before placing."
        )
    candidates = scan_candidates(
        read_jsonl(Path(args.cards_path)),
        order_rows,
        now_ms,
    )
    if not candidates:
        print("no unexpired unplaced TRADE cards")
        return
    client = create_client()
    adapter = BinanceFuturesAdapter(client, mode="dry_run" if args.dry_run else "live")
    try:
        equity: float | None = adapter.get_equity()
    except Exception:
        equity = None
        print("! equity unavailable - % figures suppressed")
    selected = pick_interactive(candidates, equity, input_fn=input, print_fn=print)
    if not selected:
        print("nothing placed")
        return
    symbols = sorted({c.symbol for c in selected})
    filters = adapter.get_filters(symbols)
    # Load-bearing ORDER: `_universe_symbols()` raises FileNotFoundError on a
    # wrong cwd, so it fails loudly BEFORE `XS_LIVE_MARKER.exists()` is
    # probed. That probe is a relative path too and would answer a silent
    # False from the same wrong cwd, leaving the XS collision guard inert.
    managed = _universe_symbols()
    dual_side = bool(client.futures_get_position_mode().get("dualSidePosition"))
    decisions = [
        check_placement(
            c,
            filters.get(c.symbol),
            managed=c.symbol in managed,
            xs_live_marker=XS_LIVE_MARKER.exists(),
            now_ms=_now_ms(),  # re-read: the operator may have thought a while
        )
        for c in selected
    ]
    for d in decisions:
        for v in d.vetoes:
            print(f"VETO {d.candidate.symbol}: {v}")
        for w in d.warnings:
            print(f"warn {d.candidate.symbol}: {w}")
    rows = place_orders(
        adapter,
        decisions,
        ledger_path=Path(args.ledger),
        dual_side=dual_side,
        marks=adapter.get_marks(symbols),
        books=adapter.get_book_tops(symbols),
        # hedge-aware: this account is dual-side, where get_positions() keeps
        # only the last leg it saw for a symbol
        positions=adapter.get_net_positions(),
        equity=equity,
        now_ms=_now_ms(),
    )
    for row in rows:
        if row.get("terminal_reason") == "gtx_rejected":
            print(
                f"{row['symbol']}: GTX rejected - price already through the "
                "level, the setup is stale (recorded, not an error)"
            )
        else:
            print(
                f"{row['symbol']}: placed order {row['order_id']} "
                f"@ {row['limit_price']} x {row['qty']}"
            )
    if args.dry_run:
        print("dry run - nothing submitted, nothing recorded")


def run_orders(args: argparse.Namespace) -> None:
    ledger = Path(args.ledger)
    if args.refresh:
        from utils.binance_client import create_client

        client = create_client()
        rows = read_jsonl(ledger)
        symbols = sorted(
            {str(r["symbol"]) for r in rows if r.get("kind") in ("placement", "intent")}
        )
        adapter = BinanceFuturesAdapter(client, mode="dry_run")
        written = refresh_orders(
            client, ledger, marks=adapter.get_marks(symbols), now_ms=_now_ms()
        )
        for w in written:
            if w["kind"] == "resolved":
                print(
                    f"{w['symbol']} {w['client_order_id']}: unknown submit resolved "
                    f"- {w['status']} (order {w['order_id']})"
                )
            elif w["kind"] == "lookup_miss":
                print(
                    f"{w['symbol']} {w['client_order_id']}: not found "
                    f"({w['misses']} of {NEVER_PLACED_AFTER_MISSES} lookups)"
                )
        n_terminal = sum(1 for w in written if w["kind"] == "terminal")
        print(f"{n_terminal} order(s) reached a terminal state")
    rows = read_jsonl(ledger)
    terminal = {r["order_id"]: r for r in rows if r.get("kind") == "terminal"}
    resolved = {r["client_order_id"]: r for r in rows if r.get("kind") == "resolved"}
    with_placement = {
        r.get("client_order_id") for r in rows if r.get("kind") == "placement"
    }
    refused = {r.get("client_order_id") for r in rows if r.get("kind") == "refused"}
    for p in rows:
        kind = p.get("kind")
        cid = p.get("client_order_id")
        if kind == "intent":
            # Listed only when the run died before its placement row.
            if cid in with_placement or cid in refused:
                continue
        elif kind != "placement":
            continue
        res = resolved.get(cid) if cid is not None else None
        order_id = p.get("order_id") or (res or {}).get("order_id")
        t = terminal.get(order_id) if order_id else None
        if res is not None and res["status"] == "NEVER_PLACED":
            state = "never_placed"
        elif t is not None:
            state = t["reason"]
        elif res is not None:
            state = "working"
        elif kind == "intent" or p.get("terminal_reason") == "submit_unknown":
            state = "UNKNOWN - --refresh looks it up by client order id"
        else:
            state = p.get("terminal_reason") or "working"
        print(
            f"{p['symbol']:<10} {p.get('direction', '?'):<5} "
            f"{p['limit_price']:>12} x {p['qty']:<8} {state}"
        )


def add_card_orders_subparsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    place = subparsers.add_parser(
        "card-place",
        help="interactive GTX picklist over unexpired TRADE cards",
    )
    place.add_argument("--cards-path", default=CardConfig().cards_path)
    place.add_argument("--ledger", default=DEFAULT_ORDERS_PATH)
    place.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="walk the whole flow, submit nothing, record nothing",
    )
    place.set_defaults(func=run_place)

    orders = subparsers.add_parser(
        "card-orders",
        help="list card order placements; --refresh polls terminal states",
    )
    orders.add_argument("--ledger", default=DEFAULT_ORDERS_PATH)
    orders.add_argument("--refresh", action="store_true")
    orders.set_defaults(func=run_orders)

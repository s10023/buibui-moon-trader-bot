"""Reconstruct journal-ready trade candidates from the Binance Futures account.

Read-only, local one-shot (sibling of ``tools/live_outcomes_report.py``): groups raw
account fills into round-trips + open positions so the ``/journal-trade`` skill can
pre-fill the *mechanical* facts (entries/adds/exit, avg prices, $ PnL, fees, funding,
exchange SL/TP) and leave the *judgement* (thesis, soft stop, tags, retrospective) to
the human. Never places or cancels orders; no DB / schema changes.

Usage::

    poetry run python tools/journal_fetch.py [--days 7] [--symbol BTCUSDT ...] \
        [--json] [--include-journaled]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DEFAULT_JOURNAL_DIR = Path("docs/plans/journal")
_SL_ORDER_TYPES = ("STOP_MARKET", "STOP")
_TP_ORDER_TYPES = ("TAKE_PROFIT_MARKET", "TAKE_PROFIT")
_ZERO_TOL = 1e-9


@dataclass(frozen=True)
class TradeLeg:
    ts_utc: str
    side: str
    price: float
    qty: float
    role: str
    realized_pnl: float
    commission: float


@dataclass
class TradeCandidate:
    symbol: str
    direction: str
    status: str
    position_side: str
    opened_ts_utc: str
    closed_ts_utc: str | None
    legs: list[TradeLeg]
    avg_entry: float
    avg_exit: float | None
    qty_total: float
    realized_pnl_usd: float
    fees_usd: float
    funding_usd: float = 0.0
    exchange_sl: float | None = None
    exchange_tp: float | None = None
    mark_price: float | None = None
    already_journaled: bool = False
    index: int = 0
    suggested_filename: str = ""
    opened_ms: int = 0
    closed_ms: int | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "symbol": self.symbol,
            "direction": self.direction,
            "status": self.status,
            "position_side": self.position_side,
            "opened_ts_utc": self.opened_ts_utc,
            "closed_ts_utc": self.closed_ts_utc,
            "legs": [
                {
                    "ts_utc": leg.ts_utc,
                    "side": leg.side,
                    "price": leg.price,
                    "qty": leg.qty,
                    "role": leg.role,
                    "realized_pnl": leg.realized_pnl,
                    "commission": leg.commission,
                }
                for leg in self.legs
            ],
            "avg_entry": self.avg_entry,
            "avg_exit": self.avg_exit,
            "mark_price": self.mark_price,
            "qty_total": self.qty_total,
            "realized_pnl_usd": self.realized_pnl_usd,
            "fees_usd": self.fees_usd,
            "funding_usd": self.funding_usd,
            "exchange_sl": self.exchange_sl,
            "exchange_tp": self.exchange_tp,
            "already_journaled": self.already_journaled,
            "suggested_filename": self.suggested_filename,
        }


def _iso_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _base_symbol(symbol: str) -> str:
    return (symbol[:-4] if symbol.upper().endswith("USDT") else symbol).lower()


def _suggested_filename(candidate: TradeCandidate) -> str:
    date = candidate.opened_ts_utc[:10]
    return f"{date}-{_base_symbol(candidate.symbol)}-{candidate.direction}.md"


def _is_zero(x: float) -> bool:
    return abs(x) < _ZERO_TOL


def _build_candidate(
    symbol: str, position_side: str, legs: list[TradeLeg], *, closed: bool
) -> TradeCandidate:
    entry_legs = [leg for leg in legs if leg.role in ("entry", "add")]
    exit_legs = [leg for leg in legs if leg.role in ("partial_exit", "exit")]
    qty_total = sum(leg.qty for leg in entry_legs)
    avg_entry = (
        sum(leg.price * leg.qty for leg in entry_legs) / qty_total if qty_total else 0.0
    )
    exit_qty = sum(leg.qty for leg in exit_legs)
    avg_exit = (
        sum(leg.price * leg.qty for leg in exit_legs) / exit_qty
        if closed and exit_qty
        else None
    )
    direction = "long" if entry_legs[0].side == "BUY" else "short"
    opened_iso = legs[0].ts_utc
    closed_iso = legs[-1].ts_utc if closed else None
    candidate = TradeCandidate(
        symbol=symbol,
        direction=direction,
        status="closed" if closed else "open",
        position_side=position_side,
        opened_ts_utc=opened_iso,
        closed_ts_utc=closed_iso,
        legs=legs,
        avg_entry=avg_entry,
        avg_exit=avg_exit,
        qty_total=qty_total,
        realized_pnl_usd=sum(leg.realized_pnl for leg in legs),
        fees_usd=sum(leg.commission for leg in legs),
        opened_ms=_ms_from_iso(opened_iso),
        closed_ms=_ms_from_iso(closed_iso) if closed_iso else None,
    )
    candidate.suggested_filename = _suggested_filename(candidate)
    return candidate


def _ms_from_iso(iso: str) -> int:
    return int(
        datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
        * 1000
    )


def group_fills(symbol: str, fills: list[dict[str, Any]]) -> list[TradeCandidate]:
    """Group one symbol's raw ``futures_account_trades`` fills into trade candidates.

    Pure (no network). Keyed by ``positionSide`` so hedge-mode LONG/SHORT on the same
    symbol stay independent. Walks fills time-ascending tracking signed net qty
    (BUY +qty / SELL -qty); a fill that crosses zero closes the current trade and
    opens a new one with the remainder.
    """
    by_side: dict[str, list[dict[str, Any]]] = {}
    for f in fills:
        by_side.setdefault(str(f.get("positionSide", "BOTH")), []).append(f)

    candidates: list[TradeCandidate] = []
    for position_side, side_fills in by_side.items():
        ordered = sorted(side_fills, key=lambda f: int(f["time"]))
        net = 0.0
        cur_legs: list[TradeLeg] = []
        for f in ordered:
            qty = float(f["qty"])
            price = float(f["price"])
            ts = _iso_utc(int(f["time"]))
            rpnl = float(f.get("realizedPnl", 0) or 0)
            comm = float(f.get("commission", 0) or 0)
            signed = qty if f["side"] == "BUY" else -qty

            if _is_zero(net):
                cur_legs.append(
                    TradeLeg(ts, f["side"], price, qty, "entry", rpnl, comm)
                )
                net = signed
                continue

            if (net > 0) == (signed > 0):  # same direction -> add
                cur_legs.append(TradeLeg(ts, f["side"], price, qty, "add", rpnl, comm))
                net += signed
                continue

            # opposing fill: reduces, fully closes, or flips through zero
            new_net = net + signed
            if _is_zero(new_net):
                cur_legs.append(TradeLeg(ts, f["side"], price, qty, "exit", rpnl, comm))
                candidates.append(
                    _build_candidate(symbol, position_side, cur_legs, closed=True)
                )
                cur_legs = []
                net = 0.0
            elif (new_net > 0) == (net > 0):  # partial reduction, still open
                cur_legs.append(
                    TradeLeg(ts, f["side"], price, qty, "partial_exit", rpnl, comm)
                )
                net = new_net
            else:  # flip through zero: close current, open remainder
                close_qty = abs(net)
                open_qty = abs(new_net)
                close_comm = comm * (close_qty / qty)
                cur_legs.append(
                    TradeLeg(ts, f["side"], price, close_qty, "exit", rpnl, close_comm)
                )
                candidates.append(
                    _build_candidate(symbol, position_side, cur_legs, closed=True)
                )
                cur_legs = [
                    TradeLeg(
                        ts, f["side"], price, open_qty, "entry", 0.0, comm - close_comm
                    )
                ]
                net = new_net

        if cur_legs:
            candidates.append(
                _build_candidate(symbol, position_side, cur_legs, closed=False)
            )
    return candidates


def merge_open_position(
    candidates: list[TradeCandidate], positions: list[dict[str, Any]]
) -> None:
    """Enrich open candidates with authoritative entry/mark price from position info."""
    by_key = {
        (str(p.get("symbol", "")), str(p.get("positionSide", "BOTH"))): p
        for p in positions
        if not _is_zero(float(p.get("positionAmt", 0) or 0))
    }
    for c in candidates:
        if c.status != "open":
            continue
        pos = by_key.get((c.symbol, c.position_side))
        if pos is None:
            continue
        entry = float(pos.get("entryPrice", 0) or 0)
        if entry > 0:
            c.avg_entry = entry
        mark = float(pos.get("markPrice", 0) or 0)
        if mark > 0:
            c.mark_price = mark


def attach_funding(
    candidate: TradeCandidate, income_rows: list[dict[str, Any]]
) -> None:
    """Sum FUNDING_FEE income that accrued within the candidate's hold window."""
    upper = candidate.closed_ms if candidate.closed_ms is not None else _now_ms()
    total = 0.0
    for row in income_rows:
        if row.get("incomeType") != "FUNDING_FEE":
            continue
        t = int(row["time"])
        if candidate.opened_ms <= t <= upper:
            total += float(row.get("income", 0) or 0)
    candidate.funding_usd = total


def attach_exchange_stops(
    candidate: TradeCandidate, sl: float | None, tp: float | None
) -> None:
    candidate.exchange_sl = sl
    candidate.exchange_tp = tp


def _position_side_matches(order_side: str, position_side: str) -> bool:
    if position_side == "BOTH" or order_side == "BOTH":
        return True
    return order_side == position_side


def _stops_from_orders(
    orders: list[dict[str, Any]], symbol: str, position_side: str
) -> tuple[float | None, float | None]:
    sl: float | None = None
    tp: float | None = None
    for o in orders:
        if o.get("symbol") != symbol:
            continue
        if not _position_side_matches(
            str(o.get("positionSide", "BOTH")), position_side
        ):
            continue
        price = float(o.get("stopPrice", 0) or 0)
        if price <= 0:
            continue
        if sl is None and o.get("type") in _SL_ORDER_TYPES:
            sl = price
        elif tp is None and o.get("type") in _TP_ORDER_TYPES:
            tp = price
    return sl, tp


def _parse_journal_key(path: Path) -> tuple[str, str, str] | None:
    """Return (symbol_upper, direction, entry_date) from a journal file's frontmatter."""
    try:
        text = path.read_text()
    except OSError:
        return None
    sym = re.search(r"^symbol:\s*(\S+)", text, re.MULTILINE)
    direction = re.search(r"^direction:\s*(\S+)", text, re.MULTILINE)
    if not sym or not direction:
        return None
    date_match = re.search(
        r"^id:\s*(\d{4}-\d{2}-\d{2})", text, re.MULTILINE
    ) or re.search(r"^entry_ts_utc:\s*\"?(\d{4}-\d{2}-\d{2})", text, re.MULTILINE)
    date = date_match.group(1) if date_match else path.stem[:10]
    return sym.group(1).upper(), direction.group(1).lower(), date


def mark_already_journaled(
    candidates: list[TradeCandidate], journal_dir: Path | None
) -> None:
    """Flag candidates that already have a journal file (symbol + direction + date)."""
    if journal_dir is None or not journal_dir.exists():
        return
    keys = {
        key
        for path in journal_dir.glob("*.md")
        if path.name != "TEMPLATE.md" and (key := _parse_journal_key(path)) is not None
    }
    for c in candidates:
        c.already_journaled = (
            c.symbol.upper(),
            c.direction,
            c.opened_ts_utc[:10],
        ) in keys


def _now_ms() -> int:
    return int(time.time() * 1000)


def _resolve_symbols(client: Any, explicit: list[str] | None) -> list[str]:
    if explicit:
        return explicit
    from utils.binance_client import load_coins_config

    symbols: set[str] = set()
    with contextlib.suppress(Exception):
        symbols |= {str(s) for s in load_coins_config()}
    with contextlib.suppress(Exception):
        for p in client.futures_position_information():
            if not _is_zero(float(p.get("positionAmt", 0) or 0)):
                symbols.add(str(p["symbol"]))
    return sorted(symbols)


def fetch_candidates(
    client: Any,
    symbols: list[str],
    days: int,
    journal_dir: Path | None,
    include_journaled: bool,
) -> list[TradeCandidate]:
    """Assemble enriched, indexed, sorted trade candidates (read-only)."""
    cutoff_ms = _now_ms() - days * 86_400_000
    positions = client.futures_position_information()
    open_orders = client.futures_get_open_orders()

    out: list[TradeCandidate] = []
    for sym in symbols:
        fills = client.futures_account_trades(
            symbol=sym, startTime=cutoff_ms, limit=1000
        )
        cands = [
            c
            for c in group_fills(sym, fills)
            if c.status == "open"
            or (c.closed_ms is not None and c.closed_ms >= cutoff_ms)
        ]
        if not cands:
            continue
        merge_open_position(cands, positions)
        for c in cands:
            if c.status == "open":
                sl, tp = _stops_from_orders(open_orders, sym, c.position_side)
                attach_exchange_stops(c, sl, tp)
        income = client.futures_income_history(
            symbol=sym, incomeType="FUNDING_FEE", startTime=cutoff_ms, limit=1000
        )
        for c in cands:
            attach_funding(c, income)
        out.extend(cands)

    mark_already_journaled(out, journal_dir)
    if not include_journaled:
        out = [c for c in out if not c.already_journaled]

    now = _now_ms()
    out.sort(key=lambda c: (c.status != "open", -(c.closed_ms or now)))
    for i, c in enumerate(out, 1):
        c.index = i
    return out


def _fmt(x: float | None, prec: int = 2) -> str:
    return "—" if x is None else f"{x:,.{prec}f}"


def _print_table(candidates: list[TradeCandidate]) -> None:
    cols = [
        "#",
        "SYMBOL",
        "DIR",
        "STATUS",
        "ENTRY→EXIT",
        "$PNL",
        "FEES",
        "FUND",
        "SL",
        "JRNL",
    ]
    rows: list[list[str]] = []
    for c in candidates:
        rows.append(
            [
                str(c.index),
                c.symbol,
                c.direction,
                c.status,
                f"{_fmt(c.avg_entry)}→{_fmt(c.avg_exit)}",
                _fmt(c.realized_pnl_usd),
                _fmt(c.fees_usd),
                _fmt(c.funding_usd),
                _fmt(c.exchange_sl),
                "✓" if c.already_journaled else "",
            ]
        )
    if not rows:
        print("(no trade candidates in window)")
        return
    widths = [
        max(len(c), max((len(r[i]) for r in rows), default=0))
        for i, c in enumerate(cols)
    ]
    print(" | ".join(c.ljust(w) for c, w in zip(cols, widths, strict=True)))
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(" | ".join(x.ljust(w) for x, w in zip(r, widths, strict=True)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="lookback for closed round-trips (default 7)",
    )
    parser.add_argument(
        "--symbol",
        action="append",
        default=None,
        help="narrow to specific symbols (repeatable); default = coins.json ∪ open positions",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-parseable JSON (skill default)",
    )
    parser.add_argument(
        "--include-journaled",
        action="store_true",
        help="include trades already matched to an existing journal file",
    )
    parser.add_argument(
        "--journal-dir",
        type=Path,
        default=_DEFAULT_JOURNAL_DIR,
        help="journal directory",
    )
    args = parser.parse_args()

    from utils.binance_client import create_client

    client = create_client()
    symbols = _resolve_symbols(client, args.symbol)
    candidates = fetch_candidates(
        client,
        symbols=symbols,
        days=args.days,
        journal_dir=args.journal_dir,
        include_journaled=args.include_journaled,
    )

    if args.json:
        print(json.dumps([c.to_json_dict() for c in candidates], indent=2))
    else:
        _print_table(candidates)


if __name__ == "__main__":
    main()

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
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# A bare `python3 tools/journal_fetch.py` puts `tools/` on sys.path rather than the repo
# root, so the `monitor.*` import below died with ModuleNotFoundError — only the Make
# target and an explicit `PYTHONPATH=.` worked. Per ST129 the guarantee is
# `test_bare_invocation_works`, not this line.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor.position_lib import normalize_conditional_orders  # noqa: E402

_DEFAULT_JOURNAL_DIR = Path("docs/plans/journal")
_SL_ORDER_TYPES = ("STOP_MARKET", "STOP")
_TP_ORDER_TYPES = ("TAKE_PROFIT_MARKET", "TAKE_PROFIT")
_ZERO_TOL = 1e-9


_ALGO_MAX_INTERVAL_MS = 7 * 86_400_000
"""Binance caps /fapi/v1/allAlgoOrders at a 7-day interval.

Measured live 2026-08-07, and HOW you hit the cap decides what you get:
an explicit `startTime`+`endTime` wider than this fails loudly with `-4165`
("Maximum time interval is 7 days"), but a `startTime` with NO `endTime` is
silently clamped to `[startTime, startTime+7d]`. A 30-day lookback in that
second form returned a ~6-day slice from the START of the window — dropping
every recent stop — with `limit` nowhere near reached, so nothing in the
response looked truncated. Same trap `futures_account_trades` already carries.
"""

_ALGO_SOURCE_PREFIXES = (("stToAg", "auto"), ("ios", "phone"))
"""`clientAlgoId` prefixes, verified against live rows.

`stToAg_OTO_*` is the stop Binance auto-attaches to an entry; `ios_*` is a
phone-placed one, i.e. a manual trail. Anything else is some other manual
route. This is the only field that says WHO moved a stop.
"""


@dataclass(frozen=True)
class StopRecord:
    """One stop-loss order in a position's lifetime."""

    ts_ms: int
    trigger_price: float
    qty: float | None
    status: str
    source: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "ts": _iso_utc(self.ts_ms),
            "trigger_price": self.trigger_price,
            "qty": self.qty,
            "status": self.status,
            "source": self.source,
        }


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
    initial_sl: float | None = None
    sl_history: list[StopRecord] = field(default_factory=list)
    mark_price: float | None = None
    already_journaled: bool = False
    truncated: bool = False
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
            # Both ends on purpose: the R basis differs by up to 4.7x between
            # them, and the settled journal rule scores against the stop in
            # force AT ENTRY while noting the trailed R beside it.
            "initial_sl": self.initial_sl,
            "sl_history": [r.to_json_dict() for r in self.sl_history],
            "already_journaled": self.already_journaled,
            # True when the lookback opened mid-position, so the entry legs predate
            # the window: avg_entry / qty_total describe the CLOSE only. Widen --days.
            "truncated": self.truncated,
            "suggested_filename": self.suggested_filename,
        }


def _iso_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _base_symbol(symbol: str) -> str:
    return (symbol[:-4] if symbol.upper().endswith("USDT") else symbol).lower()


def _suggested_filename(candidate: TradeCandidate) -> str:
    """`YYYY-MM-DD-HHMM-<symbol>-<direction>.md`, entry instant in UTC.

    The `HHMM` is not decoration. Without it a second wave on the same symbol,
    direction and UTC day proposes the filename the first one already owns, and
    writing it OVERWRITES a gitignored, single-copy journal entry. The read path
    (`mark_already_journaled`) stopped hiding that second wave on 2026-08-27, so
    the write path had to stop colliding with it in the same change.
    """
    date, hhmm = candidate.opened_ts_utc[:10], candidate.opened_ts_utc[11:16]
    stamp = f"{date}-{hhmm.replace(':', '')}"
    return f"{stamp}-{_base_symbol(candidate.symbol)}-{candidate.direction}.md"


def _is_zero(x: float) -> bool:
    return abs(x) < _ZERO_TOL


def _opens_position(side: str, position_side: str) -> bool:
    """Does this fill INCREASE the position on its own book?

    On a hedge-mode LONG book a BUY opens and a SELL reduces; on a SHORT book it is
    the other way round. Reading every BUY as an open is what let a *cover* start a
    phantom long. ``BOTH`` (one-way mode) keeps the BUY-opens reading, which is the
    only one available there.
    """
    if position_side == "SHORT":
        return side == "SELL"
    return side == "BUY"


def _direction_for(position_side: str, entry_legs: list[TradeLeg]) -> str:
    """Direction of the book these legs belong to.

    In hedge mode ``positionSide`` states it outright, and it is the only reading
    that survives a lookback opening mid-position: on a SHORT book the first fill
    in such a window is a BUY -- a COVER -- which the fill-side heuristic calls a
    long. Measured 2026-08-27 on `--days 3`: all three of the account's same-day
    shorts came back ``long`` and ``open`` against a flat account, with entry and
    exit legs swapped and one round-trip split across two candidates.

    One-way mode (``BOTH``) carries no such field, so the first entry leg's side
    stays the signal; it is correct whenever the window opens flat.
    """
    if position_side in ("LONG", "SHORT"):
        return position_side.lower()
    if not entry_legs:
        return "unknown"
    return "long" if entry_legs[0].side == "BUY" else "short"


def _build_candidate(
    symbol: str,
    position_side: str,
    legs: list[TradeLeg],
    *,
    closed: bool,
    truncated: bool = False,
) -> TradeCandidate:
    entry_legs = [leg for leg in legs if leg.role in ("entry", "add")]
    exit_legs = [leg for leg in legs if leg.role in ("partial_exit", "exit")]
    entry_qty = sum(leg.qty for leg in entry_legs)
    exit_qty = sum(leg.qty for leg in exit_legs)
    # A truncated candidate has no entry legs at all -- its size is what it CLOSED.
    qty_total = entry_qty if entry_qty else exit_qty
    avg_entry = (
        sum(leg.price * leg.qty for leg in entry_legs) / entry_qty if entry_qty else 0.0
    )
    avg_exit = (
        sum(leg.price * leg.qty for leg in exit_legs) / exit_qty
        if closed and exit_qty
        else None
    )
    direction = _direction_for(position_side, entry_legs)
    opened_iso = legs[0].ts_utc
    closed_iso = legs[-1].ts_utc if closed else None
    candidate = TradeCandidate(
        symbol=symbol,
        direction=direction,
        status="truncated" if truncated else ("closed" if closed else "open"),
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
        truncated=truncated,
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
    symbol stay independent. Walks fills time-ascending tracking net qty in the
    direction of the book (see `_opens_position`); a fill that crosses zero closes
    the current trade and opens a new one with the remainder.

    ⚠ **The sign convention is BOOK-RELATIVE, not BUY-positive.** On a hedge-mode
    SHORT book a SELL opens and a BUY reduces, so a lookback that opens
    mid-position starts on a COVER. Treating that as an entry inverted the
    direction, swapped the entry and exit legs, and merged the leading cover with
    the NEXT trade -- measured 2026-08-27, where it split one +$22.81 SOL
    round-trip into a +$9.57 "short" and a +$13.23 "long open" while the account
    was flat. Such leading fills now become their own ``truncated`` candidate
    rather than seeding a phantom one.
    """
    by_side: dict[str, list[dict[str, Any]]] = {}
    for f in fills:
        by_side.setdefault(str(f.get("positionSide", "BOTH")), []).append(f)

    candidates: list[TradeCandidate] = []
    for position_side, side_fills in by_side.items():
        ordered = sorted(side_fills, key=lambda f: int(f["time"]))
        hedged = position_side in ("LONG", "SHORT")
        net = 0.0
        cur_legs: list[TradeLeg] = []
        pre_window_legs: list[TradeLeg] = []
        for f in ordered:
            qty = float(f["qty"])
            price = float(f["price"])
            ts = _iso_utc(int(f["time"]))
            rpnl = float(f.get("realizedPnl", 0) or 0)
            comm = float(f.get("commission", 0) or 0)
            signed = qty if _opens_position(str(f["side"]), position_side) else -qty

            if _is_zero(net):
                if hedged and signed < 0:
                    # Opened mid-position: this fill REDUCES a position whose entry
                    # legs predate the window. Say so instead of inventing an entry.
                    pre_window_legs.append(
                        TradeLeg(ts, f["side"], price, qty, "exit", rpnl, comm)
                    )
                    continue
                if pre_window_legs:
                    candidates.append(
                        _build_candidate(
                            symbol,
                            position_side,
                            pre_window_legs,
                            closed=True,
                            truncated=True,
                        )
                    )
                    pre_window_legs = []
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

        if pre_window_legs:
            candidates.append(
                _build_candidate(
                    symbol,
                    position_side,
                    pre_window_legs,
                    closed=True,
                    truncated=True,
                )
            )
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


def _algo_source(client_algo_id: str) -> str:
    """Who placed this stop: `auto` (exchange-attached), `phone`, or `manual`."""
    head = str(client_algo_id or "").split("_", 1)[0]
    for prefix, label in _ALGO_SOURCE_PREFIXES:
        if head == prefix:
            return label
    return "manual"


def fetch_algo_orders(
    client: Any, symbol: str, now_ms: int, lookback_days: int
) -> list[dict[str, Any]]:
    """Every algo (conditional) order for `symbol` over the lookback, paged.

    python-binance 1.0.37 has no named wrapper for `/fapi/v1/allAlgoOrders`, so
    this calls the signed endpoint directly. Windows are tiled at
    `_ALGO_MAX_INTERVAL_MS` with an EXPLICIT `endTime` — see that constant for
    why the obvious `startTime`-only form loses data silently.

    A window that fails is skipped rather than fatal: partial stop history is
    strictly better than none, and this whole feature is enrichment on top of
    fills that already stand on their own.
    """
    # Deduped by algoId because consecutive windows SHARE their boundary
    # instant: `endTime` is inclusive, so a stop armed exactly on a boundary
    # comes back in both windows and would otherwise be counted as two
    # separate trail steps at the same price.
    seen: set[Any] = set()
    out: list[dict[str, Any]] = []
    start = now_ms - lookback_days * 86_400_000
    while start < now_ms:
        end = min(start + _ALGO_MAX_INTERVAL_MS, now_ms)
        try:
            rows = client._request_futures_api(
                "get",
                "allAlgoOrders",
                True,
                data={
                    "symbol": symbol,
                    "startTime": start,
                    "endTime": end,
                    "limit": 200,
                },
            )
        except Exception as exc:  # noqa: BLE001 - degrade, never abort the fetch
            print(
                f"warning: algo-order history unavailable for {symbol} "
                f"[{_iso_utc(start)}..{_iso_utc(end)}]: {exc}",
                file=sys.stderr,
            )
        else:
            if isinstance(rows, dict):
                rows = rows.get("orders", [])
            for row in rows or []:
                key = row.get("algoId") or (
                    row.get("clientAlgoId"),
                    row.get("createTime"),
                )
                if key in seen:
                    continue
                seen.add(key)
                out.append(row)
        start = end
    return out


def stop_records_from_algo_orders(
    orders: list[dict[str, Any]],
    symbol: str,
    position_side: str,
    opened_ms: int,
    closed_ms: int | None,
) -> list[StopRecord]:
    """Stop orders belonging to one position, oldest first.

    Windowed to the position's own lifetime because a symbol traded repeatedly
    carries the previous trade's stops in the same response. `createTime` is
    the ordering key: it is when the stop was ARMED, which is what makes the
    sequence a trail. Algo rows spell the trigger `triggerPrice` and the size
    `quantity` (classic rows use `stopPrice`/`origQty`), and both arrive as
    strings.

    Statuses are kept verbatim rather than collapsed to a boolean — the live
    vocabulary is `CANCELED` / `EXPIRED` / `FINISHED`. Status alone does NOT
    say how a position ended: an algo stop reads `EXPIRED` when it merely
    expired on the position going flat, measured 2026-08-20 with an exit 2%
    away from its trigger. A stop fired iff the exit price sits within ~0.2%
    of the working trigger; status is a second condition, never the first.
    """
    records: list[StopRecord] = []
    for o in orders:
        if o.get("symbol") != symbol:
            continue
        if o.get("orderType") not in _SL_ORDER_TYPES:
            continue
        if not _position_side_matches(
            str(o.get("positionSide", "BOTH")), position_side
        ):
            continue
        created = int(o.get("createTime", 0) or 0)
        if created < opened_ms or (closed_ms is not None and created > closed_ms):
            continue
        trigger = float(o.get("triggerPrice", 0) or 0)
        if trigger <= 0:
            continue
        raw_qty = o.get("quantity")
        records.append(
            StopRecord(
                ts_ms=created,
                trigger_price=trigger,
                qty=float(raw_qty) if raw_qty is not None else None,
                status=str(o.get("algoStatus", "")),
                source=_algo_source(str(o.get("clientAlgoId", ""))),
            )
        )
    records.sort(key=lambda r: r.ts_ms)
    return records


def attach_stop_history(
    candidate: TradeCandidate, orders: list[dict[str, Any]]
) -> None:
    """Fill `sl_history`, `initial_sl` and `exchange_sl` from algo-order rows.

    `exchange_sl` was `None` for every CLOSED round-trip before this: only
    `openAlgoOrders` was wired, and a closed position has no resting orders
    left. An empty history leaves all three fields untouched, so a symbol with
    no conditional orders behaves exactly as it did.
    """
    records = stop_records_from_algo_orders(
        orders,
        candidate.symbol,
        candidate.position_side,
        candidate.opened_ms,
        candidate.closed_ms,
    )
    if not records:
        return
    candidate.sl_history = records
    candidate.initial_sl = records[0].trigger_price
    candidate.exchange_sl = records[-1].trigger_price


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


_SAME_TRADE_WINDOW_MIN = 30
"""How far a journal entry's stated entry time may sit from a candidate's open.

A journal entry records `entry_ts_utc` as the first entry leg's timestamp, so a
correctly-filled one agrees with `opened_ts_utc` to the minute. The window only
absorbs a hand-rounded time and the basket-prefix skew (measured 8 min: a wave
filed as `-0547-` whose BTC leg entered at 05:55). It is deliberately far below
the 150-minute gap that produced the 2026-08-27 miss, and NARROW is the safe
direction here: too wide hides a real trade (the defect), too narrow merely
offers one twice, which is visible.
"""

_JournalKey = tuple[str, str, str, datetime | None]


def _parse_entry_instant(text: str) -> datetime | None:
    """The entry instant a journal file states, or None if it states none.

    `entry_ts_utc` is canonical (the skill's own pre-fill table). The `id`'s
    optional `-HHMM-` prefix is the fallback, because that is the spelling the
    operator reaches for by hand when disambiguating two waves on one day.
    """
    stated = re.search(
        r"^entry_ts_utc:\s*\"?(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})",
        text,
        re.MULTILINE,
    )
    if stated:
        day, hh, mm = stated.group(1), stated.group(2), stated.group(3)
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(f"{day}T{hh}:{mm}:00+00:00")
    prefixed = re.search(
        r"^id:\s*(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})-", text, re.MULTILINE
    )
    if prefixed:
        day, hh, mm = prefixed.group(1), prefixed.group(2), prefixed.group(3)
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(f"{day}T{hh}:{mm}:00+00:00")
    return None


def _parse_journal_key(path: Path) -> _JournalKey | None:
    """Return (symbol_upper, direction, entry_date, entry_instant|None)."""
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
    return (
        sym.group(1).upper(),
        direction.group(1).lower(),
        date,
        _parse_entry_instant(text),
    )


def _opened_instant(candidate: TradeCandidate) -> datetime | None:
    with contextlib.suppress(ValueError):
        return datetime.fromisoformat(candidate.opened_ts_utc.replace("Z", "+00:00"))
    return None


def _is_same_trade(candidate: TradeCandidate, key: _JournalKey) -> bool:
    symbol, direction, date, stated = key
    if candidate.symbol.upper() != symbol or candidate.direction != direction:
        return False
    opened = _opened_instant(candidate)
    if stated is None or opened is None:
        # A journal entry that states no time can only be matched on the day, which
        # is the pre-2026-08-27 behaviour and keeps legacy entries recognised.
        return candidate.opened_ts_utc[:10] == date
    return abs((opened - stated).total_seconds()) <= _SAME_TRADE_WINDOW_MIN * 60


def mark_already_journaled(
    candidates: list[TradeCandidate], journal_dir: Path | None
) -> None:
    """Flag candidates that already have a journal file.

    Keyed on symbol + direction + entry INSTANT (see `_SAME_TRADE_WINDOW_MIN`).
    Until 2026-08-27 it keyed on the entry DATE alone, so the second wave of a day
    read as journaled the moment the first was filed and then dropped out of the
    default listing entirely — measured: filing the 25-Aug 05:47 UTC BTC/ETH/SOL
    short hid the 03:17 UTC one, invisibly.
    """
    if journal_dir is None or not journal_dir.exists():
        return
    keys = [
        key
        for path in journal_dir.glob("*.md")
        if path.name != "TEMPLATE.md" and (key := _parse_journal_key(path)) is not None
    ]
    for c in candidates:
        c.already_journaled = any(_is_same_trade(c, key) for key in keys)


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
    open_orders = list(client.futures_get_open_orders())
    # UI-placed TP/SL live on /fapi/v1/openAlgoOrders since Binance's 2025-12-09
    # conditional-order migration; classic openOrders no longer returns them.
    with contextlib.suppress(Exception):
        open_orders += normalize_conditional_orders(
            client.futures_get_open_orders(conditional=True)
        )

    out: list[TradeCandidate] = []
    for sym in symbols:
        # No startTime: Binance caps account_trades / income_history to
        # [startTime, startTime+7d] when it is set, which drops the MOST RECENT
        # trades on a >7-day lookback. Fetch the most-recent fills and filter by
        # cutoff in code instead.
        fills = client.futures_account_trades(symbol=sym, limit=1000)
        cands = [
            c
            for c in group_fills(sym, fills)
            if c.status == "open"
            or (c.closed_ms is not None and c.closed_ms >= cutoff_ms)
        ]
        if not cands:
            continue
        merge_open_position(cands, positions)
        # Stop HISTORY first, then the resting-order override below. A closed
        # round-trip has no resting orders at all — which is why `exchange_sl`
        # was None for every one of them — so history is its only source. For
        # an OPEN position the resting order is authoritative for "the stop
        # right now", and its absence genuinely means nothing is protecting
        # the position, so it must be allowed to win even when it is None.
        algo_orders = fetch_algo_orders(
            client, sym, now_ms=_now_ms(), lookback_days=days
        )
        for c in cands:
            attach_stop_history(c, algo_orders)
        for c in cands:
            if c.status == "open":
                sl, tp = _stops_from_orders(open_orders, sym, c.position_side)
                attach_exchange_stops(c, sl, tp)
        income = client.futures_income_history(
            symbol=sym, incomeType="FUNDING_FEE", limit=1000
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


def _sl_cell(candidate: TradeCandidate) -> str:
    """Render the SL column, showing both ends when the stop was trailed.

    One number would hide the move, and the move is the point: the R basis
    differs by up to 4.7x between the stop at entry and the last working one.
    """
    last = _fmt(candidate.exchange_sl)
    first = candidate.initial_sl
    if first is None or candidate.exchange_sl is None:
        return last
    if abs(first - candidate.exchange_sl) < _ZERO_TOL:
        return last
    return f"{_fmt(first)}→{last}"


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
                f"{'?' if c.truncated else _fmt(c.avg_entry)}→{_fmt(c.avg_exit)}",
                _fmt(c.realized_pnl_usd),
                _fmt(c.fees_usd),
                _fmt(c.funding_usd),
                _sl_cell(c),
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
    if any(c.truncated for c in candidates):
        print(
            "\ntruncated = the lookback opened mid-position, so the entry legs are "
            "outside it.\n  Re-run with a wider --days to journal these."
        )


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

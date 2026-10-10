"""Exit manager v1 (#981): rest a stop and ONE maker TP1 partial on an armed position.

The operator arms one position at a time (`buibui exits arm SYMBOL --side
LONG ...`); the manager never touches a position it was not handed. Each poll
of an armed episode:

1. Journals every fill on that symbol and side since arming, so journal
   completeness (#915 unlock condition 2) stops depending on memory.
2. While the side is flat it waits. Binance has no atomic entry + stop + TP
   (#829), so the sequence is entry fills -> detect the fill -> place exits.
3. On the first poll that sees a position it places the stop, then the TP1
   partial, and records both against their ids.
4. Afterwards it compares the exchange's resting orders with what it placed.
   Any difference (a moved, resized or cancelled stop or TP1) is an operator
   edit: the manager stands down for that position, logs it and never
   re-places or repairs anything. A stood-down episode is still journaled
   until the side is flat.
5. Once the side is flat it cancels its own still-resting, unedited TP1 and
   closes the episode with a fee and maker-share summary.

The stop is a `closePosition` STOP_MARKET on mark price, not a sized stop, so
it keeps covering the whole side when the entry fills in pieces, when the
operator adds, and after TP1 takes its part; a sized stop would leave a naked
tail in the first two cases. The TP1 is a post-only (GTX) LIMIT on the
reducing side: a reduce-only partial is exempt from minNotional (#829), so it
can rest as maker at small size. Its quantity is `tp1_frac` of the position
floored to the lot step, and a partial that floors below the minimum lot is
skipped and never sized up (#915 rule 3). The #915 rule also supplies the
position's bet R, and placement warns when the risk at the stop exceeds it.

Every state change is an append-only row in `DEFAULT_LEDGER_PATH`, and the
state is rebuilt from those rows on every poll. An `intent` row is written
before each submit, so a crash between a submit and its result row is
detected on the next poll and stood down as UNKNOWN rather than re-placed.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from card.ledger import _append_line
from card.orders import read_jsonl_counted
from portfolio.sizing import (
    SizingConfig,
    _tick_decimals,
    basket_legs,
    leg_share_usd,
    resolve_bet_unit,
    round_down_to_step,
    round_to_tick,
)
from trade.binance_futures import (
    KEY_OR_IP_REJECTED,
    POST_ONLY_REJECT,
    APIError,
    BinanceFuturesAdapter,
)
from trade.routing import OrderIntent

DEFAULT_LEDGER_PATH = "docs/plans/journal/exit-manager.jsonl"
# A live watch overwrites this after every poll round (#1000). A quiet poll
# writes no ledger row, so the ledger cannot say whether a watcher is running.
DEFAULT_HEARTBEAT_PATH = "docs/plans/journal/exit-manager.heartbeat.json"
# The watcher is missing once its heartbeat is this many intervals old, and
# never sooner than the floor, so a slow round of network calls is not a gap.
HEARTBEAT_STALE_POLLS = 4
HEARTBEAT_STALE_FLOOR_S = 120.0
DEFAULT_TP1_FRAC = 0.5

# Success metric (#981), stated before first live use: maker share of exit
# fills and fee R per trade over the next METRIC_EPISODES journaled episodes,
# against #916's 28 canonical-basis journal entries (2026-07-26..2026-09-08):
# every exit leg taker, fee 0.052R a trade (95% CI 0.042-0.070). Fee R is on
# #916's basis: fees over the risk at the initial stop, not over the bet R.
METRIC_EPISODES = 30
BASELINE_EPISODES = 28
BASELINE_EXIT_MAKER_SHARE = 0.0
BASELINE_FEE_R = 0.052

# Commission assets counted as dollars. A fee paid in anything else (BNB) has
# no price here, so the episode's fee R is left unknown rather than understated.
_USD_ASSETS = frozenset({"USDT", "USDC", "BUSD", "FDUSD"})
_WORKING = frozenset({"NEW", "PARTIALLY_FILLED", "PENDING_CANCEL"})
_PRICE_REL_TOL = 1e-9

STATUS_ARMED = "armed"
STATUS_PROTECTED = "protected"
STATUS_STOOD_DOWN = "stood_down"
STATUS_CLOSED = "closed"
STATUS_DISARMED = "disarmed"
_TERMINAL = frozenset({STATUS_CLOSED, STATUS_DISARMED})

Notify = Callable[[str], None]


class SignedPathRejected(Exception):
    """Binance answered -2015: the key, its IP allowlist or its permissions.

    The key is allowlisted to a dynamic residential IP, so an ISP change makes
    every signed call fail this way with no local symptom (#981). While it
    lasts nothing is managed, so it must reach the operator, never pass as a
    quiet no-op.
    """

    def __init__(self, where: str, exc: BaseException) -> None:
        super().__init__(
            f"Binance -2015 at {where}: API key, IP allowlist or permissions "
            "rejected. The egress IP has probably changed (compare "
            "https://api.ipify.org with the key's allowlist). Exits are NOT "
            f"being managed until it clears. ({exc})"
        )


def _is_key_or_ip_rejection(exc: BaseException) -> bool:
    return getattr(exc, "code", None) == KEY_OR_IP_REJECTED


def _same_price(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=_PRICE_REL_TOL, abs_tol=0.0)


@dataclass
class PlacedLeg:
    leg: str  # "stop" | "tp1"
    price: float  # trigger for the stop, limit for TP1
    qty: float | None  # None for the closePosition stop
    order_id: int | None = None  # TP1 (classic route)
    algo_id: int | None = None  # stop (algo route)
    done: bool = False  # TP1 fully filled


@dataclass
class Episode:
    episode_id: str
    symbol: str
    side: str  # "LONG" | "SHORT"
    stop: float
    tp1: float
    tp1_frac: float
    armed_at_ms: int
    existing: bool
    status: str = STATUS_ARMED
    legs: dict[str, PlacedLeg] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    open_intents: set[str] = field(default_factory=set)
    fills: dict[str, dict[str, Any]] = field(default_factory=dict)
    position_qty_at_placement: float = 0.0
    entry_price_at_placement: float = 0.0
    stand_down_reason: str | None = None
    summary: dict[str, Any] | None = None

    @property
    def entry_side(self) -> str:
        return "BUY" if self.side == "LONG" else "SELL"

    @property
    def exit_side(self) -> str:
        return "SELL" if self.side == "LONG" else "BUY"

    @property
    def active(self) -> bool:
        return self.status not in _TERMINAL

    @property
    def attempted(self) -> bool:
        """Has any submit been tried whose outcome rules out a retry?

        A -2015 refusal (`auth_rejected`) is the one retryable outcome: the
        exchange turned the request away before anything existed, so the next
        poll after the allowlist is fixed may place again.
        """
        return (
            bool(self.open_intents)
            or bool(self.legs)
            or any(o != "auth_rejected" for o in self.failed.values())
        )


# ----- ledger replay -----


def load_episodes(rows: list[dict[str, Any]]) -> list[Episode]:
    """Rebuild every episode from ledger rows, in file order."""
    by_id: dict[str, Episode] = {}
    order: list[str] = []
    for r in rows:
        kind = r.get("kind")
        eid = str(r.get("episode_id", ""))
        if kind == "armed":
            ep = Episode(
                episode_id=eid,
                symbol=str(r["symbol"]),
                side=str(r["side"]),
                stop=float(r["stop"]),
                tp1=float(r["tp1"]),
                tp1_frac=float(r["tp1_frac"]),
                armed_at_ms=int(r["armed_at_ms"]),
                existing=bool(r.get("existing", False)),
            )
            by_id[eid] = ep
            order.append(eid)
            continue
        ep_or_none = by_id.get(eid)
        if ep_or_none is None:
            continue
        ep = ep_or_none
        if kind == "intent":
            ep.open_intents.add(str(r["leg"]))
        elif kind == "placed":
            leg = str(r["leg"])
            ep.open_intents.discard(leg)
            ep.legs[leg] = PlacedLeg(
                leg=leg,
                price=float(r["price"]),
                qty=float(r["qty"]) if r.get("qty") is not None else None,
                order_id=int(r["order_id"]) if r.get("order_id") is not None else None,
                algo_id=int(r["algo_id"]) if r.get("algo_id") is not None else None,
            )
        elif kind == "leg_failed":
            leg = str(r["leg"])
            ep.open_intents.discard(leg)
            ep.failed[leg] = str(r["outcome"])
        elif kind == "leg_done":
            placed = ep.legs.get(str(r["leg"]))
            if placed is not None:
                placed.done = True
        elif kind == "protected":
            ep.status = STATUS_PROTECTED
            ep.position_qty_at_placement = float(r.get("position_qty") or 0.0)
            ep.entry_price_at_placement = float(r.get("entry_price") or 0.0)
        elif kind == "stand_down":
            if ep.status != STATUS_CLOSED:
                ep.status = STATUS_STOOD_DOWN
                ep.stand_down_reason = str(r.get("reason"))
        elif kind == "fill":
            ep.fills[str(r["trade_id"])] = r
        elif kind == "closed":
            ep.status = STATUS_CLOSED
            ep.summary = r.get("summary")
        elif kind == "disarmed":
            ep.status = STATUS_DISARMED
    return [by_id[e] for e in order]


def read_ledger(path: Path) -> tuple[list[Episode], int]:
    """Episodes plus the count of unparseable lines (see `read_jsonl_counted`)."""
    rows, dropped = read_jsonl_counted(path)
    return load_episodes(rows), dropped


def find_active(episodes: list[Episode], symbol: str, side: str) -> Episode | None:
    for ep in episodes:
        if ep.active and ep.symbol == symbol and ep.side == side:
            return ep
    return None


# ----- watcher heartbeat (#1000) -----


def write_heartbeat(
    path: Path, *, now_ms: int, interval_s: float, errors: list[str]
) -> None:
    """Record that a live watch finished a poll round. Replaced, never appended.

    Written through a temp file and a rename, so a reader never sees half a
    file. `errors` carries the round's failures (a -2015 among them): the
    watcher is alive but may be protecting nothing, and the reader says so.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    body = {"at_ms": now_ms, "interval_s": interval_s, "errors": sorted(errors)}
    tmp.write_text(json.dumps(body), encoding="utf-8")
    tmp.replace(path)


def read_heartbeat(path: Path) -> dict[str, Any] | None:
    """The last heartbeat, or None when it is missing or unreadable."""
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(body, dict) or not isinstance(body.get("at_ms"), int):
        return None
    return body


def watcher_gap(
    episodes: list[Episode], heartbeat: dict[str, Any] | None, *, now_ms: int
) -> str | None:
    """Why an active episode has no live watcher, or None when it is covered.

    Every active episode (armed, protected or stood down) needs a running
    `exits watch --live`: an armed entry that fills with no watcher gets no
    exits, and nothing else says so. No active episode means no gap, whatever
    the heartbeat reads.
    """
    active = [ep for ep in episodes if ep.active]
    if not active:
        return None
    names = ", ".join(sorted(f"{ep.symbol} {ep.side}" for ep in active))
    if heartbeat is None:
        return f"{len(active)} active ({names}), no watcher heartbeat on record"
    interval = float(heartbeat.get("interval_s") or 0.0)
    stale_s = max(HEARTBEAT_STALE_POLLS * interval, HEARTBEAT_STALE_FLOOR_S)
    age_s = (now_ms - heartbeat["at_ms"]) / 1000.0
    if age_s > stale_s:
        return f"{len(active)} active ({names}), watcher last seen {age_s / 60:.0f} min ago"
    return None


# ----- arm / disarm -----


def arm(
    adapter: BinanceFuturesAdapter,
    ledger_path: Path,
    *,
    symbol: str,
    side: str,
    stop: float,
    tp1: float,
    tp1_frac: float,
    existing: bool,
    dual_side: bool,
    now_ms: int,
) -> dict[str, Any]:
    """Hand the manager one position. Raises ValueError on anything malformed.

    Arming is per (symbol, side), and only while that side is flat: the
    manager acts on the entry FILL. `existing=True` instead hands it a
    position that is already open, which it protects on the next poll; such
    an episode never counts towards the success metric, because its entry
    fees predate arming.
    """
    if side not in ("LONG", "SHORT"):
        raise ValueError(f"side must be LONG or SHORT, got {side!r}")
    for name, value in (("stop", stop), ("tp1", tp1)):
        if not (math.isfinite(value) and value > 0.0):
            raise ValueError(f"{name} must be a positive price, got {value!r}")
    if side == "LONG" and not stop < tp1:
        raise ValueError(f"a LONG needs stop < tp1, got stop {stop} tp1 {tp1}")
    if side == "SHORT" and not stop > tp1:
        raise ValueError(f"a SHORT needs stop > tp1, got stop {stop} tp1 {tp1}")
    if not (0.0 < tp1_frac < 1.0):
        raise ValueError(f"tp1_frac is a partial, in (0, 1); got {tp1_frac!r}")
    episodes, dropped = read_ledger(ledger_path)
    if dropped:
        raise ValueError(
            f"{dropped} unparseable line(s) in {ledger_path}; repair the ledger "
            "before arming (a lost row can hide an episode already armed)"
        )
    if find_active(episodes, symbol, side) is not None:
        raise ValueError(f"{symbol} {side} is already armed; disarm it first")
    qty, _ = adapter.get_side_position(symbol, side, dual_side=dual_side)
    if qty > 0.0 and not existing:
        raise ValueError(
            f"{symbol} {side} is already open ({qty}); pass --existing to "
            "protect an open position"
        )
    if qty == 0.0 and existing:
        raise ValueError(f"{symbol} {side} has no open position to protect")
    row: dict[str, Any] = {
        "kind": "armed",
        "episode_id": f"{symbol}:{side}:{now_ms}",
        "symbol": symbol,
        "side": side,
        "stop": stop,
        "tp1": tp1,
        "tp1_frac": tp1_frac,
        "existing": existing,
        "armed_at_ms": now_ms,
        "at_ms": now_ms,
    }
    _append_line(ledger_path, row)
    return row


def disarm(ledger_path: Path, *, symbol: str, side: str, now_ms: int) -> str:
    """End an armed episode by hand. Returns what happened, for printing.

    Before any exit was placed this ends the episode. Afterwards it is a
    stand-down: the resting exits become the operator's, and fills keep being
    journaled until the side is flat.
    """
    episodes, _ = read_ledger(ledger_path)
    ep = find_active(episodes, symbol, side)
    if ep is None:
        raise ValueError(f"{symbol} {side} is not armed")
    base = {"episode_id": ep.episode_id, "at_ms": now_ms}
    if ep.status == STATUS_ARMED and not ep.attempted:
        _append_line(ledger_path, {"kind": "disarmed", **base})
        return "disarmed before any exit was placed"
    if ep.status == STATUS_STOOD_DOWN:
        return "already stood down; fills are journaled until the side is flat"
    _append_line(
        ledger_path, {"kind": "stand_down", "reason": "operator disarm", **base}
    )
    return (
        "stood down: resting exits are now yours; fills are journaled until "
        "the side is flat"
    )


# ----- the poll -----


@dataclass
class PollContext:
    adapter: BinanceFuturesAdapter
    ledger_path: Path
    dual_side: bool
    sizing: SizingConfig
    notify: Notify
    now_ms: int

    @property
    def dry_run(self) -> bool:
        return self.adapter.mode == "dry_run"

    def write(self, ep: Episode, kind: str, **fields: Any) -> None:
        """Append one row; a dry run prints it instead and records nothing."""
        row = {"kind": kind, "episode_id": ep.episode_id, "at_ms": self.now_ms}
        row.update(fields)
        if self.dry_run:
            print(f"  [dry-run, not recorded] {kind}: {fields}")
            return
        _append_line(self.ledger_path, row)


def poll_episode(ep: Episode, ctx: PollContext) -> None:
    """Advance one active episode by one poll. See the module docstring."""
    try:
        _record_fills(ep, ctx)
        if ep.status == STATUS_ARMED:
            _poll_armed(ep, ctx)
        else:
            _poll_placed(ep, ctx)
    except APIError as exc:
        if _is_key_or_ip_rejection(exc):
            raise SignedPathRejected(f"{ep.symbol} {ep.side}", exc) from exc
        raise


def _record_fills(ep: Episode, ctx: PollContext) -> None:
    """Journal every new fill on this symbol and side since arming."""
    start = max((int(f["time_ms"]) for f in ep.fills.values()), default=ep.armed_at_ms)
    trades = ctx.adapter.get_account_trades(ep.symbol, start, ctx.now_ms)
    tp1 = ep.legs.get("tp1")
    for t in trades:
        trade_id = str(t.get("id"))
        if trade_id in ep.fills:
            continue
        if ctx.dual_side and t.get("positionSide") != ep.side:
            continue
        order_id = int(t["orderId"]) if t.get("orderId") is not None else None
        if tp1 is not None and order_id == tp1.order_id:
            role = "tp1"
        elif t.get("side") == ep.entry_side:
            role = "entry"
        else:
            role = "exit"
        row = {
            "trade_id": trade_id,
            "order_id": order_id,
            "time_ms": int(t["time"]),
            "symbol": ep.symbol,
            "side": t.get("side"),
            "position_side": t.get("positionSide"),
            "role": role,
            "price": float(t["price"]),
            "qty": float(t["qty"]),
            "maker": bool(t.get("maker", False)),
            "commission": float(t.get("commission") or 0.0),
            "commission_asset": t.get("commissionAsset"),
            "realized_pnl": float(t.get("realizedPnl") or 0.0),
        }
        ctx.write(ep, "fill", **row)
        ep.fills[trade_id] = row


def _poll_armed(ep: Episode, ctx: PollContext) -> None:
    if ep.attempted:
        # A submit was attempted and never reached `protected` or a stand-down:
        # the run that tried it died between submit and result. What exists on
        # the exchange is unknown, so stand down rather than place twice.
        reason = (
            "placement interrupted (submit outcome never recorded for "
            f"{sorted(ep.open_intents) or 'a leg'}); check the exchange by hand"
        )
        ctx.write(ep, "stand_down", reason=reason)
        ctx.notify(f"exit-manager {ep.symbol} {ep.side}: STOOD DOWN - {reason}")
        return
    qty, entry_price = ctx.adapter.get_side_position(
        ep.symbol, ep.side, dual_side=ctx.dual_side
    )
    if qty <= 0.0:
        return
    _place_exits(ep, ctx, qty, entry_price)


def _bet_check(
    ep: Episode, ctx: PollContext, qty: float, entry_price: float, stop: float
) -> dict[str, Any]:
    """The position's risk at the stop against its #915 bet R (leg share)."""
    try:
        equity: float | None = ctx.adapter.get_equity()
    except Exception:  # noqa: BLE001 - sizing context only; never blocks a stop
        equity = None
    unit = resolve_bet_unit(ctx.sizing, equity)
    share = leg_share_usd(unit.r_usd, basket_legs(ep.symbol, ctx.sizing))
    risk_usd = qty * abs(entry_price - stop) if entry_price > 0.0 else None
    risk_vs_share = risk_usd / share if risk_usd is not None and share > 0.0 else None
    return {
        "risk_usd": risk_usd,
        "bet_share_usd": share,
        "risk_vs_bet_share": risk_vs_share,
        "sizing_regime": unit.regime,
        "basis_source": unit.basis_source,
    }


def _place_exits(ep: Episode, ctx: PollContext, qty: float, entry_price: float) -> None:
    adapter = ctx.adapter
    filt = adapter.get_filters([ep.symbol]).get(ep.symbol)
    mark = adapter.get_marks([ep.symbol]).get(ep.symbol)
    tick = filt.price_tick if filt is not None else 0.0
    stop_px = round_to_tick(ep.stop, tick, ep.exit_side)
    tp1_px = round_to_tick(ep.tp1, tick, ep.exit_side)
    label = f"exit-manager {ep.symbol} {ep.side}"

    # A stop already through mark triggers on arrival (Binance refuses it
    # -2021); placing nothing and saying so beats a market exit nobody chose.
    through = mark is not None and (
        (ep.side == "LONG" and stop_px >= mark)
        or (ep.side == "SHORT" and stop_px <= mark)
    )
    if mark is None or through:
        why = (
            "no mark price"
            if mark is None
            else f"stop {stop_px} is already through mark {mark}"
        )
        reason = f"entry filled ({qty}) but nothing placed: {why}; protect it by hand"
        ctx.write(ep, "stand_down", reason=reason)
        ctx.notify(f"{label}: STOOD DOWN - {reason}")
        return

    # TP1 size: tp1_frac of the position, floored to the lot step and
    # quantised to the step's decimals (python-binance sends a bare str(), so
    # float noise reaches the wire as -1111). Below the minimum lot it is
    # skipped, never sized up (#915 rule 3); reduce-only waives minNotional.
    tp1_qty = 0.0
    if filt is not None and filt.qty_step > 0.0:
        tp1_qty = round_down_to_step(qty * ep.tp1_frac, filt.qty_step)
        tp1_qty = round(tp1_qty, _tick_decimals(filt.qty_step))
    tp1_sublot = filt is None or tp1_qty <= 0.0 or tp1_qty < filt.min_qty

    position_side = ep.side if ctx.dual_side else None
    stop_intent = OrderIntent(
        ep.symbol,
        ep.exit_side,
        0.0,
        True,
        0.0,
        "exit_stop",
        "STOP_MARKET",
        position_side=position_side,
        stop_price=stop_px,
        close_position=True,
    )
    ctx.write(ep, "intent", leg="stop", stop_price=stop_px, close_position=True)
    try:
        resp = adapter.submit(stop_intent)
    except APIError as exc:
        if _is_key_or_ip_rejection(exc):
            ctx.write(
                ep, "leg_failed", leg="stop", outcome="auth_rejected", error=repr(exc)
            )
            raise SignedPathRejected(f"{ep.symbol} {ep.side} stop", exc) from exc
        ctx.write(ep, "leg_failed", leg="stop", outcome="refused", error=repr(exc))
        reason = f"stop REFUSED by the exchange ({exc}); position NOT protected"
        ctx.write(ep, "stand_down", reason=reason)
        ctx.notify(f"{label}: STOOD DOWN - {reason}")
        return
    except Exception as exc:  # noqa: BLE001 - unknown is recorded, then surfaced
        ctx.write(ep, "leg_failed", leg="stop", outcome="unknown", error=repr(exc))
        reason = f"stop outcome UNKNOWN ({exc!r}); check openAlgoOrders by hand"
        ctx.write(ep, "stand_down", reason=reason)
        ctx.notify(f"{label}: STOOD DOWN - {reason}")
        return
    if resp.get("dryRun"):
        print(f"  [dry-run] would rest stop {stop_px} closePosition on {ep.symbol}")
    else:
        ctx.write(
            ep,
            "placed",
            leg="stop",
            price=stop_px,
            qty=None,
            algo_id=int(resp["algoId"]),
        )

    tp1_note: str
    tp1_unknown = False
    if tp1_sublot:
        step = filt.qty_step if filt is not None else None
        tp1_note = f"TP1 skipped: {qty} x {ep.tp1_frac} floors below a lot ({step})"
        ctx.write(ep, "leg_failed", leg="tp1", outcome="skipped_sublot", error=tp1_note)
    else:
        tp1_intent = OrderIntent(
            ep.symbol,
            ep.exit_side,
            tp1_qty,
            True,
            0.0,
            "exit_tp1",
            "LIMIT",
            position_side=position_side,
        )
        ctx.write(ep, "intent", leg="tp1", price=tp1_px, qty=tp1_qty)
        try:
            resp = adapter.submit(tp1_intent, price=tp1_px)
        except APIError as exc:
            code = getattr(exc, "code", None)
            outcome = (
                "gtx_rejected"
                if code == POST_ONLY_REJECT
                else "auth_rejected"
                if code == KEY_OR_IP_REJECTED
                else "refused"
            )
            tp1_note = f"TP1 {outcome} ({exc}); the stop still protects"
            ctx.write(ep, "leg_failed", leg="tp1", outcome=outcome, error=repr(exc))
        except Exception as exc:  # noqa: BLE001
            tp1_unknown = True
            tp1_note = f"TP1 outcome UNKNOWN ({exc!r}); check openOrders by hand"
            ctx.write(ep, "leg_failed", leg="tp1", outcome="unknown", error=repr(exc))
        else:
            if resp.get("dryRun"):
                tp1_note = f"[dry-run] would rest TP1 {tp1_qty} @ {tp1_px} GTX"
            else:
                ctx.write(
                    ep,
                    "placed",
                    leg="tp1",
                    price=tp1_px,
                    qty=tp1_qty,
                    order_id=int(resp["orderId"]),
                )
                tp1_note = f"TP1 {tp1_qty} @ {tp1_px} resting (GTX)"

    check = _bet_check(ep, ctx, qty, entry_price, stop_px)
    warnings: list[str] = []
    ratio = check["risk_vs_bet_share"]
    if ratio is not None and ratio > 1.0:
        warnings.append(
            f"risk at stop ${check['risk_usd']:.2f} is {ratio:.2f}x its #915 bet "
            f"share ${check['bet_share_usd']:.2f}"
        )
    ctx.write(
        ep,
        "protected",
        position_qty=qty,
        entry_price=entry_price,
        warnings=warnings,
        **check,
    )
    msg = (
        f"{label}: entry filled {qty} @ {entry_price}; stop {stop_px} "
        f"(closePosition, mark) resting. {tp1_note}."
    )
    for w in warnings:
        msg += f" WARNING: {w}."
    ctx.notify(msg)
    if tp1_unknown:
        reason = "TP1 outcome unknown; nothing more is managed on this position"
        ctx.write(ep, "stand_down", reason=reason)
        ctx.notify(f"{label}: STOOD DOWN - {reason}")


def _detect_edits(
    ep: Episode, algo_rows: list[dict[str, Any]], tp1_order: dict[str, Any] | None
) -> tuple[list[str], bool]:
    """Differences between the exchange and what was placed; and TP1 filled?"""
    edits: list[str] = []
    stop = ep.legs.get("stop")
    if stop is not None:
        row = next(
            (r for r in algo_rows if str(r.get("algoId")) == str(stop.algo_id)), None
        )
        if row is None:
            edits.append(
                f"stop {stop.algo_id} is no longer resting (cancelled or replaced)"
            )
        else:
            trigger = float(row.get("triggerPrice") or 0.0)
            if not _same_price(trigger, stop.price):
                edits.append(f"stop moved {stop.price} -> {trigger}")
    tp1_filled = False
    tp1 = ep.legs.get("tp1")
    if tp1 is not None and not tp1.done and tp1_order is not None:
        status = str(tp1_order.get("status"))
        if status == "FILLED":
            tp1_filled = True
        elif status in _WORKING:
            price = float(tp1_order.get("price") or 0.0)
            orig = float(tp1_order.get("origQty") or 0.0)
            if not _same_price(price, tp1.price):
                edits.append(f"TP1 moved {tp1.price} -> {price}")
            if tp1.qty is not None and not _same_price(orig, tp1.qty):
                edits.append(f"TP1 resized {tp1.qty} -> {orig}")
        else:
            edits.append(f"TP1 {tp1.order_id} is {status}")
    return edits, tp1_filled


def _poll_placed(ep: Episode, ctx: PollContext) -> None:
    adapter = ctx.adapter
    label = f"exit-manager {ep.symbol} {ep.side}"
    managing = ep.status == STATUS_PROTECTED
    algo_rows: list[dict[str, Any]] = []
    tp1_order: dict[str, Any] | None = None
    tp1 = ep.legs.get("tp1")
    if managing:
        # Orders are read BEFORE the position: a stop that fired between the
        # two reads then shows as resting-and-flat (a close), never as
        # missing-and-open (a false operator edit).
        algo_rows = adapter.get_open_algo_orders(ep.symbol)
        if tp1 is not None and not tp1.done and tp1.order_id is not None:
            tp1_order = adapter.get_order(ep.symbol, tp1.order_id)
    qty, _ = adapter.get_side_position(ep.symbol, ep.side, dual_side=ctx.dual_side)

    if qty <= 0.0:
        if managing and tp1 is not None and tp1_order is not None:
            edits, _ = _detect_edits(ep, algo_rows, tp1_order)
            tp1_status = str(tp1_order.get("status"))
            tp1_edited = any(e.startswith("TP1") for e in edits)
            if tp1_status in _WORKING and not tp1_edited:
                adapter.cancel_order(ep.symbol, order_id=tp1.order_id)
                ctx.write(ep, "cancelled", leg="tp1", order_id=tp1.order_id)
        summary = summarize(ep)
        ctx.write(ep, "closed", summary=summary)
        fee = summary.get("fee_r")
        fee_text = f"{fee:.3f}R" if fee is not None else "unknown"
        ctx.notify(
            f"{label}: CLOSED. exit fills {summary['exit_fills']} "
            f"({summary['maker_exit_fills']} maker); fee {fee_text}"
        )
        return

    if not managing:
        return
    edits, tp1_filled = _detect_edits(ep, algo_rows, tp1_order)
    if tp1_filled and tp1 is not None:
        ctx.write(ep, "leg_done", leg="tp1", order_id=tp1.order_id)
        tp1.done = True
        ctx.notify(f"{label}: TP1 filled; the stop covers the remaining {qty}")
    if edits:
        reason = "operator edit: " + "; ".join(edits)
        ctx.write(ep, "stand_down", reason=reason)
        ctx.notify(
            f"{label}: STOOD DOWN - {reason}. Nothing will be re-placed; fills "
            "are journaled until the side is flat."
        )


# ----- summary and the success metric -----


def summarize(ep: Episode) -> dict[str, Any]:
    """One episode's fees and exit maker share, on #916's fee-R basis.

    Risk is entry qty x |avg entry - initial stop| (the stop as placed, else
    as armed). Fee R is total commission (entry and exit) over that risk.
    """
    fills = sorted(ep.fills.values(), key=lambda f: int(f["time_ms"]))
    entries = [f for f in fills if f["role"] == "entry"]
    exits = [f for f in fills if f["role"] in ("tp1", "exit")]
    entry_qty = sum(float(f["qty"]) for f in entries)
    if entry_qty > 0.0:
        avg_entry = (
            sum(float(f["price"]) * float(f["qty"]) for f in entries) / entry_qty
        )
    else:
        entry_qty = ep.position_qty_at_placement
        avg_entry = ep.entry_price_at_placement
    stop = ep.legs["stop"].price if "stop" in ep.legs else ep.stop
    risk_usd = entry_qty * abs(avg_entry - stop) if avg_entry > 0.0 else 0.0
    usd_fees = [f for f in fills if f.get("commission_asset") in _USD_ASSETS]
    non_usd = len(fills) - len(usd_fees)
    commission = sum(float(f["commission"]) for f in usd_fees)
    pnl = sum(float(f["realized_pnl"]) for f in fills)
    fee_r = commission / risk_usd if risk_usd > 0.0 and non_usd == 0 else None
    exit_qty = sum(float(f["qty"]) for f in exits)
    maker_exits = [f for f in exits if f["maker"]]
    maker_qty = sum(float(f["qty"]) for f in maker_exits)
    return {
        "existing": ep.existing,
        "metric_eligible": (not ep.existing) and bool(entries) and bool(exits),
        "stood_down": ep.stand_down_reason,
        "entry_qty": entry_qty,
        "avg_entry": avg_entry,
        "initial_stop": stop,
        "risk_usd": risk_usd,
        "commission_usd": commission,
        "non_usd_fee_fills": non_usd,
        "fee_r": fee_r,
        "realized_pnl_usd": pnl,
        "net_r": (pnl - commission) / risk_usd if fee_r is not None else None,
        "exit_fills": len(exits),
        "maker_exit_fills": len(maker_exits),
        "maker_exit_qty_share": maker_qty / exit_qty if exit_qty > 0.0 else None,
    }


def metric_report(episodes: list[Episode]) -> dict[str, Any]:
    """The #981 success metric over the first METRIC_EPISODES eligible closes."""
    closed = [
        ep.summary
        for ep in episodes
        if ep.status == STATUS_CLOSED and ep.summary and ep.summary["metric_eligible"]
    ]
    window = closed[:METRIC_EPISODES]
    exit_fills = sum(int(s["exit_fills"]) for s in window)
    maker_fills = sum(int(s["maker_exit_fills"]) for s in window)
    fee_rs = [float(s["fee_r"]) for s in window if s.get("fee_r") is not None]
    return {
        "episodes": len(window),
        "target_episodes": METRIC_EPISODES,
        "exit_fills": exit_fills,
        "maker_exit_share": maker_fills / exit_fills if exit_fills else None,
        "fee_r_mean": sum(fee_rs) / len(fee_rs) if fee_rs else None,
        "fee_r_n": len(fee_rs),
        "baseline_episodes": BASELINE_EPISODES,
        "baseline_maker_exit_share": BASELINE_EXIT_MAKER_SHARE,
        "baseline_fee_r": BASELINE_FEE_R,
    }

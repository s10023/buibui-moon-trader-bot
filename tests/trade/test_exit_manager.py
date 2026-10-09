"""Exit manager v1 (#981), driven against a stateful fake exchange.

No test here places a real order: the fake records every create and cancel,
and the live-mode adapter wraps it exactly as it would wrap python-binance.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from portfolio.sizing import SizingConfig
from trade.binance_futures import APIError, BinanceFuturesAdapter
from trade.exit_manager import (
    STATUS_CLOSED,
    STATUS_PROTECTED,
    STATUS_STOOD_DOWN,
    PollContext,
    SignedPathRejected,
    arm,
    disarm,
    metric_report,
    poll_episode,
    read_ledger,
)

SYM = "AAAUSDT"


def _api_error(code: int, msg: str = "x") -> APIError:
    """Built through whichever class trade.binance_futures binds (see test_card_orders)."""
    text = json.dumps({"code": code, "msg": msg})
    resp = SimpleNamespace(status_code=400, text=text)
    try:
        err: APIError = APIError(resp, 400, text)
    except TypeError:  # the local shim takes a plain message
        err = APIError(msg)
        err.code = code
    assert getattr(err, "code", None) == code
    return err


class FakeExchange:
    """Just enough of python-binance's futures surface, with state."""

    def __init__(self, *, dual: bool = True) -> None:
        self.dual = dual
        self.positions: dict[str, tuple[float, float]] = {}  # side -> (amt, entry)
        self.algo: dict[int, dict[str, Any]] = {}
        self.orders: dict[int, dict[str, Any]] = {}
        self.trades: list[dict[str, Any]] = []
        self.created: list[dict[str, Any]] = []
        self.cancelled: list[dict[str, Any]] = []
        self.create_errors: dict[str, BaseException] = {}
        self.read_error: BaseException | None = None
        self.mark = 100.0
        self._next = 1000

    # reads
    def futures_get_position_mode(self) -> dict[str, Any]:
        return {"dualSidePosition": self.dual}

    def futures_position_information(self, **_: Any) -> list[dict[str, Any]]:
        if self.read_error is not None:
            raise self.read_error
        if self.dual:
            return [
                {
                    "symbol": SYM,
                    "positionSide": side,
                    "positionAmt": str(self.positions.get(side, (0.0, 0.0))[0]),
                    "entryPrice": str(self.positions.get(side, (0.0, 0.0))[1]),
                }
                for side in ("LONG", "SHORT")
            ]
        amt, entry = self.positions.get("BOTH", (0.0, 0.0))
        return [
            {
                "symbol": SYM,
                "positionSide": "BOTH",
                "positionAmt": str(amt),
                "entryPrice": str(entry),
            }
        ]

    def futures_exchange_info(self) -> dict[str, Any]:
        return {
            "symbols": [
                {
                    "symbol": SYM,
                    "filters": [
                        {
                            "filterType": "LOT_SIZE",
                            "stepSize": "0.001",
                            "minQty": "0.001",
                        },
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    ],
                }
            ]
        }

    def futures_mark_price(self) -> list[dict[str, Any]]:
        return [{"symbol": SYM, "markPrice": str(self.mark)}]

    def futures_account(self) -> dict[str, Any]:
        return {"totalMarginBalance": "1000"}

    def futures_get_open_orders(
        self, symbol: str | None = None, conditional: bool = False
    ) -> list[dict[str, Any]]:
        if conditional:
            return [dict(r) for r in self.algo.values() if r["algoStatus"] == "NEW"]
        return [dict(r) for r in self.orders.values() if r["status"] == "NEW"]

    def futures_get_order(self, symbol: str, orderId: int) -> dict[str, Any]:
        return dict(self.orders[orderId])

    def futures_account_trades(
        self, symbol: str, startTime: int, endTime: int, limit: int
    ) -> list[dict[str, Any]]:
        if self.read_error is not None:
            raise self.read_error
        return [t for t in self.trades if startTime <= t["time"] <= endTime]

    # writes
    def futures_create_order(self, **params: Any) -> dict[str, Any]:
        self.created.append(dict(params))
        err = self.create_errors.get(params["type"])
        if err is not None:
            raise err
        self._next += 1
        if params["type"] == "STOP_MARKET":
            self.algo[self._next] = {
                "algoId": self._next,
                "symbol": SYM,
                "orderType": "STOP_MARKET",
                "triggerPrice": str(params["stopPrice"]),
                "positionSide": params.get("positionSide", "BOTH"),
                "closePosition": True,
                "algoStatus": "NEW",
            }
            return {"algoId": self._next}
        self.orders[self._next] = {
            "orderId": self._next,
            "status": "NEW",
            "price": str(params["price"]),
            "origQty": str(params["quantity"]),
        }
        return {"orderId": self._next, "status": "NEW"}

    def futures_cancel_order(self, **params: Any) -> dict[str, Any]:
        self.cancelled.append(dict(params))
        if "algoId" in params:
            self.algo[params["algoId"]]["algoStatus"] = "CANCELED"
        else:
            self.orders[params["orderId"]]["status"] = "CANCELED"
        return {}

    # helpers
    def fill(self, side: str, qty: float, entry: float) -> None:
        self.positions[side if self.dual else "BOTH"] = (qty, entry)

    def trade(self, tid: int, t: int, **kw: Any) -> None:
        row = {
            "id": tid,
            "time": t,
            "orderId": 1,
            "side": "BUY",
            "positionSide": "LONG",
            "price": "100",
            "qty": "1",
            "maker": False,
            "commission": "0.05",
            "commissionAsset": "USDT",
            "realizedPnl": "0",
        }
        row.update(kw)
        self.trades.append(row)


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    return tmp_path / "exit-manager.jsonl"


def _arm(ex: FakeExchange, ledger: Path, **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "symbol": SYM,
        "side": "LONG",
        "stop": 95.0,
        "tp1": 110.0,
        "tp1_frac": 0.5,
        "existing": False,
        "dual_side": ex.dual,
        "now_ms": 1_000,
    }
    args.update(kw)
    return arm(BinanceFuturesAdapter(ex, mode="dry_run"), ledger, **args)


def _poll(
    ex: FakeExchange, ledger: Path, now: int = 2_000, mode: str = "live"
) -> list[str]:
    notes: list[str] = []
    episodes, dropped = read_ledger(ledger)
    assert dropped == 0
    for ep in [e for e in episodes if e.active]:
        ctx = PollContext(
            adapter=BinanceFuturesAdapter(ex, mode=mode),
            ledger_path=ledger,
            dual_side=ex.dual,
            sizing=SizingConfig(),
            notify=notes.append,
            now_ms=now,
        )
        poll_episode(ep, ctx)
    return notes


def _episode(ledger: Path) -> Any:
    (ep,) = read_ledger(ledger)[0]
    return ep


# ----- arming -----


def test_arm_refuses_an_open_side_unless_existing(ledger: Path) -> None:
    ex = FakeExchange()
    ex.fill("LONG", 2.0, 100.0)
    with pytest.raises(ValueError, match="already open"):
        _arm(ex, ledger)
    assert _arm(ex, ledger, existing=True)["existing"] is True


def test_arm_refuses_a_second_episode_and_inverted_geometry(ledger: Path) -> None:
    ex = FakeExchange()
    with pytest.raises(ValueError, match="stop < tp1"):
        _arm(ex, ledger, stop=110.0, tp1=95.0)
    with pytest.raises(ValueError, match="partial"):
        _arm(ex, ledger, tp1_frac=1.0)
    _arm(ex, ledger)
    with pytest.raises(ValueError, match="already armed"):
        _arm(ex, ledger)


def test_a_flat_side_places_nothing(ledger: Path) -> None:
    ex = FakeExchange()
    _arm(ex, ledger)
    assert _poll(ex, ledger) == []
    assert ex.created == []


# ----- placement -----


def test_entry_fill_rests_close_position_stop_then_gtx_tp1_partial(
    ledger: Path,
) -> None:
    ex = FakeExchange()
    _arm(ex, ledger)
    ex.fill("LONG", 2.5, 100.0)
    notes = _poll(ex, ledger)

    stop, tp1 = ex.created
    assert stop["type"] == "STOP_MARKET"
    assert stop["closePosition"] == "true"
    assert "quantity" not in stop and "reduceOnly" not in stop
    assert stop["positionSide"] == "LONG" and stop["side"] == "SELL"
    assert stop["stopPrice"] == 95.0 and stop["workingType"] == "MARK_PRICE"
    assert tp1["type"] == "LIMIT" and tp1["timeInForce"] == "GTX"
    assert tp1["quantity"] == 1.25 and tp1["price"] == 110.0
    assert "reduceOnly" not in tp1  # hedge mode: positionSide implies it
    ep = _episode(ledger)
    assert ep.status == STATUS_PROTECTED
    assert ep.legs["stop"].algo_id is not None and ep.legs["tp1"].order_id is not None
    assert len(notes) == 1 and "resting" in notes[0]


def test_one_way_account_sends_reduce_only_on_tp1_only(ledger: Path) -> None:
    ex = FakeExchange(dual=False)
    _arm(ex, ledger)
    ex.fill("LONG", 2.0, 100.0)
    _poll(ex, ledger)
    stop, tp1 = ex.created
    assert "reduceOnly" not in stop and "positionSide" not in stop
    assert tp1["reduceOnly"] is True


def test_sub_lot_tp1_is_skipped_never_sized_up(ledger: Path) -> None:
    """#915 rule 3: 0.001 x 0.5 floors to zero, so only the stop rests."""
    ex = FakeExchange()
    _arm(ex, ledger)
    ex.fill("LONG", 0.001, 100.0)
    notes = _poll(ex, ledger)
    assert [c["type"] for c in ex.created] == ["STOP_MARKET"]
    assert _episode(ledger).failed == {"tp1": "skipped_sublot"}
    assert "skipped" in notes[0]


def test_refused_stop_stands_down_and_places_no_tp1(ledger: Path) -> None:
    ex = FakeExchange()
    ex.create_errors["STOP_MARKET"] = _api_error(-4130, "closePosition stop exists")
    _arm(ex, ledger)
    ex.fill("LONG", 2.0, 100.0)
    notes = _poll(ex, ledger)
    assert [c["type"] for c in ex.created] == ["STOP_MARKET"]
    assert _episode(ledger).status == STATUS_STOOD_DOWN
    assert "NOT protected" in notes[0]


def test_unacknowledged_stop_is_unknown_not_refused(ledger: Path) -> None:
    """#829: a 2xx body with no algoId means the stop MAY exist."""
    ex = FakeExchange()
    ex.create_errors["STOP_MARKET"] = RuntimeError("no algoId in body")
    _arm(ex, ledger)
    ex.fill("LONG", 2.0, 100.0)
    notes = _poll(ex, ledger)
    ep = _episode(ledger)
    assert ep.status == STATUS_STOOD_DOWN and ep.failed["stop"] == "unknown"
    assert "UNKNOWN" in notes[0]


def test_gtx_rejected_tp1_keeps_the_stop_and_stays_protected(ledger: Path) -> None:
    ex = FakeExchange()
    ex.create_errors["LIMIT"] = _api_error(-5022, "Post Only order will be rejected")
    _arm(ex, ledger)
    ex.fill("LONG", 2.0, 100.0)
    _poll(ex, ledger)
    ep = _episode(ledger)
    assert ep.status == STATUS_PROTECTED and ep.failed["tp1"] == "gtx_rejected"


def test_stop_already_through_mark_places_nothing(ledger: Path) -> None:
    ex = FakeExchange()
    ex.mark = 94.0
    _arm(ex, ledger)
    ex.fill("LONG", 2.0, 100.0)
    notes = _poll(ex, ledger)
    assert ex.created == []
    assert _episode(ledger).status == STATUS_STOOD_DOWN
    assert "through mark" in notes[0]


def test_an_interrupted_placement_is_stood_down_not_placed_again(ledger: Path) -> None:
    """An intent with no result row means a run died mid-submit."""
    ex = FakeExchange()
    row = _arm(ex, ledger)
    with ledger.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {"kind": "intent", "episode_id": row["episode_id"], "leg": "stop"}
            )
            + "\n"
        )
    ex.fill("LONG", 2.0, 100.0)
    notes = _poll(ex, ledger)
    assert ex.created == []
    assert _episode(ledger).status == STATUS_STOOD_DOWN
    assert "interrupted" in notes[0]


def test_dry_run_places_and_records_nothing(ledger: Path) -> None:
    ex = FakeExchange()
    _arm(ex, ledger)
    before = ledger.read_text(encoding="utf-8")
    ex.fill("LONG", 2.0, 100.0)
    _poll(ex, ledger, mode="dry_run")
    assert ex.created == []
    assert ledger.read_text(encoding="utf-8") == before


# ----- operator edits win -----


def _protected(ledger: Path, **arm_kw: Any) -> FakeExchange:
    ex = FakeExchange()
    _arm(ex, ledger, **arm_kw)
    ex.fill("LONG", 2.0, 100.0)
    _poll(ex, ledger)
    assert _episode(ledger).status == STATUS_PROTECTED
    return ex


def test_moved_stop_stands_down_and_is_never_repaired(ledger: Path) -> None:
    ex = _protected(ledger)
    (algo,) = ex.algo.values()
    algo["triggerPrice"] = "97.5"  # the operator trailed it
    notes = _poll(ex, ledger, now=3_000)
    assert _episode(ledger).status == STATUS_STOOD_DOWN
    assert "stop moved 95.0 -> 97.5" in notes[0]
    created, cancelled = len(ex.created), len(ex.cancelled)
    for t in (4_000, 5_000):
        assert _poll(ex, ledger, now=t) == []
    assert (len(ex.created), len(ex.cancelled)) == (created, cancelled)


def test_cancelled_stop_with_the_position_open_stands_down(ledger: Path) -> None:
    ex = _protected(ledger)
    for row in ex.algo.values():
        row["algoStatus"] = "CANCELED"
    notes = _poll(ex, ledger, now=3_000)
    assert _episode(ledger).status == STATUS_STOOD_DOWN
    assert "no longer resting" in notes[0]
    assert len(ex.created) == 2  # never re-placed


@pytest.mark.parametrize(
    ("field", "value", "needle"),
    [
        ("price", "112", "TP1 moved"),
        ("origQty", "0.5", "TP1 resized"),
        ("status", "CANCELED", "is CANCELED"),
    ],
)
def test_edited_tp1_stands_down(
    ledger: Path, field: str, value: str, needle: str
) -> None:
    ex = _protected(ledger)
    (order,) = ex.orders.values()
    order[field] = value
    notes = _poll(ex, ledger, now=3_000)
    assert _episode(ledger).status == STATUS_STOOD_DOWN
    assert needle in notes[0]


def test_filled_tp1_is_done_not_an_edit(ledger: Path) -> None:
    ex = _protected(ledger)
    (order,) = ex.orders.values()
    order["status"] = "FILLED"
    ex.fill("LONG", 1.0, 100.0)
    notes = _poll(ex, ledger, now=3_000)
    ep = _episode(ledger)
    assert ep.status == STATUS_PROTECTED and ep.legs["tp1"].done
    assert "TP1 filled" in notes[0]


def test_an_unchanged_book_is_a_quiet_poll(ledger: Path) -> None:
    ex = _protected(ledger)
    assert _poll(ex, ledger, now=3_000) == []
    assert _episode(ledger).status == STATUS_PROTECTED


# ----- close -----


def test_flat_side_cancels_our_own_tp1_and_closes(ledger: Path) -> None:
    ex = _protected(ledger)
    ex.fill("LONG", 0.0, 0.0)  # the stop fired
    for row in ex.algo.values():
        row["algoStatus"] = "FINISHED"
    _poll(ex, ledger, now=3_000)
    (tp1_id,) = ex.orders
    assert ex.cancelled == [{"symbol": SYM, "orderId": tp1_id}]
    assert _episode(ledger).status == STATUS_CLOSED


def test_flat_side_after_a_stand_down_cancels_nothing(ledger: Path) -> None:
    ex = _protected(ledger)
    (algo,) = ex.algo.values()
    algo["triggerPrice"] = "97.5"
    _poll(ex, ledger, now=3_000)
    ex.fill("LONG", 0.0, 0.0)
    _poll(ex, ledger, now=4_000)
    assert ex.cancelled == []
    assert _episode(ledger).status == STATUS_CLOSED


# ----- fills, the journal and the metric -----


def test_fills_are_journaled_once_with_roles_and_summarised(ledger: Path) -> None:
    ex = FakeExchange()
    _arm(ex, ledger)
    ex.trade(1, 1_500, side="BUY", price="100", qty="2", commission="0.04", maker=True)
    ex.trade(9, 900, side="BUY", qty="5")  # before arming: not this episode
    ex.trade(2, 1_600, side="BUY", positionSide="SHORT")  # the other side
    ex.fill("LONG", 2.0, 100.0)
    _poll(ex, ledger)
    tp1_id = _episode(ledger).legs["tp1"].order_id
    ex.trade(
        3,
        2_500,
        side="SELL",
        orderId=tp1_id,
        price="110",
        qty="1",
        commission="0.02",
        maker=True,
        realizedPnl="10",
    )
    ex.trade(
        4, 2_600, side="SELL", price="95", qty="1", commission="0.04", realizedPnl="-5"
    )
    ex.fill("LONG", 0.0, 0.0)
    _poll(ex, ledger, now=3_000)
    _poll(ex, ledger, now=3_500)  # nothing active: no duplicate rows

    ep = _episode(ledger)
    assert sorted(ep.fills) == ["1", "3", "4"]
    assert {k: f["role"] for k, f in ep.fills.items()} == {
        "1": "entry",
        "3": "tp1",
        "4": "exit",
    }
    s = ep.summary
    assert s["risk_usd"] == pytest.approx(2 * 5.0)
    assert s["fee_r"] == pytest.approx(0.10 / 10.0)
    assert (s["exit_fills"], s["maker_exit_fills"]) == (2, 1)
    assert s["maker_exit_qty_share"] == pytest.approx(0.5)
    assert s["metric_eligible"] is True
    m = metric_report(read_ledger(ledger)[0])
    assert m["episodes"] == 1 and m["maker_exit_share"] == pytest.approx(0.5)
    assert m["fee_r_mean"] == pytest.approx(0.01)


def test_a_non_usd_fee_leaves_fee_r_unknown(ledger: Path) -> None:
    ex = FakeExchange()
    _arm(ex, ledger)
    ex.trade(1, 1_500, qty="2", commissionAsset="BNB")
    ex.fill("LONG", 2.0, 100.0)
    _poll(ex, ledger)
    ex.fill("LONG", 0.0, 0.0)
    _poll(ex, ledger, now=3_000)
    assert _episode(ledger).summary["fee_r"] is None


def test_an_existing_position_never_counts_to_the_metric(ledger: Path) -> None:
    ex = FakeExchange()
    ex.fill("LONG", 2.0, 100.0)
    _arm(ex, ledger, existing=True)
    _poll(ex, ledger)
    ex.trade(1, 2_500, side="SELL", qty="2")
    ex.fill("LONG", 0.0, 0.0)
    _poll(ex, ledger, now=3_000)
    assert _episode(ledger).summary["metric_eligible"] is False
    assert metric_report(read_ledger(ledger)[0])["episodes"] == 0


# ----- -2015 and disarm -----


def test_key_or_ip_rejection_raises_loudly(ledger: Path) -> None:
    ex = FakeExchange()
    _arm(ex, ledger)
    ex.read_error = _api_error(-2015, "Invalid API-key, IP, or permissions")
    with pytest.raises(SignedPathRejected, match="api.ipify.org"):
        _poll(ex, ledger)


def test_a_2015_stop_refusal_is_retried_once_the_key_works(ledger: Path) -> None:
    ex = FakeExchange()
    _arm(ex, ledger)
    ex.fill("LONG", 2.0, 100.0)
    ex.create_errors["STOP_MARKET"] = _api_error(-2015)
    with pytest.raises(SignedPathRejected):
        _poll(ex, ledger)
    del ex.create_errors["STOP_MARKET"]
    _poll(ex, ledger, now=3_000)
    assert _episode(ledger).status == STATUS_PROTECTED


def test_disarm_before_placement_ends_it_after_placement_stands_down(
    ledger: Path,
) -> None:
    ex = FakeExchange()
    _arm(ex, ledger)
    assert "disarmed" in disarm(ledger, symbol=SYM, side="LONG", now_ms=1_500)
    assert read_ledger(ledger)[0][0].active is False
    _arm(ex, ledger, now_ms=2_000)
    ex.fill("LONG", 2.0, 100.0)
    _poll(ex, ledger, now=2_500)
    assert "stood down" in disarm(ledger, symbol=SYM, side="LONG", now_ms=3_000)
    assert read_ledger(ledger)[0][1].status == STATUS_STOOD_DOWN

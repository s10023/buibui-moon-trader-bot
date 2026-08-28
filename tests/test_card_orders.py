"""Tests for card/orders.py — scan, selection, guards, placement, refresh."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from card.orders import (
    CardCandidate,
    PlacementDecision,
    aggregate_risk,
    check_placement,
    parse_selection,
    pick_interactive,
    place_orders,
    read_jsonl,
    read_jsonl_counted,
    refresh_orders,
    scan_candidates,
)
from trade.binance_futures import APIError, BinanceFuturesAdapter
from trade.routing import ExchangeFilters

NOW_MS = 1_756_000_000_000

_FILT = ExchangeFilters(
    symbol="BTCUSDT",
    qty_step=0.001,
    min_qty=0.001,
    min_notional=100.0,
    price_tick=0.1,
)


def _trade_row(
    symbol: str = "BTCUSDT", gen_ms: int = NOW_MS - 60_000, **kw: Any
) -> dict[str, Any]:
    card = {
        "direction": kw.get("direction", "long"),
        "entry": kw.get("entry", 100.0),
        "sl": kw.get("sl", 98.0),
        "valid_until_utc": kw.get("valid_until_utc", "2099-01-01T00:00:00Z"),
    }
    return {
        "verdict": kw.get("verdict", "TRADE"),
        "symbol": symbol,
        "generated_at_ms": gen_ms,
        "state_digest": "abc123",
        "size_units": kw.get("size_units", 0.5),
        "risk_usd": kw.get("risk_usd", 1.0),
        "risk_frac": kw.get("risk_frac", 0.0025),
        "card": card,
    }


def _cand(symbol: str, direction: str, risk_usd: float = 2.5) -> CardCandidate:
    return CardCandidate(
        symbol=symbol,
        direction=direction,
        entry=100.0,
        sl=98.0,
        # 1.5 units @ 100.0 = 150.0 notional, clearing _FILT.min_notional
        # (100.0); 0.5 units used to sit below the floor and would newly
        # veto guard (d) once the notional check landed.
        qty=1.5,
        risk_usd=risk_usd,
        risk_frac=None,
        valid_until_ms=NOW_MS + 3_600_000,
        generated_at_ms=NOW_MS - 60_000,
        state_digest="abc123",
    )


def _decision(symbol: str = "BTCUSDT", direction: str = "long") -> PlacementDecision:
    return check_placement(
        _cand(symbol, direction),
        _FILT,
        managed=False,
        xs_live_marker=False,
        now_ms=NOW_MS,
    )


def _api_error(code: int, msg: str) -> APIError:
    """Build an APIError through whichever class trade.binance_futures binds.

    ``BinanceAPIException`` (python-binance 1.0.37, the installed version)
    takes ``(response, status_code, text)`` and parses ``text`` as JSON to
    set ``.code`` -- a one-argument message string raises ``TypeError``. The
    local shim (library absent entirely) takes a plain message instead. The
    ``.code`` assertion below means a future rebinding that stops setting it
    fails this helper loudly rather than silently skipping whichever path it
    is used to exercise. Shared by both the -5022 (recorded) and non-5022
    (propagated) placement tests so both paths construct the exception
    identically -- a divergent construction would make the two tests prove
    nothing about each other.
    """
    text = json.dumps({"code": code, "msg": msg})
    resp = SimpleNamespace(status_code=400, text=text)
    try:
        err: APIError = APIError(resp, 400, text)
    except TypeError:  # the local shim takes a plain message
        err = APIError(msg)
        err.code = code
    assert getattr(err, "code", None) == code
    return err


def _post_only_rejection() -> APIError:
    """Binance's -5022 GTX rejection -- the ONE terminal_reason=gtx_rejected code."""
    return _api_error(-5022, "Post Only order will be rejected")


def test_scan_keeps_only_unexpired_trade_cards() -> None:
    rows = [
        _trade_row(),
        _trade_row(verdict="NO_TRADE"),
        _trade_row(verdict="VETOED"),
        _trade_row(valid_until_utc="2020-01-01T00:00:00Z"),  # expired
        _trade_row(valid_until_utc=None),  # unparseable -> skipped
    ]
    out = scan_candidates(rows, [], NOW_MS)
    assert len(out) == 1
    assert out[0].symbol == "BTCUSDT"
    assert out[0].direction == "long"


def test_scan_anti_joins_already_placed_cards() -> None:
    row = _trade_row()
    placed = [
        {
            "kind": "placement",
            "symbol": "BTCUSDT",
            "card_generated_at_ms": row["generated_at_ms"],
        }
    ]
    assert scan_candidates([row], placed, NOW_MS) == []
    # a DIFFERENT card on the same symbol still presents
    other = _trade_row(gen_ms=row["generated_at_ms"] + 1)
    assert len(scan_candidates([other], placed, NOW_MS)) == 1


def test_scan_skips_rows_missing_price_or_size() -> None:
    assert scan_candidates([_trade_row(size_units=None)], [], NOW_MS) == []
    assert scan_candidates([_trade_row(entry=None)], [], NOW_MS) == []


def test_parse_selection_empty_means_none_selected() -> None:
    assert parse_selection("", 5) == []
    assert parse_selection("   ", 5) == []


def test_parse_selection_accepts_spaces_and_commas_dedups() -> None:
    assert parse_selection("1, 3 3", 5) == [0, 2]


def test_parse_selection_rejects_out_of_range_and_garbage() -> None:
    assert parse_selection("0", 5) is None
    assert parse_selection("6", 5) is None
    assert parse_selection("all", 5) is None


def test_aggregate_risk_stacks_same_symbol_and_side() -> None:
    # the 08-17e shape: six 0.25% choices that are three bets doubled
    cands = [
        _cand("BTCUSDT", "short"),
        _cand("BTCUSDT", "short"),
        _cand("ETHUSDT", "long"),
        _cand("ETHUSDT", "long"),
        _cand("SOLUSDT", "short"),
        _cand("SOLUSDT", "short"),
    ]
    agg = aggregate_risk(cands, equity=1000.0)
    assert agg.total_risk_usd == 15.0  # 6 x 2.5
    assert agg.total_risk_frac == 0.015
    assert ("BTCUSDT", "short", 5.0) in agg.by_bet
    assert len(agg.by_bet) == 3  # three bets, not six


def test_aggregate_risk_without_equity_suppresses_fraction() -> None:
    agg = aggregate_risk([_cand("BTCUSDT", "long")], equity=None)
    assert agg.total_risk_frac is None


def test_xs_guard_four_quadrants() -> None:
    c = _cand("BTCUSDT", "long")
    # managed AND live marker -> VETO
    d = check_placement(c, _FILT, managed=True, xs_live_marker=True, now_ms=NOW_MS)
    assert any("XS" in v for v in d.vetoes)
    # managed, no marker -> WARN only (heard before XS go-live)
    d = check_placement(c, _FILT, managed=True, xs_live_marker=False, now_ms=NOW_MS)
    assert d.vetoes == [] and any("XS managed set" in w for w in d.warnings)
    # unmanaged: marker state is irrelevant either way
    for marker in (True, False):
        d = check_placement(
            c, _FILT, managed=False, xs_live_marker=marker, now_ms=NOW_MS
        )
        assert d.vetoes == []
        assert not any("XS" in w for w in d.warnings)


def test_expiry_rechecked_at_placement_instant() -> None:
    c = _cand("BTCUSDT", "long")
    d = check_placement(
        c,
        _FILT,
        managed=False,
        xs_live_marker=False,
        now_ms=c.valid_until_ms + 1,  # expired while the operator thought
    )
    assert any("expired" in v for v in d.vetoes)


def test_rounding_restates_risk_and_warns_when_moved() -> None:
    c = CardCandidate(
        symbol="BTCUSDT",
        direction="long",
        entry=1000.05,
        sl=980.0,
        qty=0.5015,
        risk_usd=1.0,
        risk_frac=None,
        valid_until_ms=NOW_MS + 3_600_000,
        generated_at_ms=NOW_MS,
        state_digest="d",
    )
    d = check_placement(c, _FILT, managed=False, xs_live_marker=False, now_ms=NOW_MS)
    assert d.qty == 0.501  # floored to LOT_SIZE step
    assert d.price == 1000.0  # long=BUY floors to tick, never crosses up
    assert d.risk_usd == round(0.501 * (1000.0 - 980.0), 8)  # restated from ROUNDED
    assert d.risk_frac is None  # no risk_frac on the card -> nothing to restate
    assert d.warnings  # says the numbers moved


def test_rounding_restates_risk_frac_proportionally() -> None:
    c = CardCandidate(
        symbol="BTCUSDT",
        direction="long",
        entry=1000.05,
        sl=980.0,
        qty=0.5015,
        risk_usd=1.0,
        risk_frac=0.01,
        valid_until_ms=NOW_MS + 3_600_000,
        generated_at_ms=NOW_MS,
        state_digest="d",
    )
    d = check_placement(c, _FILT, managed=False, xs_live_marker=False, now_ms=NOW_MS)
    assert d.risk_usd == round(0.501 * (1000.0 - 980.0), 8)
    # proportional restatement: cand.risk_frac * (new_risk_usd / cand.risk_usd)
    assert c.risk_frac is not None
    assert c.risk_usd is not None
    assert d.risk_usd is not None
    assert d.risk_frac == c.risk_frac * (d.risk_usd / c.risk_usd)


def test_sub_lot_quantity_vetoes() -> None:
    c = _cand("BTCUSDT", "long")
    tiny = CardCandidate(**{**c.__dict__, "qty": 0.0004})
    d = check_placement(tiny, _FILT, managed=False, xs_live_marker=False, now_ms=NOW_MS)
    assert any("sub-lot" in v for v in d.vetoes)


def test_missing_filters_warns_and_keeps_raw_numbers() -> None:
    c = _cand("BTCUSDT", "long")
    d = check_placement(c, None, managed=False, xs_live_marker=False, now_ms=NOW_MS)
    assert d.vetoes == []
    assert d.qty == c.qty and d.price == c.entry
    assert any("filters unavailable" in w for w in d.warnings)


def test_min_notional_veto_when_below_floor() -> None:
    c = _cand("BTCUSDT", "long")
    # 0.5 units @ 100.0 = 50.0 notional, clears min_qty but sits below
    # _FILT.min_notional (100.0) -- the old _cand() default before the bump.
    small = CardCandidate(**{**c.__dict__, "qty": 0.5})
    d = check_placement(
        small, _FILT, managed=False, xs_live_marker=False, now_ms=NOW_MS
    )
    assert any("notional" in v for v in d.vetoes)


def test_sub_lot_vetoes_when_positive_but_below_min_qty() -> None:
    # min_qty (0.01) wider than qty_step (0.001) so a rounded qty can land
    # strictly between zero and the floor -- exercising the `qty <
    # filt.min_qty` half of the OR on its own, distinct from `qty <= 0.0`.
    filt = ExchangeFilters(
        symbol="BTCUSDT",
        qty_step=0.001,
        min_qty=0.01,
        min_notional=0.0,
        price_tick=0.1,
    )
    c = _cand("BTCUSDT", "long")
    below_min = CardCandidate(**{**c.__dict__, "qty": 0.005})
    d = check_placement(
        below_min, filt, managed=False, xs_live_marker=False, now_ms=NOW_MS
    )
    assert any("sub-lot" in v for v in d.vetoes)


def test_place_orders_writes_placement_row_with_instrumentation(
    tmp_path: Path,
) -> None:
    client = MagicMock()
    client.futures_create_order.return_value = {"orderId": 42}
    adapter = BinanceFuturesAdapter(client, mode="live")
    ledger = tmp_path / "card-orders.jsonl"
    rows = place_orders(
        adapter,
        [_decision()],
        ledger_path=ledger,
        dual_side=True,
        marks={"BTCUSDT": 100.2},
        books={"BTCUSDT": (100.1, 100.3)},
        positions={"BTCUSDT": 0.25},
        equity=550.0,
        now_ms=NOW_MS,
    )
    assert len(rows) == 1
    row = read_jsonl(ledger)[0]
    assert row["kind"] == "placement" and row["order_id"] == 42
    assert row["state_digest"] == "abc123"
    assert row["card_generated_at_ms"] == NOW_MS - 60_000
    assert row["mark_at_placement"] == 100.2
    assert row["bid_at_placement"] == 100.1 and row["ask_at_placement"] == 100.3
    assert row["position_at_placement"] == 0.25
    assert row["equity_at_placement"] == 550.0
    assert row["position_mode"] == "hedge"
    # R11: risk_frac rides alongside risk_usd, restated from the ROUNDED
    # quantity same as check_placement -- otherwise the ledger holds a risk
    # number whose meaning depends on a capital figure recorded nowhere here.
    assert row["risk_frac"] == _decision().risk_frac
    # hedge-mode wire shape came from Task 1
    kwargs = client.futures_create_order.call_args.kwargs
    assert kwargs["positionSide"] == "LONG" and "reduceOnly" not in kwargs


def test_gtx_rejection_is_recorded_not_raised(tmp_path: Path) -> None:
    client = MagicMock()
    client.futures_create_order.side_effect = _post_only_rejection()
    adapter = BinanceFuturesAdapter(client, mode="live")
    ledger = tmp_path / "card-orders.jsonl"
    rows = place_orders(
        adapter,
        [_decision()],
        ledger_path=ledger,
        dual_side=False,
        marks={},
        books={},
        positions={},
        equity=None,
        now_ms=NOW_MS,
    )
    row = rows[0]
    assert row["order_id"] is None
    assert row["terminal_reason"] == "gtx_rejected"
    # and the rejected card no longer re-presents: same anti-join key
    assert row["kind"] == "placement" and row["symbol"] == "BTCUSDT"
    assert row["position_mode"] == "one_way"  # dual_side=False -> one-way wire shape


def test_non_post_only_api_error_propagates_and_ledger_untouched(
    tmp_path: Path,
) -> None:
    """A -5022 is recorded as gtx_rejected; every OTHER code must NOT be.

    place_orders reads a -5022 as "price already through the level" and
    writes that reassurance into the ledger. If the code comparison were
    ever inverted, or the re-raise dropped, a real exchange failure -- here
    -2019 "Margin is insufficient" -- would be recorded wearing the same
    reassuring terminal_reason instead of surfacing to the operator. This
    pins the OTHER half of that branch: built through the same _api_error
    helper as the -5022 case, so both paths construct the exception
    identically and neither test can pass by accident.
    """
    client = MagicMock()
    client.futures_create_order.side_effect = _api_error(
        -2019, "Margin is insufficient"
    )
    adapter = BinanceFuturesAdapter(client, mode="live")
    ledger = tmp_path / "card-orders.jsonl"
    with pytest.raises(APIError) as exc_info:
        place_orders(
            adapter,
            [_decision()],
            ledger_path=ledger,
            dual_side=False,
            marks={},
            books={},
            positions={},
            equity=None,
            now_ms=NOW_MS,
        )
    assert exc_info.value.code == -2019
    assert not ledger.exists()  # a propagated error must not touch the ledger


def test_vetoed_decisions_are_skipped_and_dry_run_writes_nothing(
    tmp_path: Path,
) -> None:
    vetoed = check_placement(
        _cand("BTCUSDT", "long"),
        _FILT,
        managed=True,
        xs_live_marker=True,
        now_ms=NOW_MS,
    )
    client = MagicMock()
    adapter = BinanceFuturesAdapter(client, mode="live")
    ledger = tmp_path / "card-orders.jsonl"
    assert (
        place_orders(
            adapter,
            [vetoed],
            ledger_path=ledger,
            dual_side=False,
            marks={},
            books={},
            positions={},
            equity=None,
            now_ms=NOW_MS,
        )
        == []
    )
    client.futures_create_order.assert_not_called()

    dry = BinanceFuturesAdapter(MagicMock(), mode="dry_run")
    assert (
        place_orders(
            dry,
            [_decision()],
            ledger_path=ledger,
            dual_side=False,
            marks={},
            books={},
            positions={},
            equity=None,
            now_ms=NOW_MS,
        )
        == []
    )
    assert not ledger.exists()  # a dry run must not pollute the ledger


def test_place_orders_short_one_way_wire_shape(tmp_path: Path) -> None:
    """R12 gap 1: only the hedge/LONG path was asserted before this test."""
    client = MagicMock()
    client.futures_create_order.return_value = {"orderId": 43}
    adapter = BinanceFuturesAdapter(client, mode="live")
    ledger = tmp_path / "card-orders.jsonl"
    rows = place_orders(
        adapter,
        [_decision(direction="short")],
        ledger_path=ledger,
        dual_side=False,
        marks={},
        books={},
        positions={},
        equity=None,
        now_ms=NOW_MS,
    )
    assert len(rows) == 1
    kwargs = client.futures_create_order.call_args.kwargs
    assert kwargs["side"] == "SELL"
    # one-way wire shape: reduceOnly present, positionSide absent
    assert "reduceOnly" in kwargs
    assert "positionSide" not in kwargs


def test_place_orders_short_hedge_wire_shape(tmp_path: Path) -> None:
    """R12 gap 1, second case: SHORT under dual_side=True."""
    client = MagicMock()
    client.futures_create_order.return_value = {"orderId": 44}
    adapter = BinanceFuturesAdapter(client, mode="live")
    ledger = tmp_path / "card-orders.jsonl"
    rows = place_orders(
        adapter,
        [_decision(direction="short")],
        ledger_path=ledger,
        dual_side=True,
        marks={},
        books={},
        positions={},
        equity=None,
        now_ms=NOW_MS,
    )
    assert len(rows) == 1
    kwargs = client.futures_create_order.call_args.kwargs
    assert kwargs["side"] == "SELL"
    assert kwargs["positionSide"] == "SHORT"
    # symmetry with the hedge/LONG case: hedge mode REJECTS reduceOnly
    assert "reduceOnly" not in kwargs


def _ledger_with_placement(tmp_path: Path, order_id: int = 42) -> Path:
    ledger = tmp_path / "card-orders.jsonl"
    client = MagicMock()
    client.futures_create_order.return_value = {"orderId": order_id}
    adapter = BinanceFuturesAdapter(client, mode="live")
    place_orders(
        adapter,
        [_decision()],
        ledger_path=ledger,
        dual_side=False,
        marks={},
        books={},
        positions={},
        equity=None,
        now_ms=NOW_MS,
    )
    return ledger


def test_refresh_writes_terminal_row_for_filled_order(tmp_path: Path) -> None:
    ledger = _ledger_with_placement(tmp_path)
    client = MagicMock()
    client.futures_get_order.return_value = {
        "status": "FILLED",
        "avgPrice": "99.9",
        "executedQty": "0.5",
        "updateTime": NOW_MS + 60_000,
    }
    rows = refresh_orders(
        client, ledger, marks={"BTCUSDT": 100.5}, now_ms=NOW_MS + 90_000
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "terminal" and r["order_id"] == 42
    assert r["reason"] == "filled" and r["avg_price"] == 99.9
    assert r["terminal_at_ms"] == NOW_MS + 60_000
    assert r["mark_at_terminal"] == 100.5
    client.futures_get_order.assert_called_once_with(symbol="BTCUSDT", orderId=42)


def test_refresh_skips_working_and_already_terminal_orders(tmp_path: Path) -> None:
    ledger = _ledger_with_placement(tmp_path)
    client = MagicMock()
    client.futures_get_order.return_value = {"status": "NEW"}
    assert refresh_orders(client, ledger, marks={}, now_ms=NOW_MS) == []
    # now close it, then a second refresh must not re-poll it
    client.futures_get_order.return_value = {
        "status": "CANCELED",
        "avgPrice": "0",
        "executedQty": "0",
        "updateTime": NOW_MS + 1,
    }
    assert len(refresh_orders(client, ledger, marks={}, now_ms=NOW_MS)) == 1
    client.futures_get_order.reset_mock()
    assert refresh_orders(client, ledger, marks={}, now_ms=NOW_MS) == []
    client.futures_get_order.assert_not_called()


def test_refresh_maps_expired_in_match_to_expired(tmp_path: Path) -> None:
    """R13: self-trade prevention's status must still terminalise the ledger.

    This account rests a card order and an XS order on the same symbol --
    exactly the collision check_placement's XS guard exists for, arriving by
    a different route (self-trade prevention rather than cancel_open_orders).
    """
    ledger = _ledger_with_placement(tmp_path)
    client = MagicMock()
    client.futures_get_order.return_value = {
        "status": "EXPIRED_IN_MATCH",
        "avgPrice": "0",
        "executedQty": "0",
        "updateTime": NOW_MS + 1,
    }
    rows = refresh_orders(client, ledger, marks={}, now_ms=NOW_MS)
    assert len(rows) == 1
    assert rows[0]["reason"] == "expired"
    assert rows[0]["status"] == "EXPIRED_IN_MATCH"


def test_refresh_maps_unrecognised_status_to_other(tmp_path: Path) -> None:
    """R13: inverted default -- an unmapped status is still written, under
    reason "other", with the raw status preserved on the row rather than
    silently retried forever."""
    ledger = _ledger_with_placement(tmp_path)
    client = MagicMock()
    client.futures_get_order.return_value = {
        "status": "SOME_NEW_STATUS",
        "avgPrice": "0",
        "executedQty": "0",
        "updateTime": NOW_MS + 1,
    }
    rows = refresh_orders(client, ledger, marks={}, now_ms=NOW_MS)
    assert len(rows) == 1
    assert rows[0]["reason"] == "other"
    assert rows[0]["status"] == "SOME_NEW_STATUS"


def test_refresh_pending_cancel_still_writes_nothing(tmp_path: Path) -> None:
    ledger = _ledger_with_placement(tmp_path)
    client = MagicMock()
    client.futures_get_order.return_value = {"status": "PENDING_CANCEL"}
    assert refresh_orders(client, ledger, marks={}, now_ms=NOW_MS) == []


def test_refresh_terminal_ids_updates_within_one_call(tmp_path: Path) -> None:
    """R14 minor 1: two placement rows sharing one order_id must not each
    produce a terminal row -- terminal_ids has to update as the loop writes,
    not stay frozen from before the loop started."""
    ledger = tmp_path / "card-orders.jsonl"
    placement = {
        "kind": "placement",
        "order_id": 42,
        "symbol": "BTCUSDT",
        "card_generated_at_ms": NOW_MS - 60_000,
        "state_digest": "abc123",
    }
    with ledger.open("w", encoding="utf-8") as f:
        f.write(json.dumps(placement) + "\n")
        f.write(json.dumps(placement) + "\n")
    client = MagicMock()
    client.futures_get_order.return_value = {
        "status": "FILLED",
        "avgPrice": "99.9",
        "executedQty": "0.5",
        "updateTime": NOW_MS + 1,
    }
    rows = refresh_orders(client, ledger, marks={}, now_ms=NOW_MS)
    assert len(rows) == 1  # one terminal row per order_id, not one per placement row


def test_parse_selection_non_ascii_digit_returns_none_not_raises() -> None:
    """R14b: `"²".isdigit()` is True but `int("²")` raises ValueError -- the
    unguarded `tok.isdigit()` check let a superscript digit reach `int()` and
    raise instead of returning None as the contract promises. Reachable only
    via `pick_interactive`'s re-prompt loop, which feeds raw operator input
    straight into this function."""
    assert parse_selection("²", 5) is None


def test_pick_interactive_empty_input_selects_none() -> None:
    lines = iter([""])
    out: list[str] = []
    picked = pick_interactive(
        [_cand("BTCUSDT", "long")],
        equity=1000.0,
        input_fn=lambda _prompt: next(lines),
        print_fn=out.append,
    )
    assert picked == []
    assert any("ONE DRAW" in s for s in out)  # the hazard line is in the header


def test_pick_interactive_confirm_gate_and_aggregate_echo() -> None:
    lines = iter(["1 2", "y"])
    out: list[str] = []
    cands = [_cand("BTCUSDT", "short"), _cand("BTCUSDT", "short")]
    picked = pick_interactive(
        cands,
        equity=1000.0,
        input_fn=lambda _prompt: next(lines),
        print_fn=out.append,
    )
    assert len(picked) == 2
    assert any("BTCUSDT short" in s and "5.0" in s for s in out)  # stacked bet


def test_pick_interactive_n_aborts() -> None:
    lines = iter(["1", "n"])
    picked = pick_interactive(
        [_cand("BTCUSDT", "long")],
        equity=None,
        input_fn=lambda _prompt: next(lines),
        print_fn=lambda _s: None,
    )
    assert picked == []


def test_pick_interactive_reprompts_on_garbage() -> None:
    lines = iter(["banana", "1", "y"])
    picked = pick_interactive(
        [_cand("BTCUSDT", "long")],
        equity=None,
        input_fn=lambda _prompt: next(lines),
        print_fn=lambda _s: None,
    )
    assert len(picked) == 1


def test_pick_interactive_reprompts_on_non_ascii_digit() -> None:
    """R14b, end to end: a non-ASCII digit must re-prompt like any other
    piece of garbage input, not crash the picklist with a ValueError."""
    lines = iter(["²", "1", "y"])
    picked = pick_interactive(
        [_cand("BTCUSDT", "long")],
        equity=None,
        input_fn=lambda _prompt: next(lines),
        print_fn=lambda _s: None,
    )
    assert len(picked) == 1


def test_universe_symbols_reads_the_nested_universe_table() -> None:
    """R1: the real `config/universe.toml` nests its list under `[universe]`
    -- the top level carries no `symbols` key at all. A version that reads
    the top level returns an empty set with no error, which silently
    disables `check_placement`'s XS managed-set guard for every symbol."""
    from cli.card_orders import _universe_symbols

    symbols = _universe_symbols()
    assert symbols  # non-empty: a version reading the wrong table passes silently
    assert "BTCUSDT" in symbols


def test_pick_interactive_countdown_uses_injected_clock() -> None:
    """R16: `now_ms` must be an injectable parameter, mirroring every other
    function in this module -- the "expires" countdown is the one field the
    operator actually reads before confirming a real-money placement, and it
    is otherwise the only untestable-for-time surface in the file. The
    candidate's default valid_until_ms is exactly NOW_MS + 3_600_000, one
    hour ahead, so passing now_ms=NOW_MS produces an unambiguous "60m"."""
    out: list[str] = []
    pick_interactive(
        [_cand("BTCUSDT", "long")],
        equity=None,
        input_fn=lambda _prompt: "",
        print_fn=out.append,
        now_ms=NOW_MS,
    )
    assert any("BTCUSDT" in s and "60m" in s for s in out)


def test_pick_interactive_header_names_every_column_in_order() -> None:
    """Minor 1 (R17): pins the spec-mandated column list on a money surface
    -- a future edit dropping, say, the SL column would otherwise stay
    invisible to every gate (this repo's own "green gates are blind to
    rendering" lesson)."""
    out: list[str] = []
    pick_interactive(
        [_cand("BTCUSDT", "long")],
        equity=None,
        input_fn=lambda _prompt: "",
        print_fn=out.append,
        now_ms=NOW_MS,
    )
    header = out[1]  # out[0] is the ONE DRAW hazard line
    fields = ["symbol", "dir", "entry", "sl", "qty", "risk_usd", "expires"]
    positions = [header.index(f) for f in fields]  # raises if any is missing
    assert positions == sorted(positions)  # and in the spec's order


def test_pick_interactive_shows_question_mark_when_risk_usd_missing() -> None:
    """Minor 3 (R17): the risk_usd=None -> "?" fallback had no covering
    candidate anywhere in this file."""
    cand = CardCandidate(
        symbol="BTCUSDT",
        direction="long",
        entry=100.0,
        sl=98.0,
        qty=1.5,
        risk_usd=None,
        risk_frac=None,
        valid_until_ms=NOW_MS + 3_600_000,
        generated_at_ms=NOW_MS - 60_000,
        state_digest="abc123",
    )
    out: list[str] = []
    pick_interactive(
        [cand],
        equity=None,
        input_fn=lambda _prompt: "",
        print_fn=out.append,
        now_ms=NOW_MS,
    )
    row = out[2]
    assert "?" in row


def test_pick_interactive_zero_risk_frac_still_shows_percentage() -> None:
    """Minor 2 (R17): `agg.total_risk_frac` of exactly 0.0 is a legitimate
    value (equity known, risk zero) and must not be treated as falsy like
    `None` is -- the old `if agg.total_risk_frac` dropped the "% of equity"
    suffix for both cases alike."""
    cand = CardCandidate(
        symbol="BTCUSDT",
        direction="long",
        entry=100.0,
        sl=98.0,
        qty=1.5,
        risk_usd=0.0,
        risk_frac=None,
        valid_until_ms=NOW_MS + 3_600_000,
        generated_at_ms=NOW_MS - 60_000,
        state_digest="abc123",
    )
    out: list[str] = []
    lines = iter(["1", "y"])
    pick_interactive(
        [cand],
        equity=1000.0,  # known equity + zero risk -> total_risk_frac == 0.0, not None
        input_fn=lambda _prompt: next(lines),
        print_fn=out.append,
        now_ms=NOW_MS,
    )
    assert any("AGGREGATE" in s and "% of equity" in s for s in out)


# --- R18 fix wave -----------------------------------------------------------


def _decimal_places(x: float) -> int:
    """Decimal places in a float's own `str()` -- what reaches the wire."""
    exponent = Decimal(str(x)).normalize().as_tuple().exponent
    return max(0, -int(exponent))


def _qty_filter(step: float) -> ExchangeFilters:
    return ExchangeFilters(
        symbol="BTCUSDT",
        qty_step=step,
        min_qty=step,
        min_notional=5.0,
        price_tick=0.1,
    )


@pytest.mark.parametrize(("raw_qty", "expected"), [(0.009, "0.009"), (0.8175, "0.817")])
def test_rounded_qty_carries_no_float_noise_to_the_wire(
    raw_qty: float, expected: str
) -> None:
    """R18 critical: the quantity must be quantised to the STEP's precision.

    `round_down_to_step` returns `floor(quotient) * step`, which is a float
    product and not a decimal: `round_down_to_step(0.009, 0.001)` is
    0.009000000000000001 and `0.8175` is 0.8170000000000001. python-binance
    urlencodes params with a bare `str()`, so 18 decimals reach the wire on a
    3-decimal filter and Binance rejects the order -1111. Both inputs
    reproduce that today, and 2 of 46 real TRADE rows in `ai-cards.jsonl` do.

    Asserted on `str()` rather than on `==` alone because the string form IS
    the wire form -- a numeric assertion invites a later `pytest.approx`,
    which would pass on a float that still serialises wrong. Every other test
    in this file is blind to this class: `MagicMock` accepts any float.
    """
    c = CardCandidate(
        symbol="BTCUSDT",
        direction="long",
        entry=10_000.0,
        sl=9_800.0,
        qty=raw_qty,
        risk_usd=1.0,
        risk_frac=None,
        valid_until_ms=NOW_MS + 3_600_000,
        generated_at_ms=NOW_MS,
        state_digest="d",
    )
    d = check_placement(
        c, _qty_filter(0.001), managed=False, xs_live_marker=False, now_ms=NOW_MS
    )
    assert d.vetoes == []
    assert str(d.qty) == expected
    assert _decimal_places(d.qty) <= _decimal_places(0.001)


def test_unknown_qty_step_passes_through_unrounded() -> None:
    """A zero step means "filter unavailable" and must not be quantised.

    `_tick_decimals(0.0)` is 0, so an unguarded `round(qty, decimals)` would
    turn a fractional quantity into a whole one -- rounding a 1.5 lot UP to 2
    and buying more than the card sized.
    """
    c = _cand("BTCUSDT", "long")  # qty 1.5
    filt = ExchangeFilters(
        symbol="BTCUSDT",
        qty_step=0.0,
        min_qty=0.0,
        min_notional=0.0,
        price_tick=0.1,
    )
    d = check_placement(c, filt, managed=False, xs_live_marker=False, now_ms=NOW_MS)
    assert d.qty == 1.5


def test_read_jsonl_counted_reports_a_torn_append(tmp_path: Path) -> None:
    """R18 #2: a dropped line must be COUNTABLE, not merely tolerated.

    A crash mid-write leaves no trailing newline, so the next append
    concatenates onto it and both rows become one unparseable line -- losing a
    whole placement row, which is what stops a card being placed twice.
    """
    ledger = tmp_path / "card-orders.jsonl"
    good = {"kind": "placement", "order_id": 7, "symbol": "BTCUSDT"}
    torn = json.dumps(good) + json.dumps(good)  # the concatenation, no newline
    ledger.write_text(json.dumps(good) + "\n" + torn + "\n", encoding="utf-8")
    rows, dropped = read_jsonl_counted(ledger)
    assert len(rows) == 1 and dropped == 1
    assert read_jsonl(ledger) == rows  # the rows-only signature still works
    assert read_jsonl_counted(tmp_path / "absent.jsonl") == ([], 0)


def _wire_run_place(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    ledger_text: str = "",
) -> tuple[argparse.Namespace, list[dict[str, Any]]]:
    """Run-place harness: real wiring, no network, `check_placement` spied on.

    Only the leaf calls are replaced -- the client factory, the interactive
    picklist and the submit -- so `run_place`'s own argument construction
    (which is what R18 #4 says is untested) runs for real.
    """
    from cli import card_orders as cli_orders

    cards = tmp_path / "ai-cards.jsonl"
    cards.write_text(json.dumps(_trade_row()) + "\n", encoding="utf-8")
    ledger = tmp_path / "card-orders.jsonl"
    if ledger_text:
        ledger.write_text(ledger_text, encoding="utf-8")
    marker = tmp_path / "execution_state_live.json"
    marker.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli_orders, "XS_LIVE_MARKER", marker)
    monkeypatch.setattr(
        "utils.binance_client.create_client", lambda *a, **k: MagicMock()
    )
    monkeypatch.setattr(
        cli_orders, "pick_interactive", lambda cands, equity, **kw: cands
    )
    seen: list[dict[str, Any]] = []

    def _spy(cand: CardCandidate, filt: Any, **kwargs: Any) -> PlacementDecision:
        seen.append(kwargs)
        return check_placement(cand, filt, **kwargs)

    monkeypatch.setattr(cli_orders, "check_placement", _spy)
    monkeypatch.setattr(cli_orders, "place_orders", lambda *a, **k: [])
    args = argparse.Namespace(cards_path=str(cards), ledger=str(ledger), dry_run=True)
    return args, seen


def test_run_place_wires_the_xs_guard_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R18 #4: the XS guard's WIRING, not just its truth table.

    `test_xs_guard_four_quadrants` passes `managed=` / `xs_live_marker=` as
    literals and `test_universe_symbols_reads_the_nested_universe_table` tests
    the parser alone, so a slip in `run_place` -- `managed=False`, or the
    marker renamed under `docs/plans/` -- leaves the guard PERMANENTLY INERT
    with every test still green. This is the guard the spec calls the
    feature's original blocker: `cancel_open_orders` is symbol-WIDE, and
    `config/universe.toml` leads with exactly the carded majors.

    BTCUSDT is read from the real committed universe, so a symbol leaving that
    file surfaces here rather than silently.
    """
    from cli.card_orders import run_place

    args, seen = _wire_run_place(tmp_path, monkeypatch)
    run_place(args)
    assert len(seen) == 1
    assert seen[0]["managed"] is True
    assert seen[0]["xs_live_marker"] is True


def test_run_place_warns_when_a_ledger_line_was_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """R18 #2: a torn ledger re-presents an already-placed card -- say so.

    The torn line here IS this card's placement row, concatenated with the
    next one, so the anti-join misses and the card presents as new. The
    operator must not learn that from a duplicate live order.
    """
    from cli.card_orders import run_place

    placed = {
        "kind": "placement",
        "order_id": 7,
        "symbol": "BTCUSDT",
        "card_generated_at_ms": NOW_MS - 60_000,
    }
    torn = json.dumps(placed) + json.dumps(placed) + "\n"
    args, seen = _wire_run_place(tmp_path, monkeypatch, ledger_text=torn)
    run_place(args)
    out = capsys.readouterr().out
    assert "unparseable" in out and "ALREADY-PLACED" in out
    assert len(seen) == 1  # and the card really did re-present


def _ledger_with_two_placements(tmp_path: Path) -> Path:
    ledger = tmp_path / "card-orders.jsonl"
    with ledger.open("w", encoding="utf-8") as f:
        for oid in (42, 43):
            f.write(
                json.dumps(
                    {
                        "kind": "placement",
                        "order_id": oid,
                        "symbol": "BTCUSDT",
                        "card_generated_at_ms": NOW_MS - 60_000,
                        "state_digest": "abc123",
                    }
                )
                + "\n"
            )
    return ledger


def _filled_order() -> dict[str, Any]:
    return {
        "status": "FILLED",
        "avgPrice": "99.9",
        "executedQty": "0.5",
        "updateTime": NOW_MS + 5,
    }


def test_refresh_records_not_found_and_keeps_going(tmp_path: Path) -> None:
    """R18 #3: -2013 is permanent, so it must not block every LATER order.

    Binance stops answering Query Order for an order that was cancelled or
    expired without filling and is over 7 days old -- the exact shape of an
    unfilled GTX entry from a hand-run command. Iterating in file order, one
    such row aborted the batch and permanently blocked every order behind it
    from ever getting a terminal row.
    """
    ledger = _ledger_with_two_placements(tmp_path)
    client = MagicMock()
    client.futures_get_order.side_effect = [
        _api_error(-2013, "Order does not exist."),
        _filled_order(),
    ]
    rows = refresh_orders(client, ledger, marks={"BTCUSDT": 100.5}, now_ms=NOW_MS)
    assert [r["order_id"] for r in rows] == [42, 43]
    assert rows[0]["reason"] == "not_found" and rows[0]["status"] == "NOT_FOUND"
    assert rows[0]["terminal_at_ms"] == NOW_MS  # no updateTime to read
    assert rows[0]["avg_price"] == 0.0 and rows[0]["executed_qty"] == 0.0
    assert rows[0]["mark_at_terminal"] == 100.5
    assert rows[1]["reason"] == "filled"


def test_refresh_poll_failure_warns_and_leaves_the_order_open(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """R18 #3, other half: a transient failure says NOTHING about the order.

    So it warns, moves to the next order (the batch is not abandoned), leaves
    no terminal row behind, and the next `--refresh` picks it up -- which is
    where the docstring's self-healing claim actually lives.
    """
    ledger = _ledger_with_two_placements(tmp_path)
    client = MagicMock()
    client.futures_get_order.side_effect = [
        ConnectionError("read timed out"),
        _filled_order(),
    ]
    rows = refresh_orders(client, ledger, marks={}, now_ms=NOW_MS)
    assert [r["order_id"] for r in rows] == [43]
    assert "poll failed" in capsys.readouterr().out
    client.futures_get_order.side_effect = None
    client.futures_get_order.return_value = _filled_order()
    again = refresh_orders(client, ledger, marks={}, now_ms=NOW_MS)
    assert [r["order_id"] for r in again] == [42]  # retried, not lost


def test_submit_timeout_records_submit_unknown_then_reraises(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """R18 #7: a timeout AFTER the exchange accepts leaves an orphan order.

    `requests` timeouts raise BinanceRequestException / requests.Timeout, not
    APIError, so they used to propagate past the APIError handler with no row
    written -- and `card-orders --refresh` only polls ids it already holds, so
    that order would be invisible to the ledger forever. The row makes it
    visible; the re-raise keeps the failure loud.
    """
    client = MagicMock()
    client.futures_create_order.side_effect = ConnectionError("read timed out")
    adapter = BinanceFuturesAdapter(client, mode="live")
    ledger = tmp_path / "card-orders.jsonl"
    with pytest.raises(ConnectionError):
        place_orders(
            adapter,
            [_decision()],
            ledger_path=ledger,
            dual_side=True,
            marks={},
            books={},
            positions={},
            equity=None,
            now_ms=NOW_MS,
        )
    rows = read_jsonl(ledger)
    assert len(rows) == 1
    assert rows[0]["kind"] == "placement" and rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["order_id"] is None
    assert rows[0]["terminal_reason"] == "submit_unknown"
    assert "MANUALLY" in capsys.readouterr().out


def test_aggregate_names_the_rows_whose_risk_is_unknown() -> None:
    """R18 #6: `sum(c.risk_usd or 0.0)` treats an unknown risk as $0.

    The per-row view prints "?", but the AGGREGATE line is the figure the y/N
    is answering, and a silently understated total reads as complete.
    """
    known = _cand("BTCUSDT", "long", risk_usd=2.5)
    unknown = CardCandidate(
        symbol="ETHUSDT",
        direction="long",
        entry=100.0,
        sl=98.0,
        qty=1.5,
        risk_usd=None,
        risk_frac=None,
        valid_until_ms=NOW_MS + 3_600_000,
        generated_at_ms=NOW_MS - 60_000,
        state_digest="abc123",
    )
    assert aggregate_risk([known, unknown], 1000.0).unknown_risk_count == 1
    assert aggregate_risk([known], 1000.0).unknown_risk_count == 0
    out: list[str] = []
    lines = iter(["1 2", "n"])
    pick_interactive(
        [known, unknown],
        equity=1000.0,
        input_fn=lambda _prompt: next(lines),
        print_fn=out.append,
        now_ms=NOW_MS,
    )
    agg = next(s for s in out if s.startswith("AGGREGATE"))
    assert "1 of 2" in agg and "UNKNOWN" in agg
    # and no noise on the all-known path
    out.clear()
    lines = iter(["1", "n"])
    pick_interactive(
        [known],
        equity=1000.0,
        input_fn=lambda _prompt: next(lines),
        print_fn=out.append,
        now_ms=NOW_MS,
    )
    assert "UNKNOWN" not in next(s for s in out if s.startswith("AGGREGATE"))

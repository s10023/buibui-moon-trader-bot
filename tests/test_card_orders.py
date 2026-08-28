"""Tests for card/orders.py — scan, selection, guards, placement, refresh."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from card.orders import (
    CardCandidate,
    PlacementDecision,
    aggregate_risk,
    check_placement,
    parse_selection,
    place_orders,
    read_jsonl,
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


def _post_only_rejection() -> APIError:
    """Binance's -5022 rejection, built through whichever class Task 1 bound.

    ``BinanceAPIException`` (python-binance 1.0.37, the installed version)
    takes ``(response, status_code, text)`` and parses ``text`` as JSON to
    set ``.code`` -- a one-argument message string raises ``TypeError``. The
    local shim (library absent entirely) takes a plain message instead. The
    ``.code`` assertion below means a future rebinding that stops setting it
    fails this helper loudly rather than silently skipping the rejection
    path it exists to exercise.
    """
    text = json.dumps({"code": -5022, "msg": "Post Only order will be rejected"})
    resp = SimpleNamespace(status_code=400, text=text)
    try:
        err: APIError = APIError(resp, 400, text)
    except TypeError:  # the local shim takes a plain message
        err = APIError("Post Only order will be rejected")
        err.code = -5022
    assert getattr(err, "code", None) == -5022
    return err


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

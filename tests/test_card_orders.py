"""Tests for card/orders.py — scan, selection, guards, placement, refresh."""

from __future__ import annotations

from typing import Any

from card.orders import (
    CardCandidate,
    aggregate_risk,
    check_placement,
    parse_selection,
    scan_candidates,
)
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
        qty=0.5,
        risk_usd=risk_usd,
        risk_frac=None,
        valid_until_ms=NOW_MS + 3_600_000,
        generated_at_ms=NOW_MS - 60_000,
        state_digest="abc123",
    )


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
        entry=100.05,
        sl=98.0,
        qty=0.5015,
        risk_usd=1.0,
        risk_frac=None,
        valid_until_ms=NOW_MS + 3_600_000,
        generated_at_ms=NOW_MS,
        state_digest="d",
    )
    d = check_placement(c, _FILT, managed=False, xs_live_marker=False, now_ms=NOW_MS)
    assert d.qty == 0.501  # floored to LOT_SIZE step
    assert d.price == 100.0  # long=BUY floors to tick, never crosses up
    assert d.risk_usd == round(0.501 * (100.0 - 98.0), 8)  # restated from ROUNDED
    assert d.warnings  # says the numbers moved


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

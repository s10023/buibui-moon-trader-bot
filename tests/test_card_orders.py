"""Tests for card/orders.py — scan, selection, guards, placement, refresh."""

from __future__ import annotations

from typing import Any

from card.orders import scan_candidates

NOW_MS = 1_756_000_000_000


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

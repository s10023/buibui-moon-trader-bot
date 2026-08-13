"""Golden-string renderer tests on fixed FinalCards."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from card.card import FinalCard, parse_trade_card
from card.render import render_card


def _final(verdict: str, **card_overrides: Any) -> FinalCard:
    obj: dict[str, Any] = {
        "verdict": "TRADE" if verdict != "NO_TRADE" else "NO_TRADE",
        "direction": "long" if verdict != "NO_TRADE" else None,
        "entry": 100.0 if verdict != "NO_TRADE" else None,
        "sl": 98.0 if verdict != "NO_TRADE" else None,
        "tp1": 103.0 if verdict != "NO_TRADE" else None,
        "tp2": 105.0 if verdict != "NO_TRADE" else None,
        "tp3": 108.0 if verdict != "NO_TRADE" else None,
        "confluence_score": 6,
        "reasoning": ["a 1", "b 2", "c 3", "d 4", "e 5"],
        "invalidation": "close below 97",
        "expected_hold": "12h",
        "valid_until_utc": "2026-07-11T12:00:00Z",
        "no_trade_reason": None if verdict != "NO_TRADE" else "regime conflict",
    }
    obj.update(card_overrides)
    return FinalCard(
        symbol="BTCUSDT",
        as_of_ms=1_760_000_000_000,
        verdict=verdict,
        card=parse_trade_card(json.dumps(obj)),
        size_units=12.5 if verdict == "TRADE" else None,
        notional_usd=1250.0 if verdict == "TRADE" else None,
        risk_usd=25.0 if verdict == "TRADE" else None,
        risk_frac=0.0025 if verdict == "TRADE" else None,
        capital_used=10_000.0 if verdict == "TRADE" else None,
        capital_source="config" if verdict == "TRADE" else None,
        rr_tp1=1.5 if verdict != "NO_TRADE" else None,
        warnings=["open risk approximated as one r_base per open position"],
        veto_reasons=["SL must be below entry for a long"]
        if verdict == "VETOED"
        else [],
        state_digest="d" * 64,
        prompt_version="card-v1",
        model="sonnet",
        generated_at_ms=1,
        cost_usd_notional=0.0123,
        horizon="intraday",
    )


class TestRender:
    def test_trade_card_layout(self) -> None:
        out = render_card(_final("TRADE"))
        assert "▲ TRADE" in out
        assert "BTCUSDT" in out
        assert "entry 100.0" in out
        assert "SL 98.0" in out
        assert "TP1 103.0" in out
        assert "RR(tp1) 1.50" in out
        assert "12.5 units" in out
        assert "risk $25.00" in out
        assert "confluence 6/9" in out
        assert "⚠ open risk approximated" in out
        assert "card-v1" in out
        assert "dddddddd" in out  # digest short-hash

    def test_no_trade_layout(self) -> None:
        out = render_card(_final("NO_TRADE"))
        assert "─ NO TRADE" in out
        assert "regime conflict" in out
        # the banner is the first line and carries no trade prices
        assert out.startswith("BUIBUI TRADE CARD — BTCUSDT · ─ NO TRADE")
        assert "gate: regime conflict" in out

    def test_cost_footer_reads_na_when_cost_none(self) -> None:
        final = dataclasses.replace(_final("TRADE"), cost_usd_notional=None)
        out = render_card(final)
        assert "cost n/a" in out
        assert "notional]" not in out  # no "$… notional" cost when absent

    def test_vetoed_layout(self) -> None:
        out = render_card(_final("VETOED"))
        assert "✕ VETOED" in out
        assert "SL must be below entry" in out

    def test_deterministic(self) -> None:
        assert render_card(_final("TRADE")) == render_card(_final("TRADE"))

    def test_partial_sizing_fields_suppress_size_block(self) -> None:
        # The four sizing fields are independently optional; a card with
        # size_units set but the money fields None must not crash — the
        # honest degradation is to skip the size block, not print $0.00.
        final = dataclasses.replace(_final("TRADE"), notional_usd=None, risk_usd=None)
        out = render_card(final)  # must not raise
        assert "▲ TRADE" in out
        assert "units" not in out


def test_render_states_the_capital_behind_the_risk() -> None:
    final = dataclasses.replace(
        _final("TRADE"), capital_used=1201.33, capital_source="live_equity"
    )
    out = render_card(final)
    assert "of 1,201.33 live_equity" in out


def test_render_omits_the_capital_note_when_absent() -> None:
    final = dataclasses.replace(_final("TRADE"), capital_used=None, capital_source=None)
    out = render_card(final)
    assert "% of capital" not in out
    assert "risk $25.00 (0.25%)" in out

"""Golden-string renderer tests on fixed FinalCards."""

from __future__ import annotations

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
        assert "entry" not in out.split("\n")[0]

    def test_vetoed_layout(self) -> None:
        out = render_card(_final("VETOED"))
        assert "✕ VETOED" in out
        assert "SL must be below entry" in out

    def test_deterministic(self) -> None:
        assert render_card(_final("TRADE")) == render_card(_final("TRADE"))

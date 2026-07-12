"""TradeCard parse/validation matrix (post-pass tests arrive in Task 7)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from card.card import parse_trade_card, validate_card_obj
from card.errors import CardValidationError


def _trade_obj(**overrides: Any) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "verdict": "TRADE",
        "direction": "long",
        "entry": 100.0,
        "sl": 98.0,
        "tp1": 103.0,
        "tp2": 105.0,
        "tp3": 108.0,
        "confluence_score": 6,
        "reasoning": ["a 1", "b 2", "c 3", "d 4", "e 5"],
        "invalidation": "close below 97",
        "expected_hold": "12h",
        "valid_until_utc": "2026-07-11T12:00:00Z",
        "no_trade_reason": None,
    }
    obj.update(overrides)
    return obj


class TestValidation:
    def test_valid_trade_parses(self) -> None:
        card = parse_trade_card(json.dumps(_trade_obj()))
        assert card.verdict == "TRADE"
        assert card.direction == "long"
        assert card.entry == 100.0
        assert card.reasoning[0] == "a 1"

    def test_valid_no_trade_parses(self) -> None:
        obj = _trade_obj(
            verdict="NO_TRADE",
            direction=None,
            entry=None,
            sl=None,
            tp1=None,
            tp2=None,
            tp3=None,
            no_trade_reason="regime conflict",
        )
        card = parse_trade_card(json.dumps(obj))
        assert card.verdict == "NO_TRADE"
        assert card.no_trade_reason == "regime conflict"

    def test_not_json_raises(self) -> None:
        with pytest.raises(CardValidationError):
            parse_trade_card("here is your card: buy")

    def test_bad_verdict(self) -> None:
        assert any(
            "verdict" in e for e in validate_card_obj(_trade_obj(verdict="MAYBE"))
        )

    def test_reasoning_count_bounds(self) -> None:
        assert validate_card_obj(_trade_obj(reasoning=["a"] * 4))
        assert validate_card_obj(_trade_obj(reasoning=["a"] * 9))
        assert not validate_card_obj(_trade_obj(reasoning=["a"] * 8))

    def test_confluence_bounds_and_type(self) -> None:
        assert validate_card_obj(_trade_obj(confluence_score=10))
        assert validate_card_obj(_trade_obj(confluence_score="6"))
        assert validate_card_obj(_trade_obj(confluence_score=True))

    def test_trade_requires_prices_and_direction(self) -> None:
        assert validate_card_obj(_trade_obj(entry=None))
        assert validate_card_obj(_trade_obj(sl=-1.0))
        assert validate_card_obj(_trade_obj(direction="up"))
        assert validate_card_obj(_trade_obj(valid_until_utc=None))

    def test_no_trade_requires_reason(self) -> None:
        obj = _trade_obj(verdict="NO_TRADE", no_trade_reason=None)
        assert any("no_trade_reason" in e for e in validate_card_obj(obj))

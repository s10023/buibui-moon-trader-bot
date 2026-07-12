"""Orchestrator: happy path, one re-ask on validation failure, then error."""

from __future__ import annotations

import json

import pytest

from card.client import LLMResponse
from card.config import CardConfig
from card.errors import CardValidationError
from card.run import generate_card
from card.state import MarketState
from portfolio.sizing import SizingConfig


def _state() -> MarketState:
    return MarketState(
        symbol="BTCUSDT",
        now_ms=1_760_000_000_000,
        direction_hint=None,
        panel=None,
        session_clock=None,
        pundit=None,
        xs=None,
        recent_fires=[],
        account=None,
        health=[],
    )


_VALID = json.dumps(
    {
        "verdict": "NO_TRADE",
        "direction": None,
        "entry": None,
        "sl": None,
        "tp1": None,
        "tp2": None,
        "tp3": None,
        "confluence_score": 2,
        "reasoning": ["a 1", "b 2", "c 3", "d 4", "e 5"],
        "invalidation": None,
        "expected_hold": None,
        "valid_until_utc": None,
        "no_trade_reason": "chop regime",
    }
)


class FakeClient:
    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> LLMResponse:
        self.prompts.append(prompt)
        return LLMResponse(
            text=self.texts.pop(0),
            model="sonnet",
            cost_usd_notional=0.01,
            input_tokens=1,
            output_tokens=1,
        )


class TestGenerateCard:
    def test_happy_path(self) -> None:
        client = FakeClient([_VALID])
        final = generate_card(
            _state(), CardConfig(), SizingConfig(), client, generated_at_ms=7
        )
        assert final.verdict == "NO_TRADE"
        assert final.generated_at_ms == 7
        assert final.model == "sonnet"
        assert final.cost_usd_notional == 0.01
        assert len(final.state_digest) == 64
        assert len(client.prompts) == 1

    def test_reask_appends_validation_errors(self) -> None:
        client = FakeClient(["not json at all", _VALID])
        final = generate_card(
            _state(), CardConfig(), SizingConfig(), client, generated_at_ms=7
        )
        assert final.verdict == "NO_TRADE"
        assert len(client.prompts) == 2
        assert "failed validation" in client.prompts[1]

    def test_second_failure_raises(self) -> None:
        client = FakeClient(["nope", "still nope"])
        with pytest.raises(CardValidationError):
            generate_card(
                _state(),
                CardConfig(),
                SizingConfig(),
                client,
                generated_at_ms=7,
            )

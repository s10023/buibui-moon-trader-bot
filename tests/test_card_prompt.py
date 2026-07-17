"""Prompt: byte-stable rubric, state embedding, direction hint."""

from __future__ import annotations

import json

from card.config import CardConfig
from card.prompt import PROMPT_VERSION, RUBRIC, build_prompt
from card.state import MarketState


def _state(hint: str | None = None) -> MarketState:
    return MarketState(
        symbol="BTCUSDT",
        now_ms=1_760_000_000_000,
        direction_hint=hint,
        panel=None,
        session_clock=None,
        pundit=None,
        xs=None,
        recent_fires=[],
        account=None,
        health=["panel: no data"],
    )


class TestPrompt:
    def test_version_constant(self) -> None:
        assert PROMPT_VERSION == "card-v2"

    def test_rubric_external_directions(self) -> None:
        # card-v2: external clusters are mapped liquidity with trust guards
        assert "panel.external" in RUBRIC
        assert "spot_hint_deviation" in RUBRIC
        assert "at most ONE agreeing input" in RUBRIC
        assert "stop-hunt warning" in RUBRIC

    def test_rubric_style_block(self) -> None:
        # card-v2: humanizer style directive covers all generated prose
        assert "Style (applies to reasoning, invalidation" in RUBRIC
        assert "No hedge words (might/could/perhaps)" in RUBRIC
        assert "no em dashes" in RUBRIC

    def test_rubric_prefix_is_byte_stable(self) -> None:
        cfg = CardConfig()
        p1 = build_prompt(_state(), cfg)
        p2 = build_prompt(_state(), cfg)
        assert p1 == p2
        assert p1.startswith(RUBRIC)

    def test_state_json_embedded_sorted(self) -> None:
        state = _state()
        prompt = build_prompt(state, CardConfig())
        assert json.dumps(state.to_dict(), sort_keys=True) in prompt

    def test_direction_hint_included_only_when_set(self) -> None:
        cfg = CardConfig()
        assert "operator is considering" not in build_prompt(_state(), cfg)
        hinted = build_prompt(_state("long"), cfg)
        assert "operator is considering a long" in hinted

    def test_rubric_names_no_absent_indicators(self) -> None:
        # fields that don't exist must never be claimed (spec + addendum)
        assert "RSI" not in RUBRIC
        assert "MACD" not in RUBRIC

    def test_schema_and_hard_rules_inlined(self) -> None:
        assert '"verdict"' in RUBRIC
        assert "ONLY a JSON object" in RUBRIC
        assert "confluence" in RUBRIC.lower()

    def test_rubric_external_trust_guards_pinned(self) -> None:
        # card-v2 delta fragments not covered by the original contract pins
        assert '"book" bands' in RUBRIC
        assert "higher intensity and lower age_hours" in RUBRIC
        assert "spot_hint_deviation true" in RUBRIC

"""Orchestrator: prompt -> LLM -> parse (one re-ask) -> post-pass."""

from __future__ import annotations

from card.card import FinalCard, parse_trade_card, post_pass
from card.client import LLMClient
from card.config import CardConfig
from card.errors import CardValidationError
from card.prompt import build_prompt
from card.state import MarketState, state_digest
from portfolio.sizing import SizingConfig


def generate_card(
    state: MarketState,
    cfg: CardConfig,
    sizing: SizingConfig,
    client: LLMClient,
    *,
    generated_at_ms: int,
) -> FinalCard:
    """One card, end to end. Raises CardError/CardValidationError on failure
    (nothing is written on failure — the CLI only persists a returned card)."""
    prompt = build_prompt(state, cfg)
    response = client.generate(prompt)
    try:
        card = parse_trade_card(response.text)
    except CardValidationError as exc:
        retry_prompt = (
            prompt
            + "\n\nYour previous response failed validation:\n- "
            + "\n- ".join(exc.errors)
            + "\nRespond again with ONLY a corrected JSON object."
        )
        response = client.generate(retry_prompt)
        card = parse_trade_card(response.text)  # second failure propagates
    return post_pass(
        card,
        state,
        sizing,
        cfg,
        digest=state_digest(state),
        model=response.model or cfg.model,
        generated_at_ms=generated_at_ms,
        cost_usd_notional=response.cost_usd_notional,
    )

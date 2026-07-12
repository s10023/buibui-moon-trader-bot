"""TradeCard schema validation + (Task 7) the deterministic post-pass."""

from __future__ import annotations

import json
from dataclasses import dataclass

from card.errors import CardValidationError

_VERDICTS = ("TRADE", "NO_TRADE")
_DIRECTIONS = ("long", "short")
_PRICE_KEYS = ("entry", "sl", "tp1", "tp2", "tp3")


@dataclass(frozen=True)
class TradeCard:
    verdict: str
    direction: str | None
    entry: float | None
    sl: float | None
    tp1: float | None
    tp2: float | None
    tp3: float | None
    confluence_score: int
    reasoning: list[str]
    invalidation: str | None
    expected_hold: str | None
    valid_until_utc: str | None
    no_trade_reason: str | None


def _is_num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_card_obj(obj: object) -> list[str]:
    """Schema errors for the raw LLM JSON object; [] when valid."""
    if not isinstance(obj, dict):
        return ["card is not a JSON object"]
    errors: list[str] = []
    verdict = obj.get("verdict")
    if verdict not in _VERDICTS:
        errors.append(f"verdict must be one of {list(_VERDICTS)}")
    reasoning = obj.get("reasoning")
    if (
        not isinstance(reasoning, list)
        or not 5 <= len(reasoning) <= 8
        or not all(isinstance(b, str) for b in reasoning)
    ):
        errors.append("reasoning must be a list of 5-8 strings")
    score = obj.get("confluence_score")
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 9:
        errors.append("confluence_score must be an integer in [0, 9]")
    if verdict == "TRADE":
        if obj.get("direction") not in _DIRECTIONS:
            errors.append("TRADE requires direction 'long' or 'short'")
        for key in _PRICE_KEYS:
            v = obj.get(key)
            if not _is_num(v) or float(v) <= 0.0:  # type: ignore[arg-type]
                errors.append(f"TRADE requires positive numeric {key}")
        if not isinstance(obj.get("valid_until_utc"), str):
            errors.append("TRADE requires valid_until_utc (ISO-8601 string)")
    if verdict == "NO_TRADE" and not isinstance(obj.get("no_trade_reason"), str):
        errors.append("NO_TRADE requires no_trade_reason")
    return errors


def parse_trade_card(text: str) -> TradeCard:
    """JSON-parse + validate the LLM output; CardValidationError on failure."""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CardValidationError([f"not valid JSON: {exc}"]) from exc
    errors = validate_card_obj(obj)
    if errors:
        raise CardValidationError(errors)

    def _f(key: str) -> float | None:
        v = obj.get(key)
        return float(v) if _is_num(v) else None

    def _s(key: str) -> str | None:
        v = obj.get(key)
        return v if isinstance(v, str) else None

    return TradeCard(
        verdict=str(obj["verdict"]),
        direction=_s("direction"),
        entry=_f("entry"),
        sl=_f("sl"),
        tp1=_f("tp1"),
        tp2=_f("tp2"),
        tp3=_f("tp3"),
        confluence_score=int(obj["confluence_score"]),
        reasoning=[str(b) for b in obj["reasoning"]],
        invalidation=_s("invalidation"),
        expected_hold=_s("expected_hold"),
        valid_until_utc=_s("valid_until_utc"),
        no_trade_reason=_s("no_trade_reason"),
    )

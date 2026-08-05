"""Dual ledger writer: ai-cards.jsonl (all) + pundit-calls.jsonl (TRADE)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from card.card import FinalCard
from card.config import CardConfig


def pundit_row(final: FinalCard) -> dict[str, str]:
    """Pundit-scorer-compatible row (the loader's exact 12 free-text keys).

    horizon uses the scorer's shortest pre-committed WINDOWS_MS key
    ("intraday" = 48h). Unknown keys no longer fall back silently — since
    the horizon guard landed, ``analytics.pundit_horizon`` rejects any value
    outside the enum at both ledger read boundaries, so inventing one here
    costs the whole row rather than mis-scoring it. Still never invent one:
    a loud loss is an improvement on a quiet wrong number, not a licence.
    """
    card = final.card
    ts = datetime.fromtimestamp(final.as_of_ms / 1000, tz=UTC)
    return {
        "source": "ai-card",
        "author": "buibui_card",
        "url": f"ai-card://{final.generated_at_ms}-{final.symbol}",
        "call_ts_utc": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol": final.symbol,
        "direction": card.direction or "",
        "entry": str(card.entry),
        "stop": str(card.sl),
        "target": str(card.tp1),
        "horizon": "intraday",
        "confidence": str(card.confluence_score),
        "raw_quote": card.reasoning[0] if card.reasoning else "",
    }


def _append_line(path: Path, obj: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, sort_keys=True) + "\n")


def append_ledgers(final: FinalCard, cfg: CardConfig) -> list[Path]:
    """Every card -> ai-cards.jsonl; TRADE cards also -> pundit-calls.jsonl."""
    cards_path = Path(cfg.cards_path)
    _append_line(cards_path, final.to_dict())
    written = [cards_path]
    if final.verdict == "TRADE":
        calls_path = Path(cfg.pundit_calls_path)
        _append_line(calls_path, dict(pundit_row(final)))
        written.append(calls_path)
    return written

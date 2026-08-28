"""Post-card GTX order helper: scan, guards, placement, instrumentation.

Spec: docs/superpowers/specs/2026-08-28-st37-card-gtx-order-helper-design.md
Places picked TRADE cards as post-only (GTX) limit entries and records every
placement + terminal state in an append-only jsonl ledger, joined back to the
card by (symbol, generated_at_ms).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from card.card import _parse_iso_ms

DEFAULT_ORDERS_PATH = "docs/plans/card-orders.jsonl"
# The XS executor writes execution_state_{mode}.json per mode; a live-mode run
# leaves this marker. Testnet orders live on a different venue and cannot
# collide with mainnet card orders, so only "live" gates.
XS_LIVE_MARKER = Path("docs/plans/xsmom_targets/execution_state_live.json")


@dataclass(frozen=True)
class CardCandidate:
    symbol: str
    direction: str  # "long" | "short"
    entry: float
    sl: float
    qty: float
    risk_usd: float | None
    risk_frac: float | None
    valid_until_ms: int
    generated_at_ms: int
    state_digest: str


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a jsonl file, tolerating a torn tail line (append-only ledgers)."""
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def scan_candidates(
    card_rows: list[dict[str, Any]],
    order_rows: list[dict[str, Any]],
    now_ms: int,
) -> list[CardCandidate]:
    """Unexpired TRADE cards not already placed.

    Anti-join on (symbol, generated_at_ms): a gtx_rejected placement row also
    suppresses re-presentation on purpose — a rejection means price is already
    through the level, so the setup is stale by definition.
    """
    placed = {
        (r.get("symbol"), r.get("card_generated_at_ms"))
        for r in order_rows
        if r.get("kind") == "placement"
    }
    out: list[CardCandidate] = []
    for row in card_rows:
        if row.get("verdict") != "TRADE":
            continue
        card = row.get("card") or {}
        expiry = _parse_iso_ms(str(card.get("valid_until_utc") or ""))
        if expiry is None or expiry <= now_ms:
            continue
        if (row.get("symbol"), row.get("generated_at_ms")) in placed:
            continue
        direction = card.get("direction")
        entry = card.get("entry")
        sl = card.get("sl")
        qty = row.get("size_units")
        if direction not in ("long", "short") or not entry or not sl or not qty:
            continue
        risk_usd = row.get("risk_usd")
        risk_frac = row.get("risk_frac")
        out.append(
            CardCandidate(
                symbol=str(row["symbol"]),
                direction=str(direction),
                entry=float(entry),
                sl=float(sl),
                qty=float(qty),
                risk_usd=float(risk_usd) if risk_usd is not None else None,
                risk_frac=float(risk_frac) if risk_frac is not None else None,
                valid_until_ms=expiry,
                generated_at_ms=int(row["generated_at_ms"]),
                state_digest=str(row.get("state_digest") or ""),
            )
        )
    return out

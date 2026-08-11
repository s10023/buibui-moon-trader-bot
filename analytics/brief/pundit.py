"""Pundit board — FILE contract over pundit-calls.jsonl + pundit-priors.json.

Deliberately no import from the scorer tool: the board must work while the
scorer is unbuilt/absent and degrade gracefully on missing or malformed files.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analytics.brief.config import BriefConfig
from analytics.brief.types import (
    PunditAuthorPrior,
    PunditBoard,
    PunditCallRow,
    PunditFamilyPrior,
)
from analytics.pundit_authors import normalize_author
from analytics.pundit_direction import normalize_direction
from analytics.pundit_horizon import normalize_horizon

_DAY_MS = 86_400_000
_MAX_PRIORS_ROWS = 8  # render constant per spec, not config
_TEXT_TRUNC = 60
# The AI trade card dual-writes its TRADE calls to the same ledger so the
# scorer can grade it, but the card is never an external pundit to itself:
# exclude its rows from the board (keyed off the stable `source` machine field)
# and its scored track record from the priors-derived authors list.
_AI_CARD_SOURCE = "ai-card"
_AI_CARD_AUTHOR = "buibui_card"


def _parse_iso_ms(value: str) -> int | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def truncate_call_text(text: str) -> str:
    """Clip a call's entry/target for the MARKDOWN brief's one-line-per-call format.

    Lives here beside the ledger read, but is applied by `render.py` only. It used
    to be applied in `build_board`, i.e. to the shared `PunditBoard` every surface
    consumes — so the API and the Brief tab received text already destroyed at 60
    characters ("...upside continuation above 6…"), and no amount of CSS could
    show what was no longer there. A terminal's line-width constraint is not the
    web UI's constraint; keep it at the renderer that actually has it.
    """
    return text if len(text) <= _TEXT_TRUNC else text[: _TEXT_TRUNC - 1] + "…"


def _opt_float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _load_priors(
    path: Path, as_of_ms: int
) -> tuple[
    str, int | None, int | None, dict[str, PunditAuthorPrior], list[PunditFamilyPrior]
]:
    """(status, age_days, min_n_marker, authors_by_name, top families)."""
    if not path.exists():
        return "absent", None, None, {}, []
    try:
        data = json.loads(path.read_text())
        authors_raw = data["authors"]
        if not isinstance(authors_raw, dict):
            raise TypeError("authors must be a mapping")
        families_raw = data.get("families", {})
        policy_raw = data.get("policy", {})
        policy = policy_raw if isinstance(policy_raw, dict) else {}
        min_n = int(policy.get("min_n_marker", 5))

        age_days: int | None = None
        gen_ms = _parse_iso_ms(str(data.get("generated_at", "")))
        if gen_ms is not None:
            age_days = max(0, (as_of_ms - gen_ms) // _DAY_MS)

        authors: dict[str, PunditAuthorPrior] = {}
        for name, cell in authors_raw.items():
            if not isinstance(cell, dict):
                continue
            n = int(cell.get("n", 0))
            # Keys are normalised on load, not just on write, so a priors file
            # generated BEFORE the scorer normalised still joins to the ledger
            # instead of silently missing every '@'-prefixed author until the
            # next `make buibui-pundit-score`.
            key = normalize_author(str(name))
            prior_existing = authors.get(key)
            if prior_existing is not None and prior_existing.n >= n:
                # A pre-fix file can hold BOTH halves of one split author.
                # Merging their rates would need the underlying rows, which are
                # not in this file, so keep the better-supported half and let
                # the regen produce the true combined figure. Deterministic by
                # n, never by dict order.
                continue
            authors[key] = PunditAuthorPrior(
                author=key,
                n=n,
                hit_rate=_opt_float(cell.get("hit_rate")),
                avg_r=_opt_float(cell.get("avg_r")),
                avg_atr_r=_opt_float(cell.get("avg_atr_r")),
                flagged=n < min_n,
            )
        families: list[PunditFamilyPrior] = []
        if isinstance(families_raw, dict):
            for fam, dirs in families_raw.items():
                if not isinstance(dirs, dict):
                    continue
                for direction, cell in dirs.items():
                    if not isinstance(cell, dict):
                        continue
                    n = int(cell.get("n", 0))
                    families.append(
                        PunditFamilyPrior(
                            family=str(fam),
                            direction=str(direction),
                            n=n,
                            hit_rate=_opt_float(cell.get("hit_rate")),
                            avg_r=_opt_float(cell.get("avg_r")),
                            avg_atr_r=_opt_float(cell.get("avg_atr_r")),
                            flagged=n < min_n,
                        )
                    )
        families.sort(key=lambda f: -f.n)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError):
        return "unreadable", None, None, {}, []
    return "ok", age_days, min_n, authors, families[:_MAX_PRIORS_ROWS]


def build_board(cfg: BriefConfig) -> PunditBoard:
    status, age_days, min_n, authors_by_name, families = _load_priors(
        cfg.priors_path, cfg.as_of_ms
    )
    ledger_status = "ok" if cfg.ledger_path.exists() else "absent"
    total = 0
    skipped = 0
    calls: list[PunditCallRow] = []
    if ledger_status == "ok":
        for line in cfg.ledger_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(raw, dict):
                skipped += 1
                continue
            if raw.get("source") == _AI_CARD_SOURCE:
                continue  # card's own dual-write — excluded, not malformed
            ts_ms = _parse_iso_ms(str(raw.get("call_ts_utc", "")))
            author = raw.get("author")
            symbol = raw.get("symbol")
            if ts_ms is None or not author or not symbol:
                skipped += 1
                continue
            # Same enum the scorer enforces — validating one side only would
            # leave the board rendering values the scorer refuses, the mirror
            # of the author-key split this module already guards against. An
            # empty/missing direction still lands here as a ValueError, so the
            # previous falsy check is subsumed, not dropped.
            try:
                direction = normalize_direction(str(raw.get("direction") or ""))
            except ValueError:
                skipped += 1
                continue
            # Validated, deliberately NOT stored. The board only *prints*
            # horizon (render.py appends it verbatim) and never computes on
            # it, so no wrong number is possible here — but the scorer skips
            # an unrecognised one **permanently**, unlike a merely unresolved
            # recent call, and rendering it would imply a tracked call that
            # will never be tracked. Storing the normalised value instead
            # would be a silent display change: absence canonicalises to
            # "unspecified", which is truthy, so every row that shows no
            # horizon today would grow a "· unspecified" suffix.
            try:
                normalize_horizon(raw.get("horizon"))
            except ValueError:
                skipped += 1
                continue
            age = (cfg.as_of_ms - ts_ms) // _DAY_MS
            if age < 0 or age > cfg.recent_call_days:
                continue  # future-dated or too old — excluded, not skipped
            # Same normalisation the scorer applies when it writes the priors
            # JSON — this is the join key, so the two must not drift apart.
            author_key = normalize_author(str(author))
            calls.append(
                PunditCallRow(
                    author=author_key,
                    symbol=str(symbol),
                    direction=direction,
                    # Full text: the markdown renderer clips its own one-liner.
                    entry=str(raw.get("entry") or ""),
                    target=str(raw.get("target") or ""),
                    horizon=str(raw.get("horizon") or ""),
                    age_days=int(age),
                    on_panel=str(symbol) in cfg.symbols,
                    prior=authors_by_name.get(author_key),
                )
            )
    calls.sort(key=lambda c: c.age_days)  # newest first; ties keep file order
    top_authors = sorted(
        (a for a in authors_by_name.values() if a.author != _AI_CARD_AUTHOR),
        key=lambda a: -a.n,
    )
    return PunditBoard(
        priors_status=status,
        priors_age_days=age_days,
        min_n_marker=min_n,
        ledger_status=ledger_status,
        ledger_total=total,
        ledger_skipped=skipped,
        recent_calls=calls[: cfg.max_recent_calls],
        authors=top_authors[:_MAX_PRIORS_ROWS],
        families=families,
    )

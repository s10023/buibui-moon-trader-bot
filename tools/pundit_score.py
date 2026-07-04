"""Score the pundit-call ledger against stored OHLCV — measured priors per author/family.

Read-only sibling of ``tools/journal_fetch.py`` / the audit drivers: resolves every
``docs/plans/pundit-calls.jsonl`` call (free-text levels) against 1h/1d OHLCV and
reports hit-rate + R proxies per author x setup-family x direction, plus a
machine-readable ``docs/plans/pundit-priors.json`` sidecar. Descriptive priors only —
no ENABLE/BUILD verdicts (audit_guard gates come later, only if a cell earns n>=30).
Never writes to the DB; no schema change.

Spec: docs/superpowers/specs/2026-07-04-pundit-ledger-scorer-design.md

Usage::

    PYTHONPATH=. poetry run python tools/pundit_score.py \
        [--ledger docs/plans/pundit-calls.jsonl] \
        [--overrides docs/plans/pundit-overrides.jsonl] \
        [--db analytics.db] [--as-of 2026-07-04T00:00:00Z] \
        [--json docs/plans/pundit-priors.json] [--min-n 5]
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
WINDOWS_MS: dict[str, int] = {
    "intraday": 48 * HOUR_MS,
    "swing": 30 * DAY_MS,
    "unspecified": 14 * DAY_MS,
}
SANITY_LO = 0.2
SANITY_HI = 5.0

_UNSPECIFIED_MARKERS = {"", "unspecified", "none", "n/a", "not specified"}
_ZONE_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*([kK])?\s*(?:-|–|\bto\b)\s*(\d[\d,]*(?:\.\d+)?)\s*([kK])?"
)
_NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([kK])?")


def _expand(num_text: str, k_suffix: str | None) -> float:
    """'57,900' -> 57900.0; '60.5' + 'k' -> 60500.0."""
    return float(num_text.replace(",", "")) * (1000.0 if k_suffix else 1.0)


@dataclass(frozen=True)
class ParsedField:
    """Raw level candidates extracted from one free-text field."""

    zones: tuple[tuple[float, float], ...]
    numbers: tuple[float, ...]
    unspecified: bool


def parse_level_field(text: str | None) -> ParsedField:
    """Extract zone and single-number candidates from a ledger level field."""
    if text is None or str(text).strip().lower() in _UNSPECIFIED_MARKERS:
        return ParsedField(zones=(), numbers=(), unspecified=True)
    cleaned = str(text).replace("$", "").replace("~", "")
    zones: list[tuple[float, float]] = []
    for zm in _ZONE_RE.finditer(cleaned):
        a = _expand(zm.group(1), zm.group(2))
        b = _expand(zm.group(3), zm.group(4))
        zones.append((min(a, b), max(a, b)))
    numbers = tuple(
        _expand(nm.group(1), nm.group(2)) for nm in _NUM_RE.finditer(cleaned)
    )
    return ParsedField(zones=tuple(zones), numbers=numbers, unspecified=False)

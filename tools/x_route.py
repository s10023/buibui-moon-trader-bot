"""Pure routing decision for ingested X posts (no I/O).

content_type gate, then the parent pipeline's 4-bucket verdict taxonomy on the
claim path. See docs/superpowers/specs/2026-06-30-x-post-ingest-design.md.
"""

from __future__ import annotations

_DROP_VERDICTS = {"ALREADY-TESTED", "FROZEN-CATEGORY", "NOT-FALSIFIABLE"}


def route_target(
    content_type: str,
    verdict: str,
    *,
    retrospective: bool = False,
    rejected: bool = False,
    unattributable: bool = False,
) -> str | None:
    """The sink for one ingested item, or None to drop it.

    Every suppressor applies to `setup` only — a mechanic or a claim has no entry to
    decline and scores nobody, and both skills already pin the flags to false there, so
    honouring them outside `setup` would let one mis-set field silently delete a
    routable item.

    They default to False so every existing caller keeps its behaviour; a source that
    cannot express the distinction (an X post today) simply never sets them.

    - `retrospective` — the speaker is reviewing a position entered BEFORE this item.
      Scoring it forward from this timestamp flatters the author, because part of the
      outcome is already known.
    - `rejected` — the speaker walked through the trade and then argued AGAINST taking
      it. Routing it scores the author on a trade they declined. This shipped once
      (2026-07-31 round 3) and was caught only by the human reading the digest.
    - `unattributable` — the speaker relayed someone else's call and the roster could
      not resolve that person to a canonical handle (unknown name, unmapped name, or a
      string marked never_auto_attribute). Routing it would credit the relaying channel
      with a call they did not make, and `tools/pundit_score.py` groups on `author`, so
      no downstream check can see the error.
    """
    if content_type == "setup":
        if retrospective or rejected or unattributable:
            return None
        return "docs/plans/pundit-calls.jsonl"
    if content_type == "mechanic":
        return "docs/plans/mechanics-backlog.md"
    if content_type == "claim":
        if verdict == "NOVEL":
            return "docs/plans/thesis-inbox.md"
        if verdict in _DROP_VERDICTS:
            return None
    raise ValueError(f"unroutable: content_type={content_type!r} verdict={verdict!r}")

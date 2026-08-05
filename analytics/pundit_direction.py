"""Direction enum for the pundit ledger — pure, no IO.

The ledger's ``direction`` field is written by the ingest *skills* as free
JSON, not by any Python write path, so nothing in code ever asserted its
domain. Both readers then assumed one:

- ``tools/pundit_score.py`` computes ``dirsign = 1.0 if direction == "long"
  else -1.0``, having special-cased only the literal ``"neutral"`` earlier.
  **Every value outside the enum was therefore booked as a SHORT**, with
  nothing raising.
- ``analytics/brief/pundit.py`` skipped a *falsy* direction but passed any
  truthy string through to the board verbatim.

Measured on 2026-08-05: pass 2 of ``/ingest-video`` emitted
``direction: "range"`` for a range-trade plan — a deliberately
non-directional call that would have scored as bearish. It was caught by
reading the scorer, not by any check, which is the point: the wrong number
is indistinguishable from a right one on every downstream surface.

A missing field is the same defect. It arrives as ``""``, which is likewise
not ``"long"``, so absence scored as a short too. Both are rejected here.

**Why this lives beside** ``pundit_authors.py`` **rather than in either
reader.** That module exists because normalising the author key on one side
of the ledger/priors join and not the other turns a split track record into
a silent lookup miss — worse than the bug it fixes. The same trap applies
here: a guard on the scorer alone would leave the Brief's board rendering
values the scorer refuses. One definition, imported by both.

Kept deliberately strict rather than coercing to a default: there is no safe
default for a direction. Guessing ``neutral`` would silently discard a real
call, and guessing ``long``/``short`` invents one.
"""

from __future__ import annotations

VALID_DIRECTIONS = frozenset({"long", "short", "neutral"})
"""The exact set ``pundit_score.score_call`` branches on.

``"neutral"`` returns UNSCORED; ``"long"`` and ``"short"`` set ``dirsign``.
If this set ever grows, that branch must be revisited in the same commit —
adding a member here without touching it re-creates the original bug.
"""


def normalize_direction(raw: str) -> str:
    """Canonical direction key, or raise ``ValueError``.

    Case and surrounding whitespace are decoration, not identity, so they are
    folded rather than rejected — the scorer already lowercased on read and
    dropping ``"SHORT"`` would be a regression. Anything else raises, and the
    message names both the field and the offending value so a caller that
    turns it into a per-line warning stays actionable.

    Idempotent, which is what lets it be applied at read time on both sides
    without a data migration.
    """
    value = raw.strip().lower()
    if value not in VALID_DIRECTIONS:
        raise ValueError(f"direction {raw!r} is not one of {sorted(VALID_DIRECTIONS)}")
    return value

"""Attribution enums for the pundit ledger — pure, no IO.

The third sibling of :mod:`analytics.pundit_direction` and
:mod:`analytics.pundit_horizon`, and the same *family* of defect — a ledger
field the writer fills in and no reader ever looks at — but it arrives from
the opposite direction. Those two guarded a field the scorer **computed on**
and could therefore get wrong. This one guards a field the scorer
**discarded**: ``tools.pundit_score.LedgerCall`` never carried ``attribution``
or ``attribution_confidence`` at all, so every relay row was scored at exactly
the same trust as a first-hand one.

Why that matters, measured on the live ledger (2026-08-08, 203 rows)::

    relay 22 · first-hand 11 · absent 170

Those 22 relay rows belong to **eleven authors who exist ONLY because of relay
attribution** — 1–4 rows each, none of whom has a single first-hand row. Every
one of them is a phantom pundit with a fabricated track record if the roster
mapping behind it is wrong, and all 22 sit at ``confidence = "operator"``,
which ``config/pundit_roster.toml.example`` defines as "supplied by the
operator, not independently checked".

The relay-attribution design doc (``docs/superpowers/specs/
2026-08-05-relay-attribution-design.md``, decision D2) chose to *record*
confidence rather than route verified-only, on the grounds that recording
"turns an invisible misattribution into a filterable, revocable one". The
recording shipped; the filtering never did, which left the row half-built —
the cost of the permissive choice was paid without the benefit that justified
it. This module is the missing half.

**Absence is legitimate, and defaulting is the interesting decision here.**
170 of 203 rows predate the feature. They came from the first-hand ingest
passes, so ``"first-hand"`` is the truthful default — but a bare default is
fail-OPEN, because a future writer who omits ``attribution`` on a genuine
relay row would have it silently scored at full trust. So the default is
cross-checked against ``relayed_by``: a row that names a relaying channel is
a relay row whatever the ``attribution`` field says or fails to say. That is
the one case where these two fields can contradict each other, and the
untrusting reading wins.

**A missing confidence on a relay row does NOT raise.** The precedent is
``pundit_horizon``: a row the scorer refuses is skipped *permanently*, which
is a worse outcome than a row that is scored and flagged. Unknown confidence
sorts below ``"operator"`` in :data:`CONFIDENCE_RANK` and is caught by the
filter, so it is visible and revocable without costing evidence.

Not live in the sense that no published number was wrong: with the default
``--min-attribution-confidence any`` every row still scores exactly as before,
so this ships as prevention plus visibility, not as a correction.
"""

from __future__ import annotations

VALID_ATTRIBUTIONS = frozenset({"first-hand", "relay"})
"""How the ledger row's ``author`` was determined.

``"first-hand"`` — the author spoke for themselves on their own channel.
``"relay"`` — an aggregator channel relayed someone else's call and
``tools/pundit_roster.py`` resolved the spoken name to a handle.
"""

VALID_ATTRIBUTION_CONFIDENCES = frozenset({"high", "operator"})
"""Roster confidence, mirroring ``config/pundit_roster.toml``'s own vocabulary.

``"high"`` = corroborated in-repo · ``"operator"`` = operator-supplied, not
independently checked. Keep in step with the roster file: a value legal there
and unknown here would raise on a row that is in fact well-formed.
"""

CONFIDENCE_RANK: dict[str, int] = {"": 0, "operator": 1, "high": 2}
"""Trust ordering for :func:`meets_confidence`. Unknown ranks LOWEST.

``""`` covers two distinct cases that happen to want the same treatment: a
relay row whose confidence was never written, and a first-hand row where the
field has no meaning. The second is never filtered on — see
:func:`meets_confidence`, which exempts first-hand rows entirely rather than
ranking them.
"""


def normalize_attribution(raw: object, *, relayed_by: object = "") -> str:
    """Canonical attribution key, or raise ``ValueError``.

    Missing or empty normalises to ``"first-hand"`` unless ``relayed_by``
    names a channel, in which case the row is ``"relay"`` regardless — the
    presence of a relaying channel is positive evidence, while the absence of
    an ``attribution`` field is merely silence, and silence must not buy
    trust.

    Case and surrounding whitespace are folded, matching
    ``normalize_direction``. Idempotent, so it can be applied at every read
    boundary without a data migration.
    """
    value = str(raw or "").strip().lower()
    if not value:
        return "relay" if str(relayed_by or "").strip() else "first-hand"
    if value not in VALID_ATTRIBUTIONS:
        raise ValueError(
            f"attribution {raw!r} is not one of {sorted(VALID_ATTRIBUTIONS)}"
        )
    return value


def normalize_attribution_confidence(raw: object, *, attribution: str) -> str:
    """Canonical roster-confidence key, or raise ``ValueError``.

    A **present** value outside the enum raises, per the house convention: the
    writer meant something no reader can honour. A **missing** one never
    raises — see the module docstring on why refusing the row is the worse
    failure.

    On a first-hand row the field has no meaning (no roster mapping was
    consulted), so any value is discarded and ``""`` returned. That is a
    deliberate silent drop rather than a raise: the row's ``author`` is
    self-evident, so a stray confidence string is noise, not a defect worth
    costing evidence over.
    """
    value = str(raw or "").strip().lower()
    if value and value not in VALID_ATTRIBUTION_CONFIDENCES:
        raise ValueError(
            f"attribution_confidence {raw!r} is not one of "
            f"{sorted(VALID_ATTRIBUTION_CONFIDENCES)}"
        )
    if attribution == "first-hand":
        return ""
    return value


def meets_confidence(attribution: str, confidence: str, floor: str) -> bool:
    """Does this row clear the ``floor`` trust level?

    **First-hand rows always pass**, at every floor. They carry no roster
    assertion, so filtering them on a roster-confidence field would drop
    exactly the rows the field was never about — the reason this is not a
    plain rank comparison.

    ``floor`` of ``"any"`` passes everything, which is the default the CLI
    ships so that no published number moves on this commit alone.
    """
    if floor == "any" or attribution == "first-hand":
        return True
    return CONFIDENCE_RANK.get(confidence, 0) >= CONFIDENCE_RANK.get(floor, 0)

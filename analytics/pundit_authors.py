"""Author identity for the pundit ledger — pure, no IO.

``tools/pundit_score.py`` groups on ``author`` and needs zero changes to
work, which is exactly why a wrong ``author`` is cheap to write and
expensive to detect: nothing crashes, the report simply reports two shorter
track records for one person.

Measured on the live ledger 2026-08-05, before this module existed:

    '@MauriceTrades'     4 rows (twitter)     one person,
    'MauriceTrades'      5 rows (twitter)     two half-length records

    '@SailorManCrypto'   2 rows (youtube)     one person,
    'SailorManCrypto'    9 rows (twitter)     two half-length records

43 author keys collapsed to 41 once a leading ``@`` stopped counting as
identity. Neither half reaches an ``n`` threshold the whole would clear, so
the effect is not cosmetic: it suppresses real pundits out of the priors
table entirely.

**Scope, stated honestly.** This handles the mechanical part of identity — a
leading ``@`` and stray whitespace are decoration, not identity. It does NOT
handle:

- **case variants** (``@traderfengge`` vs ``@Traderfengge``). X handles are
  case-insensitive, so these ARE the same account and would still split.
  Zero instances in the ledger today; folding case here would also lowercase
  every display name, so it is deliberately left to the roster below rather
  than paid for now.
- **aliases and transliterations** (``三马哥`` / ``三码哥`` / ``萨玛哥`` are
  one person; ``舒琴`` arrives ASR-mangled as ``输情`` / ``瞬间``).
- **collisions**, where one string is several people — ``陈志峰`` runs three
  traders together, the last being ``峰哥`` = ``@Traderfengge``.

Those need a curated name->handle **roster**, which is the tracked follow-up
and the reason Kolunite ingestion is paused. A roster is the right home for
all three, and it subsumes the case problem, so this module stays mechanical
on purpose: it fixes what can be fixed without asserting who anyone is.
"""

from __future__ import annotations


def normalize_author(raw: str) -> str:
    """Canonical author key: strip whitespace and any leading ``@``.

    Idempotent — ``normalize_author(normalize_author(x)) == normalize_author(x)``
    — which is what lets it be applied at read time on both sides of the
    ledger/priors join without a data migration.

    An author consisting only of ``@`` or whitespace normalises to the empty
    string; callers already treat a falsy author as a skipped row, so this
    returns the stripped original in that case rather than inventing a key.
    """
    stripped = raw.strip()
    out = stripped.lstrip("@").strip()
    return out or stripped

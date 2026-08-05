"""Horizon enum for the pundit ledger — pure, no IO.

The sibling of :mod:`analytics.pundit_direction`, and the same defect class,
but **not the same shape** — copying that guard verbatim would be wrong.

``horizon`` selects the scoring window in ``tools.pundit_score.window_ms``::

    WINDOWS_MS.get(horizon, WINDOWS_MS["unspecified"])

so a value the table has never heard of silently takes **14 days** instead of
intraday's 48h or swing's 30d. That is a different WIN / LOSS /
NOT_TRIGGERED verdict for the same call, with nothing raising — a scalp
stopped out in two hours can be booked a winner because price recovered by
day ten. The fallback was docstringed as deliberate, which is the only thing
that separated it from the ``direction`` bug rather than a reason it was safe.

**Where this diverges from ``direction``: absence is legitimate here.**
``unspecified`` is a real member of the enum — a pundit who states no
timeframe has still made a scoreable call, and 4 of the 174 live rows say so
explicitly. So a missing or empty field normalises to ``unspecified`` and is
*not* an error. Only a value that is **present and unrecognised** is a
defect, because that is the only case where the writer meant something the
scorer cannot honour. ``window_ms`` cannot tell those two apart from the
inside — by then the raw field is gone — which is why the check belongs at
the read boundary, where it can still see the difference.

Not live: all 174 rows were in-enum when this shipped (swing 132 / intraday
38 / unspecified 4). This is prevention, and no published number was wrong.

**Applied at both read boundaries**, like ``direction``: the scorer, and
``analytics/brief/pundit.py``. The Brief only *prints* horizon and never
computes on it, so no wrong number is possible on that surface — but a row
the scorer refuses is skipped **permanently**, unlike a merely unresolved
recent call, and a board that renders it implies a tracked call that will
never be tracked.
"""

from __future__ import annotations

VALID_HORIZONS = frozenset({"intraday", "swing", "unspecified"})
"""Exactly the keys of ``pundit_score.WINDOWS_MS``.

Bound to that table by ``tests/test_pundit_horizon.py``. Adding a member here
without a window there re-creates the original bug in a new place: the new
horizon would fall through to unspecified's 14 days, silently.
"""

_ABSENT = "unspecified"


def normalize_horizon(raw: str | None) -> str:
    """Canonical horizon key, or raise ``ValueError``.

    ``None`` and blank normalise to ``"unspecified"`` — see the module
    docstring: absence is a member of the enum, not a violation of it. Case
    and surrounding whitespace are folded, matching ``normalize_direction``.
    Anything else raises, naming the field and the offending value so a
    caller that turns it into a per-line warning stays actionable.

    Idempotent, which is what lets it be applied at read time on both sides
    without a data migration.
    """
    if raw is None:
        return _ABSENT
    if not isinstance(raw, str):
        raise ValueError(f"horizon {raw!r} is not a string")
    value = raw.strip().lower()
    if not value:
        return _ABSENT
    if value not in VALID_HORIZONS:
        raise ValueError(f"horizon {raw!r} is not one of {sorted(VALID_HORIZONS)}")
    return value

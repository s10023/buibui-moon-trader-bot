"""The published sleeve gate — one definition, so it cannot drift per sleeve.

**THREE legs, not four**::

    DSR >= 0.95  AND  PBO <= 0.5  AND  boot_lo > 0

All five sleeves (xsmom, forecast, combine, carry, xsrev) implement exactly
this. ``min_track_record_length`` is computed and printed beside it as a
**stamp that gates nothing**, and that is deliberate: MinTRL against a non-zero
target asks "can I confirm Sharpe >= 1?", a far harder question than "is there
an edge here at all". The deploy core would not clear a MinTRL leg — it needs
~7035 observations to confirm Sharpe > 1.0 at 95% and has ~2475, which its own
audit discloses at ``docs/audits/2026-06-16-p3-xsmom-sleeve.md:105``. Quoting
the four-leg form is a documented recurring error; do not reintroduce it here.

``corr_to_trend`` is likewise **not** a leg. P3's spec §Verdict criterion 4
originally read "corr_to_trend near zero"; xsmom cleared the gate at **+0.37**
and the criterion was amended 2026-08-06 to the disqualifier it always was in
practice. Coding it as a pass condition would fail the one sleeve that carries
capital.

Why this module exists: the thresholds lived only in
``analytics/combine/report.py`` as ``_GATE_DSR`` / ``_GATE_PBO``, so a second
sleeve wanting a coded verdict had to either import a private name across a
package boundary or restate the numbers. Restating is the failure mode this
repo keeps meeting — a bare number that looks portable, is copied, and then
silently means something else. One definition, imported.
"""

from __future__ import annotations

import math

GATE_DSR = 0.95
"""Deflated Sharpe floor — the probability the Sharpe survives multiple testing."""

GATE_PBO = 0.5
"""Probability-of-backtest-overfitting ceiling, from CSCV."""


def passes_gate(dsr: float, pbo: float, boot_lo: float) -> bool:
    """The three-leg gate. ``True`` only if all three legs clear.

    A NaN ``pbo`` fails rather than propagating: CSCV returns NaN when the
    trial matrix is too small to split, and ``float('nan') <= 0.5`` is
    ``False`` in Python anyway — this makes that intent explicit instead of
    leaving a passing verdict resting on IEEE comparison semantics.

    ``dsr`` and ``boot_lo`` are compared directly. A NaN in either also fails,
    for the same reason and by the same mechanism.
    """
    if math.isnan(pbo):
        return False
    return dsr >= GATE_DSR and pbo <= GATE_PBO and boot_lo > 0.0

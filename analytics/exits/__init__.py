"""Exit-policy research package (exit spec 2026-06-05).

Diagnostic-only for now: the §2 MFE/MAE excursion study. The policy library
(`policies.py`) and pluggable replay engine (`replay.py`) land with the
exit-sweep PR, gated on this diagnostic's verdict.
"""

from analytics.exits.mfe_mae import (
    EXCURSION_COLUMNS,
    aggregate_cohorts,
    compute_excursions,
)

__all__ = ["EXCURSION_COLUMNS", "aggregate_cohorts", "compute_excursions"]

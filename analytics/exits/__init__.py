"""Exit-policy research package (exit spec 2026-06-05).

The §2 MFE/MAE excursion study (`mfe_mae.py`), the policy config layer
(`policies.py`), and the pluggable exit-replay engine (`replay.py`). The
audit driver (`tools/exit_audit.py`) re-resolves the ledger under each policy
and feeds the P1 paper book for a risk-adjusted A/B.
"""

from analytics.exits.mfe_mae import (
    EXCURSION_COLUMNS,
    aggregate_cohorts,
    compute_excursions,
)
from analytics.exits.policies import ExitPolicyConfig, composite, fixed
from analytics.exits.replay import ExitOutcome, replay_exits

__all__ = [
    "EXCURSION_COLUMNS",
    "ExitOutcome",
    "ExitPolicyConfig",
    "aggregate_cohorts",
    "composite",
    "compute_excursions",
    "fixed",
    "replay_exits",
]

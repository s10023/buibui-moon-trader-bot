"""Research guardrails: overfitting & multiple-testing controls.

Pure-math statistics (no DB / IO / network) used to gate strategy selection
against in-sample mirages: Probabilistic & Deflated Sharpe, PBO/CSCV, the
multiple-testing Sharpe haircut, Minimum Track Record Length, and block /
stationary bootstrap confidence intervals.

Eager re-exports so callers can do
``from analytics.research_guards import deflated_sharpe_ratio, cscv_pbo``.
"""

from analytics.research_guards.bootstrap import BootstrapCI, block_bootstrap_ci
from analytics.research_guards.cluster import (
    ClusterStats,
    cluster_bootstrap_ci,
    cluster_stats,
    utc_day_keys,
)
from analytics.research_guards.dsr import (
    EULER_MASCHERONI,
    deflated_sharpe_ratio,
    expected_max_sharpe,
)
from analytics.research_guards.gate import GATE_DSR, GATE_PBO, passes_gate
from analytics.research_guards.haircut import HaircutResult, haircut_sharpe
from analytics.research_guards.mintrl import min_track_record_length
from analytics.research_guards.pbo import PBOResult, cscv_pbo
from analytics.research_guards.power import required_sharpe
from analytics.research_guards.psr import probabilistic_sharpe_ratio
from analytics.research_guards.sharpe import ann_sharpe, per_period_sharpe

__all__ = [
    "EULER_MASCHERONI",
    "GATE_DSR",
    "GATE_PBO",
    "BootstrapCI",
    "ClusterStats",
    "HaircutResult",
    "PBOResult",
    "ann_sharpe",
    "block_bootstrap_ci",
    "cluster_bootstrap_ci",
    "cluster_stats",
    "cscv_pbo",
    "deflated_sharpe_ratio",
    "expected_max_sharpe",
    "haircut_sharpe",
    "min_track_record_length",
    "passes_gate",
    "per_period_sharpe",
    "probabilistic_sharpe_ratio",
    "required_sharpe",
    "utc_day_keys",
]

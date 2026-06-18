"""P3 trend×XS combine layer — IDM book-return-space portfolio construction."""

from __future__ import annotations

from analytics.combine.book import CombinedBookResult, combine_books, equity_curve
from analytics.combine.config import CombineConfig
from analytics.combine.idm import causal_idm_series, idm_value, static_idm
from analytics.combine.replay import (
    load_sleeves,
    replay_combined,
    replay_combined_trials,
)
from analytics.combine.report import (
    CombineReport,
    combine_gate_verdict,
    evaluate_combined,
)

__all__ = [
    "CombineConfig",
    "CombineReport",
    "CombinedBookResult",
    "causal_idm_series",
    "combine_books",
    "combine_gate_verdict",
    "equity_curve",
    "evaluate_combined",
    "idm_value",
    "load_sleeves",
    "replay_combined",
    "replay_combined_trials",
    "static_idm",
]

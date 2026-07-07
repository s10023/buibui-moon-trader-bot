"""Daily market brief — pure, read-only, deterministic.

Spec: docs/superpowers/specs/2026-07-04-daily-market-brief-design.md
"""

from analytics.brief.bundle import compute_brief
from analytics.brief.config import FALLBACK_SYMBOLS, BriefConfig, default_symbols
from analytics.brief.render import render_markdown
from analytics.brief.types import BriefBundle, bundle_to_dict

__all__ = [
    "FALLBACK_SYMBOLS",
    "BriefBundle",
    "BriefConfig",
    "bundle_to_dict",
    "compute_brief",
    "default_symbols",
    "render_markdown",
]

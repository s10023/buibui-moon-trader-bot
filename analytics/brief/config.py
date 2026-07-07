"""BriefConfig — frozen configuration for the daily market brief."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FALLBACK_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


@dataclass(frozen=True)
class BriefConfig:
    """All knobs for one brief computation. ``as_of_ms`` drives everything."""

    symbols: tuple[str, ...]
    as_of_ms: int
    stats_days: int = 180
    zone_tfs: tuple[str, ...] = ("4h", "1d")
    max_levels_per_side: int = 4
    max_zones_per_side: int = 2
    recent_call_days: int = 14
    max_recent_calls: int = 10
    ledger_path: Path = Path("docs/plans/pundit-calls.jsonl")
    priors_path: Path = Path("docs/plans/pundit-priors.json")


def default_symbols() -> tuple[tuple[str, ...], list[str]]:
    """coins.json keys (sorted), or FALLBACK_SYMBOLS plus a health note."""
    try:
        from utils.binance_client import load_coins_config

        symbols = tuple(sorted(load_coins_config()))
    except Exception as exc:  # coins.json is gitignored — absence is normal
        return FALLBACK_SYMBOLS, [
            f"coins.json unavailable ({exc}); using fallback symbols"
        ]
    if not symbols:
        return FALLBACK_SYMBOLS, ["coins.json empty; using fallback symbols"]
    return symbols, []

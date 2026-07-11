"""CardConfig — frozen config for `buibui card` (optional [card] TOML block)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CardConfig:
    claude_bin: str = "claude"
    claude_config_dir: str = "~/.claude-personal"
    model: str = "sonnet"
    timeout_s: float = 180.0
    min_rr: float = 1.0
    daily_loss_limit_r: float = -2.0
    valid_hours: float = 12.0
    entry_band_pct: float = 5.0
    fires_lookback_bars: int = 4
    fires_timeframes: tuple[str, ...] = ("1h", "4h", "1d")
    ratings_config: str = "signal_watch"
    sizing_toml: str | None = None
    cards_path: str = "docs/plans/ai-cards.jsonl"
    pundit_calls_path: str = "docs/plans/pundit-calls.jsonl"
    priors_path: str = "docs/plans/pundit-priors.json"
    targets_dir: str = "docs/plans/xsmom_targets"

    @classmethod
    def from_toml(cls, path: str | Path) -> CardConfig:
        """Build from a TOML file's optional `[card]` table.

        Missing block/keys keep dataclass defaults (SizingConfig pattern);
        unknown keys raise so typos never silently no-op.
        """
        with open(Path(path), "rb") as f:
            data: dict[str, Any] = tomllib.load(f)
        block = data.get("card", {})
        if not isinstance(block, dict):
            raise ValueError("[card] must be a TOML table")
        known = {f.name for f in fields(cls)}
        unknown = set(block) - known
        if unknown:
            raise ValueError(f"[card] unknown keys: {sorted(unknown)}")
        kwargs: dict[str, Any] = dict(block)
        if "fires_timeframes" in kwargs:
            kwargs["fires_timeframes"] = tuple(kwargs["fires_timeframes"])
        return cls(**kwargs)

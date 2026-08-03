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
    entry_band_pct: float = 5.0
    fires_lookback_bars: int = 4
    fires_timeframes: tuple[str, ...] = ("1h", "4h", "1d")
    ratings_config: str = "signal_watch"
    # Lookback for the per-fire live record. 0 = all time, which maximises n.
    # Caveat: `outcome_r` only became net of costs on 2026-06-11 (PR #432) and
    # already-resolved rows were never restated, so an all-time window mixes
    # gross and net rows. Round-trip cost is ~0.06R on a 2% stop, so live-vs-
    # backtest gaps under ~0.15R are not readable; larger ones are unaffected.
    #
    # Default is 60 rather than 0 because all-time produced a citation that was
    # true and yet evidentially empty: BTC's card cited inside_bar/1h/short at
    # live_n 136 / +0.036R, which is SMALLER than the ~0.06R contamination that
    # window carries — and the same cell over 30d is −0.325R. A window is the
    # cheap fix; note it is *relative* while the contamination boundary is
    # *absolute*, so 60d still reaches ~a week past 2026-06-11 as of 2026-08-03
    # and self-cleans as time passes. 30d would be clean immediately but thins n
    # below the live_n >= 10 floor the live-beats-backtest rule needs to fire at
    # all. Set 0 to restore all-time, or lower it once the ledger is deeper.
    live_window_days: int = 60
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

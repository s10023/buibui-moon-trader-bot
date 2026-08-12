"""CardConfig — frozen config for `buibui card` (optional [card] TOML block)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

CARD_HORIZONS: tuple[str, ...] = ("intraday", "swing")
"""Card horizons — a strict subset of ``VALID_HORIZONS``.

``unspecified`` is excluded: it scores on 14 days, and a card always knows
its horizon because the operator picks it. A test binds this to
``WINDOWS_MS`` so a member added without a window cannot fall through.
"""

_HORIZON_FIRES_TIMEFRAMES: dict[str, tuple[str, ...]] = {
    "intraday": ("1h", "4h", "1d"),
    # Swing DROPS 1h; never add 1w — no detector runs there, so it would scan
    # an empty population and read as "no fires".
    "swing": ("4h", "1d"),
}


@dataclass(frozen=True)
class CardConfig:
    claude_bin: str = "claude"
    claude_config_dir: str = "~/.claude-personal"
    model: str = "sonnet"
    # Measured 2026-08-04 across six real cards: 271 / 349 / 299 / 291 s wall, mean
    # 4.9 min. The previous 180.0 sat BELOW that floor, so both attempts timed out
    # (`LLMClient.generate` retries once) and every card returned nothing after
    # burning ~6 min. It looked generous only because the /card skill quoted a stale
    # "60-90 s+" measured 2026-07-16 — the doc rotted, and the constant was set from
    # the doc. 480 clears the measured worst case (349 s) with tail headroom.
    #
    # This is a ceiling, not a target: ~97% of generated output tokens never reach
    # the card (23,116 output tokens for a 2,365-char card), and THAT is the real
    # fix. Raising the ceiling stops the failure; it does not make a card fast.
    # Overridable per-run from a [card] TOML block via `--config`.
    timeout_s: float = 480.0
    # Extended-thinking budget for the `claude -p` call, as MAX_THINKING_TOKENS.
    # None leaves the env var unset, i.e. the CLI default — this is deliberately
    # the shipped behaviour so a card's reasoning does not change without the
    # operator asking for it.
    #
    # Measured 2026-08-05 on one real BTCUSDT short prompt, thinking is the
    # ENTIRE latency story and the toolset is not:
    #   baseline                      245.6 s | 21,705 out | 4 turns
    #   + tools off, MCP stripped     254.6 s | 23,645 out | 1 turn
    #   + MAX_THINKING_TOKENS=0        30.7 s |  2,040 out | 8.0x faster
    # The card at 0 stayed good on that sample — 8 substantive reasons and 8/8
    # spot-checked citations exact, no invented numbers. But it is ONE sample and
    # the verdict differed from baseline (NO_TRADE vs TRADE), which this test
    # design cannot separate from ordinary model variance. So: opt-in, and
    # validate on a real batch before anyone changes this default.
    max_thinking_tokens: int | None = None
    # Lock the CLI down to zero tools (+ --strict-mcp-config). Pure determinism
    # and cost win in the same measurement — turns 4 -> 1 and input tokens
    # ~200K -> ~25.7K — with no latency change either way. Default off only
    # because it rides with the thinking change above; enable both together.
    restrict_tools: bool = False
    min_rr: float = 1.0
    daily_loss_limit_r: float = -2.0
    entry_band_pct: float = 5.0
    fires_lookback_bars: int = 4
    # Selects the scoring window the ledger row is resolved against:
    # intraday = 48h, swing = 30d. Was hardcoded "intraday" in ledger.py, so
    # swing-paced cards were scored on the wrong clock and booked wrong.
    horizon: str = "intraday"
    # None => derive from `horizon`; an explicit value wins. Read it through
    # `resolved_fires_timeframes`, never directly.
    fires_timeframes: tuple[str, ...] | None = None
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

    def __post_init__(self) -> None:
        # ValueError, not CardValidationError: that type is for LLM-output
        # schema failures and takes a list. `from_toml` already raises
        # ValueError, so config errors stay one type.
        if self.horizon not in CARD_HORIZONS:
            raise ValueError(
                f"[card] horizon {self.horizon!r} is not one of {list(CARD_HORIZONS)}"
            )

    @property
    def resolved_fires_timeframes(self) -> tuple[str, ...]:
        """Explicit `fires_timeframes`, else the horizon's default."""
        if self.fires_timeframes is not None:
            return self.fires_timeframes
        return _HORIZON_FIRES_TIMEFRAMES[self.horizon]

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

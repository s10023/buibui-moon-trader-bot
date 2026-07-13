"""CardConfig defaults + from_toml ([card] block)."""

from __future__ import annotations

from pathlib import Path

import pytest

from card.config import CardConfig
from card.errors import CardError, CardValidationError


class TestCardConfig:
    def test_defaults(self) -> None:
        cfg = CardConfig()
        assert cfg.claude_bin == "claude"
        assert cfg.claude_config_dir == "~/.claude-personal"
        assert cfg.model == "sonnet"
        assert cfg.timeout_s == 180.0
        assert cfg.min_rr == 1.0
        assert cfg.daily_loss_limit_r == -2.0
        assert cfg.valid_hours == 12.0
        assert cfg.entry_band_pct == 5.0
        assert cfg.fires_lookback_bars == 4
        assert cfg.fires_timeframes == ("1h", "4h", "1d")
        assert cfg.ratings_config == "signal_watch"
        assert cfg.sizing_toml is None
        assert cfg.cards_path == "docs/plans/ai-cards.jsonl"
        assert cfg.pundit_calls_path == "docs/plans/pundit-calls.jsonl"
        assert cfg.priors_path == "docs/plans/pundit-priors.json"
        assert cfg.targets_dir == "docs/plans/xsmom_targets"

    def test_from_toml_overrides(self, tmp_path: Path) -> None:
        toml = tmp_path / "card.toml"
        toml.write_text(
            '[card]\nmodel = "haiku"\nmin_rr = 1.5\nfires_timeframes = ["4h", "1d"]\n'
        )
        cfg = CardConfig.from_toml(toml)
        assert cfg.model == "haiku"
        assert cfg.min_rr == 1.5
        assert cfg.fires_timeframes == ("4h", "1d")
        assert cfg.claude_bin == "claude"  # untouched default

    def test_from_toml_missing_block_is_defaults(self, tmp_path: Path) -> None:
        toml = tmp_path / "empty.toml"
        toml.write_text("[other]\nx = 1\n")
        assert CardConfig.from_toml(toml) == CardConfig()

    def test_from_toml_unknown_key_raises(self, tmp_path: Path) -> None:
        toml = tmp_path / "bad.toml"
        toml.write_text("[card]\nnot_a_field = 1\n")
        with pytest.raises(ValueError, match="unknown"):
            CardConfig.from_toml(toml)

    def test_error_hierarchy(self) -> None:
        err = CardValidationError(["a", "b"])
        assert isinstance(err, CardError)
        assert err.errors == ["a", "b"]

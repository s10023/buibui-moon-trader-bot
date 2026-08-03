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
        assert cfg.entry_band_pct == 5.0
        assert cfg.fires_lookback_bars == 4
        assert cfg.fires_timeframes == ("1h", "4h", "1d")
        assert cfg.ratings_config == "signal_watch"
        assert cfg.live_window_days == 60
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

    def test_live_window_days_default_excludes_most_gross_cost_rows(self) -> None:
        """The live-record window must not default to all-time.

        `outcome_r` only became net of costs on 2026-06-11 (PR #432) and
        resolved rows were never restated, so an all-time window mixes cost
        bases by ~0.06R. That is larger than some cells' entire live edge —
        BTC's card once cited a cell at +0.036R, i.e. a citation that is
        literally true and evidentially empty. A bounded window is the fix;
        0 stays available as an explicit opt-in, not as the default.
        """
        assert CardConfig().live_window_days > 0

    def test_live_window_days_zero_still_selectable(self, tmp_path: Path) -> None:
        """0 must remain reachable — it is the all-time escape hatch."""
        toml = tmp_path / "card.toml"
        toml.write_text("[card]\nlive_window_days = 0\n")
        assert CardConfig.from_toml(toml).live_window_days == 0

    def test_from_toml_missing_block_is_defaults(self, tmp_path: Path) -> None:
        toml = tmp_path / "empty.toml"
        toml.write_text("[other]\nx = 1\n")
        assert CardConfig.from_toml(toml) == CardConfig()

    def test_from_toml_unknown_key_raises(self, tmp_path: Path) -> None:
        toml = tmp_path / "bad.toml"
        toml.write_text("[card]\nnot_a_field = 1\n")
        with pytest.raises(ValueError, match="unknown"):
            CardConfig.from_toml(toml)

    def test_from_toml_non_dict_block_raises(self, tmp_path: Path) -> None:
        # `card` as a scalar (not a table) must fail loudly, never silently
        # fall back to defaults.
        toml = tmp_path / "scalar.toml"
        toml.write_text('card = "not a table"\n')
        with pytest.raises(ValueError, match="must be a TOML table"):
            CardConfig.from_toml(toml)

    def test_error_hierarchy(self) -> None:
        err = CardValidationError(["a", "b"])
        assert isinstance(err, CardError)
        assert err.errors == ["a", "b"]

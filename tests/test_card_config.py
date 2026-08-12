"""CardConfig defaults + from_toml ([card] block)."""

from __future__ import annotations

from pathlib import Path

import pytest

from card.config import CARD_HORIZONS, CardConfig
from card.errors import CardError, CardValidationError


class TestCardConfig:
    def test_defaults(self) -> None:
        cfg = CardConfig()
        assert cfg.claude_bin == "claude"
        assert cfg.claude_config_dir == "~/.claude-personal"
        assert cfg.model == "sonnet"
        assert cfg.timeout_s == 480.0
        assert cfg.max_thinking_tokens is None
        assert cfg.restrict_tools is False
        assert cfg.min_rr == 1.0
        assert cfg.daily_loss_limit_r == -2.0
        assert cfg.entry_band_pct == 5.0
        assert cfg.fires_lookback_bars == 4
        assert cfg.horizon == "intraday"
        assert cfg.fires_timeframes is None  # unset => derived from horizon
        assert cfg.resolved_fires_timeframes == ("1h", "4h", "1d")
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

    def test_swing_drops_1h_and_never_reaches_for_1w(self) -> None:
        """Swing widens the fire scan by DROPPING 1h, not by adding 1w.

        No detector runs on 1w (`analytics/signal_config.py` declares no 1w
        cell), so a 1w citation would scan an empty population and read as
        "no fires" rather than as a missing timeframe — the dead-cell shape.
        """
        cfg = CardConfig(horizon="swing")
        assert cfg.resolved_fires_timeframes == ("4h", "1d")
        assert "1h" not in cfg.resolved_fires_timeframes
        assert "1w" not in cfg.resolved_fires_timeframes

    def test_explicit_fires_timeframes_beats_the_horizon_default(self) -> None:
        cfg = CardConfig(horizon="swing", fires_timeframes=("1h",))
        assert cfg.resolved_fires_timeframes == ("1h",)

    def test_unknown_horizon_raises(self) -> None:
        with pytest.raises(ValueError, match="horizon"):
            CardConfig(horizon="scalp")

    def test_unspecified_is_not_a_card_horizon(self) -> None:
        """`unspecified` is a valid LEDGER horizon but never a card's own.

        It scores on `WINDOWS_MS["unspecified"]` = 14 days, which is neither
        of the two windows a card can mean. A card always knows its horizon
        because the operator picked it, so accepting the key here would only
        ever mis-score.
        """
        with pytest.raises(ValueError, match="horizon"):
            CardConfig(horizon="unspecified")

    def test_card_horizons_are_a_subset_of_the_scorer_enum(self) -> None:
        """Binds CARD_HORIZONS to the scorer, the way test_pundit_horizon does.

        A card horizon with no `WINDOWS_MS` entry would fall through to
        unspecified's 14 days silently — the exact bug `pundit_horizon`
        exists to prevent, re-created one layer up.
        """
        from analytics.pundit_horizon import VALID_HORIZONS
        from tools.pundit_score import WINDOWS_MS

        assert set(CARD_HORIZONS) <= set(VALID_HORIZONS)
        assert set(CARD_HORIZONS) <= set(WINDOWS_MS)

    def test_from_toml_accepts_horizon(self, tmp_path: Path) -> None:
        toml = tmp_path / "card.toml"
        toml.write_text('[card]\nhorizon = "swing"\n')
        cfg = CardConfig.from_toml(toml)
        assert cfg.horizon == "swing"
        assert cfg.resolved_fires_timeframes == ("4h", "1d")

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

    def test_timeout_s_default_clears_measured_generation_time(self) -> None:
        """The default timeout must exceed real card generation, not the doc's guess.

        Six real cards on 2026-08-04 took 271 / 349 / 299 / 291 s. The default was
        180.0, i.e. below the FASTEST of them, so both attempts timed out
        (`LLMClient.generate` retries once) and every card in the batch returned
        nothing while burning ~6 min. Nothing failed loudly: the operator saw empty
        output, not a timeout, and the cause was a stale "60-90 s+" figure in the
        /card skill that the constant had been set from.

        349 s is the measured worst case. Anything at or below it re-arms the
        outage, so this asserts headroom over that observation rather than pinning
        the literal default — a slower model or a longer prompt may justify raising
        it again, but never lowering it back under the evidence.
        """
        assert CardConfig().timeout_s > 349.0

    def test_reasoning_knobs_are_opt_in_at_the_config_layer(self) -> None:
        """The OPERATOR-facing default must stay "unchanged", not just the client's.

        `max_thinking_tokens=0` + `restrict_tools=True` made one measured card
        8.0x faster (245.6 s -> 30.7 s, 21,705 -> 2,040 output tokens) with every
        spot-checked citation still exact — but on n=1, and the verdict moved
        against baseline. Until that is validated on a real batch these stay off.

        This test exists because the equivalent assertion in test_card_client.py
        does NOT cover this: it builds ClaudeCliClient directly, so it reads the
        client's own field default. There are TWO declarations of each knob, and
        flipping this one alone changes real behaviour while that test stays
        green — verified by mutation on 2026-08-05.
        """
        cfg = CardConfig()
        assert cfg.max_thinking_tokens is None
        assert cfg.restrict_tools is False

    def test_reasoning_knobs_settable_from_toml(self, tmp_path: Path) -> None:
        """The opt-in path must actually work — it is the whole point of the knob."""
        toml = tmp_path / "card.toml"
        toml.write_text("[card]\nmax_thinking_tokens = 0\nrestrict_tools = true\n")
        cfg = CardConfig.from_toml(toml)
        assert cfg.max_thinking_tokens == 0
        assert cfg.restrict_tools is True

    def test_timeout_s_overridable_from_toml(self, tmp_path: Path) -> None:
        """`--config` with a [card] block is the no-repo-change escape hatch.

        This is what unblocked the 2026-08-04 batch before the default was fixed,
        so it is load-bearing operator knowledge, not an incidental feature.
        """
        toml = tmp_path / "card.toml"
        toml.write_text("[card]\ntimeout_s = 600.0\n")
        assert CardConfig.from_toml(toml).timeout_s == 600.0

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

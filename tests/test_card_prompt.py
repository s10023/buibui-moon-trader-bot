"""Prompt: byte-stable rubric, state embedding, direction hint."""

from __future__ import annotations

import json
import re
from pathlib import Path

import card.prompt
from card.config import CardConfig
from card.prompt import PROMPT_VERSION, RUBRIC, build_prompt
from card.state import MarketState

# One `# card-vN (<date>, <item>): ...` block per version, newest first.
_CHANGELOG_BLOCK = re.compile(r"^# (card-v\d+) \(", re.MULTILINE)


def _changelog_versions() -> list[str]:
    return _CHANGELOG_BLOCK.findall(Path(card.prompt.__file__).read_text())


def _state(hint: str | None = None) -> MarketState:
    return MarketState(
        symbol="BTCUSDT",
        now_ms=1_760_000_000_000,
        direction_hint=hint,
        panel=None,
        session_clock=None,
        pundit=None,
        xs=None,
        recent_fires=[],
        account=None,
        health=["panel: no data"],
    )


class TestPrompt:
    def test_version_constant(self) -> None:
        assert PROMPT_VERSION == "card-v6"

    def test_rubric_names_the_pundit_metric_and_its_units(self) -> None:
        """card-v4: `avg_atr_r` is in ATR units, and there is no pundit R.

        The card previously received BOTH `avg_r` and `avg_atr_r` on the board
        with rubric 3a explaining an identically-named field that belongs to
        recent_fires, and cited the censored one. The field is now stripped in
        `card/state.py`; this states what the survivor means, so the model
        cannot read an ATR figure as an R figure.
        """
        assert "avg_atr_r" in RUBRIC
        assert "ATR" in RUBRIC
        assert "no per-author R" in RUBRIC

    def test_rubric_live_record_beats_backtest_star(self) -> None:
        """card-v3: the live ledger overrides the backtest star on conflict.

        Without this the model sees live_avg_r in the state JSON with no
        instruction and improvises a weighting.
        """
        assert "live_n" in RUBRIC
        assert "live_avg_r" in RUBRIC
        # the conflict rule must be explicit, not left to inference
        assert "live_avg_r is negative" in RUBRIC

    def test_rubric_external_directions(self) -> None:
        # card-v2: external clusters are mapped liquidity with trust guards
        assert "panel.external" in RUBRIC
        assert "spot_hint_deviation" in RUBRIC
        assert "at most ONE agreeing input" in RUBRIC
        assert "stop-hunt warning" in RUBRIC

    def test_rubric_treats_a_cluster_as_a_band_not_a_level(self) -> None:
        """card-v4: extraction reproduces band EDGES to only ~16% exactly.

        Measured 2026-08-12 over a same-input A/B — mean edge drift is 20-43%
        of band width while intensity agreed 19/19. Without this the model
        places a TP on a cluster edge as though the number were exact.
        """
        assert "BAND, not a level" in RUBRIC
        assert "never place an entry, SL or TP on a cluster edge" in RUBRIC

    def test_rubric_style_block(self) -> None:
        # card-v2: humanizer style directive covers all generated prose
        # card-v5: the steelman is generated prose too, so it joins the scope
        assert "Style (applies to reasoning, steelman, invalidation" in RUBRIC
        assert "No hedge words (might/could/perhaps)" in RUBRIC
        assert "no em dashes" in RUBRIC

    def test_rubric_demands_a_four_angle_steelman(self) -> None:
        """card-v5 (ST35): the one artifact from this author judged on merit.

        Approved 2026-08-17, filed to ride the card-v4 bump, then dropped
        when v4 shipped without it. The four angles are named in the rubric
        because a free-form "consider the other side" is what the model
        already does badly.
        """
        assert "Steelman" in RUBRIC
        assert "HTF counter" in RUBRIC
        assert "underweighted confluence" in RUBRIC
        assert "catalyst risk" in RUBRIC
        assert "the other trader" in RUBRIC
        assert '"steelman"' in RUBRIC

    def test_steelman_is_not_a_veto_machine(self) -> None:
        """The source's own non-goal, and the half that makes it work.

        @TraderMorin, 2026-07-25 (x.com/TraderMorin/status/2080948672439656799):
        "your job is not to convince me out of the trade... it's so the other
        side never surprises you." Without it, a disconfirmation step pointed
        at an already-negative book drifts into a reason to decline, and the
        card is a second opinion rather than a gate.
        """
        assert "not to talk yourself out of the trade" in RUBRIC

    def test_steelman_step_runs_before_the_decision(self) -> None:
        """A counter-case argued after the verdict is a caption, not a step.

        The point of ST35's angle set is that it CHANGES the answer, which
        only holds if the model works through it before choosing a verdict.
        """
        assert RUBRIC.index("4. Steelman") < RUBRIC.index("5. Decision")

    def test_rubric_bans_json_field_paths_in_generated_prose(self) -> None:
        """ST30(c): the bullets read as JSON dumps, and that is the RUBRIC.

        The operator's phone card carried `range_state.pos 0.4955` because
        v4 asked for field-path citations. The number is what keeps the
        model honest, so it stays; the path is what makes it unreadable, so
        it goes. A worked pair rather than a rule alone, because "write
        plainly" did not survive contact with a JSON payload.
        """
        assert "Never write a JSON field path" in RUBRIC
        assert "range_state.pos 0.4955" in RUBRIC
        # the anti-invention requirement must SURVIVE the register change
        assert "citing a concrete number" in RUBRIC

    def test_rubric_prefix_is_byte_stable(self) -> None:
        cfg = CardConfig()
        p1 = build_prompt(_state(), cfg)
        p2 = build_prompt(_state(), cfg)
        assert p1 == p2
        assert p1.startswith(RUBRIC)

    def test_state_json_embedded_sorted(self) -> None:
        state = _state()
        prompt = build_prompt(state, CardConfig())
        assert json.dumps(state.to_dict(), sort_keys=True) in prompt

    def test_direction_hint_included_only_when_set(self) -> None:
        cfg = CardConfig()
        assert "operator is considering" not in build_prompt(_state(), cfg)
        hinted = build_prompt(_state("long"), cfg)
        assert "operator is considering a long" in hinted

    def test_rubric_names_no_absent_indicators(self) -> None:
        # fields that don't exist must never be claimed (spec + addendum)
        assert "RSI" not in RUBRIC
        assert "MACD" not in RUBRIC

    def test_schema_and_hard_rules_inlined(self) -> None:
        assert '"verdict"' in RUBRIC
        assert "ONLY a JSON object" in RUBRIC
        assert "confluence" in RUBRIC.lower()

    def test_rubric_external_trust_guards_pinned(self) -> None:
        # card-v2 delta fragments not covered by the original contract pins
        assert '"book" bands' in RUBRIC
        assert "higher intensity and lower age_hours" in RUBRIC
        assert "spot_hint_deviation true" in RUBRIC

    def test_horizon_block_states_the_scoring_window(self) -> None:
        """The model must be told the window its call is scored against.

        Without this the rubric asks for `expected_hold` "e.g. 6h, 2d" with
        no way to know which is wanted, so the hold is a free choice while
        the scorer's window is not — the mismatch ST12 exists to close.
        """
        intraday = build_prompt(_state(), CardConfig())
        swing = build_prompt(_state(), CardConfig(horizon="swing"))
        assert "48 hours" in intraday
        assert "30 days" in swing
        assert intraday != swing

    def test_swing_block_discounts_short_window_liquidity(self) -> None:
        """A 24h heatmap is not evidence about a 30-day hold.

        The capture set is currently ALL 24h/1d, and `load_external_state`
        does not filter on `window` at all, so a swing card is handed
        intraday liquidity either way. Filtering it out would empty the
        block and read as "no external data" (the dead-cell shape), so the
        honest fix until the capture set carries 1w is to tell the model the
        window and let it discount. `window` already reaches the payload.
        """
        swing = build_prompt(_state(), CardConfig(horizon="swing"))
        assert "window" in swing

    def test_horizon_block_is_appended_not_baked_into_the_rubric(self) -> None:
        """RUBRIC stays byte-stable; horizon rides beside it like the hint.

        Baking it in would make the rubric a function of config, which
        breaks the byte-stability contract the version pin rests on.
        """
        assert "48 hours" not in RUBRIC
        assert "30 days" not in RUBRIC


class TestVersionChangelog:
    """`PROMPT_VERSION` must carry a changelog block, and nothing enforced that.

    card-v6 landed at #702 with no block of its own, so `/card` and `AGENTS.md`
    went on describing v5 for five days while every emitted card said v6.
    `TestPrompt.test_version_constant` pins the version STRING and is blind to
    this by construction — it asserts the constant equals itself, one line above
    the comment convention nothing reads back.
    """

    def test_current_version_has_a_changelog_block(self) -> None:
        versions = _changelog_versions()
        assert PROMPT_VERSION in versions, (
            f"{PROMPT_VERSION} has no `# {PROMPT_VERSION} (<date>, <item>): ...` "
            f"block in card/prompt.py -- found {versions}"
        )

    def test_the_newest_block_is_the_current_version(self) -> None:
        """Blocks run newest-first, so a bump documented lower is a bump misfiled."""
        versions = _changelog_versions()
        assert versions[0] == PROMPT_VERSION, (
            f"the top changelog block is {versions[0]!r} but PROMPT_VERSION is "
            f"{PROMPT_VERSION!r} -- add the new block above the previous one"
        )

    def test_the_check_can_fail(self) -> None:
        """Teeth: the parse must report a blockless version as ABSENT.

        Without this the two assertions above pass equally well against a regex
        that matches anything, which is the same clean-vs-blind gap the lookahead
        harness carries its injected peeking detector for.
        """
        assert "card-v99" not in _changelog_versions()

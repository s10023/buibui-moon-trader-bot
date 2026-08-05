"""Tests for analytics/pundit_direction.py — the ledger direction enum guard."""

from __future__ import annotations

import pytest

from analytics.pundit_direction import VALID_DIRECTIONS, normalize_direction


class TestNormalizeDirection:
    def test_accepts_the_three_scored_values(self) -> None:
        assert normalize_direction("long") == "long"
        assert normalize_direction("short") == "short"
        assert normalize_direction("neutral") == "neutral"

    def test_folds_case_and_strips_whitespace(self) -> None:
        assert normalize_direction("  LONG ") == "long"
        assert normalize_direction("Short") == "short"
        assert normalize_direction("Neutral\n") == "neutral"

    def test_rejects_a_fourth_value_and_names_it(self) -> None:
        with pytest.raises(ValueError, match="range"):
            normalize_direction("range")

    def test_rejects_empty_and_missing(self) -> None:
        # An absent field reaches this as "" and previously scored as a SHORT,
        # exactly like an unknown value — so it must be rejected too, not
        # treated as a benign default.
        with pytest.raises(ValueError):
            normalize_direction("")
        with pytest.raises(ValueError):
            normalize_direction("   ")

    def test_error_names_the_field_so_a_ledger_warning_is_actionable(self) -> None:
        with pytest.raises(ValueError, match="direction"):
            normalize_direction("sideways")

    def test_valid_directions_is_exactly_the_enum_the_scorer_branches_on(self) -> None:
        # pundit_score.score_call special-cases "neutral" and treats everything
        # that is not "long" as a short. If this set ever grows, that branch
        # must be revisited in the same commit.
        assert frozenset({"long", "short", "neutral"}) == VALID_DIRECTIONS

    def test_is_idempotent(self) -> None:
        for value in ("long", "SHORT", " neutral "):
            once = normalize_direction(value)
            assert normalize_direction(once) == once

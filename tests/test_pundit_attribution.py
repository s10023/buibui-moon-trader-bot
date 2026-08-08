"""Tests for analytics/pundit_attribution.py — the ledger attribution guards."""

from __future__ import annotations

import pytest

from analytics.pundit_attribution import (
    CONFIDENCE_RANK,
    VALID_ATTRIBUTION_CONFIDENCES,
    VALID_ATTRIBUTIONS,
    meets_confidence,
    normalize_attribution,
    normalize_attribution_confidence,
)


class TestNormalizeAttribution:
    def test_accepts_both_members(self) -> None:
        assert normalize_attribution("first-hand") == "first-hand"
        assert normalize_attribution("relay") == "relay"

    def test_folds_case_and_strips_whitespace(self) -> None:
        assert normalize_attribution("  RELAY ") == "relay"
        assert normalize_attribution("First-Hand\n") == "first-hand"

    def test_absence_defaults_to_first_hand(self) -> None:
        # 170 of the 203 live rows predate the feature and came from the
        # first-hand ingest passes. Defaulting to "relay" would mark every one
        # of them untrusted.
        assert normalize_attribution(None) == "first-hand"
        assert normalize_attribution("") == "first-hand"
        assert normalize_attribution("   ") == "first-hand"

    def test_relayed_by_overrides_an_absent_attribution(self) -> None:
        # THE fail-safe. A writer who omits `attribution` on a genuine relay
        # row would otherwise buy full trust with silence. Naming a relaying
        # channel is positive evidence; an absent field is not.
        assert normalize_attribution(None, relayed_by="@KoluniteVIP") == "relay"
        assert normalize_attribution("", relayed_by="@KoluniteVIP") == "relay"

    def test_blank_relayed_by_does_not_trigger_the_override(self) -> None:
        assert normalize_attribution("", relayed_by="") == "first-hand"
        assert normalize_attribution("", relayed_by="   ") == "first-hand"
        assert normalize_attribution("", relayed_by=None) == "first-hand"

    def test_an_explicit_first_hand_still_wins_over_a_stray_relayed_by(self) -> None:
        # The override covers SILENCE, not contradiction. A writer who states
        # "first-hand" has made an assertion, and the guard is not the place to
        # overrule it — that would hide a real data defect behind a repair.
        assert (
            normalize_attribution("first-hand", relayed_by="@someone") == "first-hand"
        )

    def test_rejects_a_present_but_unrecognised_value_and_names_it(self) -> None:
        with pytest.raises(ValueError, match="secondhand"):
            normalize_attribution("secondhand")

    def test_rejects_plausible_near_misses(self) -> None:
        for near_miss in ("firsthand", "first hand", "relayed", "quote", "retweet"):
            with pytest.raises(ValueError):
                normalize_attribution(near_miss)


class TestNormalizeAttributionConfidence:
    def test_accepts_the_roster_vocabulary(self) -> None:
        assert normalize_attribution_confidence("high", attribution="relay") == "high"
        assert (
            normalize_attribution_confidence("operator", attribution="relay")
            == "operator"
        )

    def test_folds_case_and_strips_whitespace(self) -> None:
        assert normalize_attribution_confidence(" HIGH ", attribution="relay") == "high"

    def test_missing_on_a_relay_row_does_not_raise(self) -> None:
        # The pundit_horizon precedent: a row the scorer refuses is skipped
        # PERMANENTLY, which is worse than a row scored and flagged. Unknown
        # ranks lowest and the filter catches it.
        assert normalize_attribution_confidence(None, attribution="relay") == ""
        assert normalize_attribution_confidence("", attribution="relay") == ""

    def test_rejects_a_present_but_unrecognised_value(self) -> None:
        with pytest.raises(ValueError, match="verified"):
            normalize_attribution_confidence("verified", attribution="relay")

    def test_rejects_out_of_enum_even_on_a_first_hand_row(self) -> None:
        # Validation runs BEFORE the first-hand discard, so a typo is still a
        # defect on a row where the field is meaningless. Ordering the other
        # way would make the guard silently unreachable for first-hand rows.
        with pytest.raises(ValueError):
            normalize_attribution_confidence("verified", attribution="first-hand")

    def test_discards_a_meaningful_value_on_a_first_hand_row(self) -> None:
        # No roster mapping was consulted, so the field carries no information.
        assert normalize_attribution_confidence("high", attribution="first-hand") == ""


class TestMeetsConfidence:
    def test_any_floor_passes_everything(self) -> None:
        # The shipped default: no published number moves on this commit alone.
        for conf in ("", "operator", "high"):
            assert meets_confidence("relay", conf, "any") is True

    def test_first_hand_always_passes_at_every_floor(self) -> None:
        # First-hand rows carry no roster assertion, so filtering them on a
        # roster-confidence field would drop exactly the rows it was never
        # about. This is why it is not a plain rank comparison.
        for floor in ("any", "operator", "high"):
            assert meets_confidence("first-hand", "", floor) is True

    def test_operator_floor_drops_only_unknown_confidence(self) -> None:
        assert meets_confidence("relay", "", "operator") is False
        assert meets_confidence("relay", "operator", "operator") is True
        assert meets_confidence("relay", "high", "operator") is True

    def test_high_floor_drops_operator_and_unknown(self) -> None:
        # The live case: all 22 relay rows sit at "operator", so this floor
        # empties the relay half of the ledger entirely. That is the intended
        # meaning of "revocable", not a bug.
        assert meets_confidence("relay", "", "high") is False
        assert meets_confidence("relay", "operator", "high") is False
        assert meets_confidence("relay", "high", "high") is True

    def test_unknown_ranks_below_operator(self) -> None:
        assert CONFIDENCE_RANK[""] < CONFIDENCE_RANK["operator"]
        assert CONFIDENCE_RANK["operator"] < CONFIDENCE_RANK["high"]

    def test_rank_table_covers_the_whole_enum(self) -> None:
        # A value legal in the enum but missing from the rank table would
        # silently rank 0 and be filtered out at every floor.
        assert set(CONFIDENCE_RANK) >= VALID_ATTRIBUTION_CONFIDENCES

    def test_enum_matches_the_roster_vocabulary(self) -> None:
        assert {"first-hand", "relay"} == VALID_ATTRIBUTIONS
        assert {"high", "operator"} == VALID_ATTRIBUTION_CONFIDENCES

"""Tests for analytics/pundit_horizon.py — the ledger horizon enum guard."""

from __future__ import annotations

import pytest

from analytics.pundit_horizon import VALID_HORIZONS, normalize_horizon


class TestNormalizeHorizon:
    def test_accepts_the_three_pre_committed_windows(self) -> None:
        assert normalize_horizon("intraday") == "intraday"
        assert normalize_horizon("swing") == "swing"
        assert normalize_horizon("unspecified") == "unspecified"

    def test_folds_case_and_strips_whitespace(self) -> None:
        assert normalize_horizon("  INTRADAY ") == "intraday"
        assert normalize_horizon("Swing") == "swing"
        assert normalize_horizon("Unspecified\n") == "unspecified"

    def test_absence_is_legitimate_and_becomes_unspecified(self) -> None:
        # THE shape difference from `direction`, which rejects absence. A call
        # with no stated horizon is a real, scoreable call — "unspecified" is a
        # member of the enum, not a fallback for it. Rejecting absence here
        # would drop 4 of the 174 live rows for being honestly silent.
        assert normalize_horizon(None) == "unspecified"
        assert normalize_horizon("") == "unspecified"
        assert normalize_horizon("   ") == "unspecified"

    def test_rejects_a_present_but_unrecognised_value_and_names_it(self) -> None:
        # This is the only case the guard exists for: the writer said
        # something, and it was not a window the scorer knows.
        with pytest.raises(ValueError, match="scalp"):
            normalize_horizon("scalp")

    def test_rejects_a_plausible_near_miss(self) -> None:
        # The realistic typo is not gibberish — it is a word that reads like a
        # horizon and silently bought the 14-day window.
        for near_miss in ("daily", "1h", "short-term", "position", "intra-day"):
            with pytest.raises(ValueError):
                normalize_horizon(near_miss)

    def test_rejects_a_non_string_value(self) -> None:
        with pytest.raises(ValueError):
            normalize_horizon(5)  # type: ignore[arg-type]

    def test_error_names_the_field_so_a_ledger_warning_is_actionable(self) -> None:
        with pytest.raises(ValueError, match="horizon"):
            normalize_horizon("scalp")

    def test_is_idempotent(self) -> None:
        for value in ("intraday", "SWING", " unspecified ", None):
            once = normalize_horizon(value)
            assert normalize_horizon(once) == once


class TestEnumCoversTheWindowTable:
    def test_valid_horizons_is_exactly_the_windows_ms_keys(self) -> None:
        # The drift this guard cannot catch on its own: adding a member here
        # without a window in `pundit_score.WINDOWS_MS` re-creates the exact
        # bug — `window_ms` falls back to unspecified's 14 days and scores the
        # new horizon under the wrong window, silently. Imported lazily so this
        # module stays IO-free and the test still binds the two together.
        from tools.pundit_score import WINDOWS_MS

        assert frozenset(WINDOWS_MS) == VALID_HORIZONS

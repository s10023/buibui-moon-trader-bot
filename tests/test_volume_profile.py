"""Tests for analytics/volume_profile.py (pure volume-at-price math)."""

from __future__ import annotations

import pandas as pd

from analytics.volume_profile import VolumeProfile, build_profile, value_area


def _bar(open_time: int, low: float, high: float, volume: float) -> dict[str, object]:
    return {
        "open_time": open_time,
        "open": low,
        "high": high,
        "low": low,
        "close": high,
        "volume": volume,
    }


class TestBuildProfile:
    def test_single_bar_spreads_volume_uniformly(self) -> None:
        # One bar spanning [100, 110] with volume 100 into 10 bins of width 1
        # -> every bin gets 10.
        df = pd.DataFrame([_bar(0, 100.0, 110.0, 100.0)])
        prof = build_profile(df, n_bins=10)
        assert prof is not None
        assert len(prof.bin_edges) == 11
        assert prof.bin_edges[0] == 100.0
        assert prof.bin_edges[-1] == 110.0
        for v in prof.volumes:
            assert abs(v - 10.0) < 1e-9

    def test_partial_overlap_is_proportional(self) -> None:
        # Bins over [100, 110] (10 bins). Second bar spans [100, 102] with
        # volume 50 -> 25 into bin 0 and 25 into bin 1.
        df = pd.DataFrame([_bar(0, 100.0, 110.0, 0.0), _bar(1, 100.0, 102.0, 50.0)])
        prof = build_profile(df, n_bins=10)
        assert prof is not None
        assert abs(prof.volumes[0] - 25.0) < 1e-9
        assert abs(prof.volumes[1] - 25.0) < 1e-9
        assert abs(sum(prof.volumes) - 50.0) < 1e-9

    def test_zero_width_bar_lands_in_containing_bin(self) -> None:
        # high == low: all volume into the single bin containing that price.
        df = pd.DataFrame([_bar(0, 100.0, 110.0, 0.0), _bar(1, 104.5, 104.5, 30.0)])
        prof = build_profile(df, n_bins=10)
        assert prof is not None
        assert abs(prof.volumes[4] - 30.0) < 1e-9

    def test_zero_width_bar_at_top_edge_lands_in_last_bin(self) -> None:
        df = pd.DataFrame([_bar(0, 100.0, 110.0, 0.0), _bar(1, 110.0, 110.0, 5.0)])
        prof = build_profile(df, n_bins=10)
        assert prof is not None
        assert abs(prof.volumes[9] - 5.0) < 1e-9

    def test_empty_frame_returns_none(self) -> None:
        df = pd.DataFrame(
            columns=["open_time", "open", "high", "low", "close", "volume"]
        )
        assert build_profile(df) is None

    def test_zero_span_returns_none(self) -> None:
        df = pd.DataFrame([_bar(0, 100.0, 100.0, 10.0)])
        assert build_profile(df) is None

    def test_zero_total_volume_returns_none(self) -> None:
        df = pd.DataFrame([_bar(0, 100.0, 110.0, 0.0)])
        assert build_profile(df) is None


class TestValueArea:
    def test_known_poc_and_va(self) -> None:
        # 5 bins [0,5), volumes [10, 20, 40, 20, 10] (total 100).
        # POC = bin 2 (center 2.5). VA at 70%: start 40, add larger
        # neighbor (tie 20/20 -> prefer UPPER bin 3) -> 60, then bin 1 -> 80
        # >= 70 -> VA bins {1,2,3}: val = 1.0, vah = 4.0.
        prof = VolumeProfile(
            bin_edges=(0.0, 1.0, 2.0, 3.0, 4.0, 5.0),
            volumes=(10.0, 20.0, 40.0, 20.0, 10.0),
        )
        poc, vah, val = value_area(prof, pct=0.70)
        assert poc == 2.5
        assert vah == 4.0
        assert val == 1.0

    def test_poc_tie_prefers_lowest_price_bin(self) -> None:
        prof = VolumeProfile(
            bin_edges=(0.0, 1.0, 2.0, 3.0),
            volumes=(30.0, 10.0, 30.0),
        )
        poc, _, _ = value_area(prof, pct=0.5)
        assert poc == 0.5  # lowest-price max bin wins

    def test_full_pct_covers_all_bins(self) -> None:
        prof = VolumeProfile(
            bin_edges=(0.0, 1.0, 2.0, 3.0),
            volumes=(10.0, 10.0, 10.0),
        )
        _, vah, val = value_area(prof, pct=1.0)
        assert vah == 3.0
        assert val == 0.0

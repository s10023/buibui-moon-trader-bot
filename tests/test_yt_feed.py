"""Tests for tools/yt_feed.py — strict TDD, no real network calls."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tools.yt_feed import (
    ChannelConfig,
    estimate_tokens,
    load_feed_config,
    parse_iso8601_duration,
    title_excluded,
    uploads_playlist_id,
)


def make_channel(**overrides: Any) -> ChannelConfig:
    base: dict[str, Any] = {
        "id": "UCabcdefghijklmnopqrstu",
        "name": "Test Channel",
        "title_include": (),
        "title_exclude": (),
        "min_duration_s": 180,
        "lang": "en",
    }
    base.update(overrides)
    return ChannelConfig(**base)


class TestPureHelpers:
    def test_uploads_playlist_id_swaps_prefix(self) -> None:
        assert uploads_playlist_id("UCabc123") == "UUabc123"

    def test_parse_iso8601_duration(self) -> None:
        assert parse_iso8601_duration("PT21M") == 1260
        assert parse_iso8601_duration("PT1H2M3S") == 3723
        assert parse_iso8601_duration("P1DT2H") == 93600
        assert parse_iso8601_duration("PT45S") == 45
        assert parse_iso8601_duration("P0D") == 0  # live stream placeholder

    def test_parse_iso8601_duration_garbage_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_iso8601_duration("banana")

    def test_estimate_tokens_matches_spec_example(self) -> None:
        # spec §9: 1260 s → 1260×4 + 15×1400 + 8000 = 34040
        assert estimate_tokens(1260) == 34040

    def test_title_exclude_wins_over_include(self) -> None:
        ch = make_channel(title_include=("bitcoin",), title_exclude=("#shorts",))
        assert title_excluded("Bitcoin update #SHORTS", ch) is True

    def test_title_include_empty_keeps_all(self) -> None:
        assert title_excluded("anything", make_channel()) is False

    def test_title_include_nonmatch_excludes(self) -> None:
        ch = make_channel(title_include=("bitcoin",))
        assert title_excluded("Ethereum update", ch) is True
        assert title_excluded("BITCOIN weekly", ch) is False


class TestLoadFeedConfig:
    def _write(self, tmp_path: Path, body: str) -> Path:
        p = tmp_path / "youtube_channels.toml"
        p.write_text(body, encoding="utf-8")
        return p

    def test_full_config(self, tmp_path: Path) -> None:
        cfg = load_feed_config(
            self._write(
                tmp_path,
                "[feed]\ncold_start_days = 7\n\n[[channel]]\n"
                'id = "UCabcdefghijklmnopqrstu"\nname = "Cowen"\n'
                'title_include = ["btc"]\ntitle_exclude = ["#shorts"]\n'
                'min_duration_s = 240\nlang = "en"\n',
            )
        )
        assert cfg.cold_start_days == 7
        ch = cfg.channels[0]
        assert ch.name == "Cowen"
        assert ch.title_include == ("btc",)
        assert ch.min_duration_s == 240

    def test_defaults_applied(self, tmp_path: Path) -> None:
        cfg = load_feed_config(
            self._write(tmp_path, '[[channel]]\nid = "UCabcdefghijklmnopqrstu"\n')
        )
        assert cfg.cold_start_days == 14
        ch = cfg.channels[0]
        assert ch.min_duration_s == 180
        assert ch.title_include == ()
        assert ch.lang == ""
        assert ch.name == "UCabcdefghijklmnopqrstu"

    def test_missing_file_aborts(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            load_feed_config(tmp_path / "nope.toml")

    def test_non_uc_id_aborts(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            load_feed_config(self._write(tmp_path, '[[channel]]\nid = "abc"\n'))

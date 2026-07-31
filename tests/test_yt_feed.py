"""Tests for tools/yt_feed.py — strict TDD, no real network calls."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tools.yt_feed import (
    ChannelConfig,
    estimate_tokens,
    floor_for,
    load_feed_config,
    load_state,
    parse_iso8601_duration,
    save_state,
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


class TestState:
    def test_missing_file_returns_fresh(self, tmp_path: Path) -> None:
        state = load_state(tmp_path / "yt-feed-state.json")
        assert state == {"version": 1, "channels": {}, "videos": {}}

    def test_malformed_json_aborts(self, tmp_path: Path) -> None:
        p = tmp_path / "s.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(SystemExit, match="refusing"):
            load_state(p)

    def test_wrong_version_aborts(self, tmp_path: Path) -> None:
        p = tmp_path / "s.json"
        p.write_text(
            json.dumps({"version": 99, "channels": {}, "videos": {}}), encoding="utf-8"
        )
        with pytest.raises(SystemExit):
            load_state(p)

    def test_save_roundtrip_atomic(self, tmp_path: Path) -> None:
        p = tmp_path / "sub" / "s.json"
        state = load_state(p)
        state["videos"]["aaaaaaaaaaa"] = {"status": "ingested"}
        save_state(p, state)
        assert load_state(p)["videos"]["aaaaaaaaaaa"]["status"] == "ingested"
        assert not p.with_suffix(".json.tmp").exists()

    def test_floor_prefers_persisted_entry(self, tmp_path: Path) -> None:
        now = datetime(2026, 7, 31, 9, 0, tzinfo=UTC)
        state = {
            "version": 1,
            "videos": {},
            "channels": {
                "UCx": {
                    "added_ts_utc": "2026-07-01T00:00:00+00:00",
                    "floor_ts_utc": "2026-06-17T00:00:00+00:00",
                }
            },
        }
        assert floor_for("UCx", state, now, 14) == datetime(2026, 6, 17, tzinfo=UTC)

    def test_floor_computed_for_unknown_channel(self) -> None:
        now = datetime(2026, 7, 31, 9, 0, tzinfo=UTC)
        state = {"version": 1, "videos": {}, "channels": {}}
        assert floor_for("UCy", state, now, 14) == now - timedelta(days=14)

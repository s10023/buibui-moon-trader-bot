"""Tests for tools/yt_feed.py — strict TDD, no real network calls."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tools.yt_feed import (
    ChannelConfig,
    _resolve_durations,
    estimate_tokens,
    floor_for,
    load_feed_config,
    load_state,
    parse_iso8601_duration,
    poll_channel,
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


class FakeResp:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self.text = json.dumps(payload)


class FakeGet:
    """Routes by endpoint suffix; records every (url, params) for shape assertions."""

    def __init__(self, routes: dict[str, list[FakeResp]]) -> None:
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, *, params: dict[str, str]) -> FakeResp:
        self.calls.append((url, dict(params)))
        endpoint = url.rsplit("/", 1)[-1]
        queue = self.routes.get(endpoint)
        if not queue:
            raise AssertionError(f"unexpected call to {endpoint}")
        return queue.pop(0)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
FRESH_STATE: dict[str, Any] = {"version": 1, "channels": {}, "videos": {}}


def playlist_item(vid: str, title: str, pub: str) -> dict[str, Any]:
    return {
        "snippet": {"title": title, "resourceId": {"videoId": vid}},
        "contentDetails": {"videoId": vid, "videoPublishedAt": pub},
    }


def video_item(vid: str, duration: str, live: str = "none") -> dict[str, Any]:
    return {
        "id": vid,
        "snippet": {"liveBroadcastContent": live},
        "contentDetails": {"duration": duration},
    }


class TestPollChannel:
    def _channel(self) -> ChannelConfig:
        return make_channel(id="UCabcdefghijklmnopqrstu", title_exclude=("#shorts",))

    def test_request_shape(self) -> None:
        get = FakeGet(
            {
                "playlistItems": [FakeResp(200, {"items": []})],
            }
        )
        poll_channel(
            self._channel(),
            dict(FRESH_STATE),
            now=NOW,
            get=get,
            api_key="K",
            cold_start_days=14,
        )
        url, params = get.calls[0]
        assert url == "https://www.googleapis.com/youtube/v3/playlistItems"
        assert params["part"] == "snippet,contentDetails"
        assert params["playlistId"] == "UUabcdefghijklmnopqrstu"
        assert params["maxResults"] == "50"
        assert params["key"] == "K"

    def test_messy_page_exclusions_and_one_candidate(self) -> None:
        state: dict[str, Any] = {
            "version": 1,
            "channels": {},
            "videos": {"bbbbbbbbbbb": {"status": "ingested"}},
        }
        page = {
            "items": [
                playlist_item("aaaaaaaaaaa", "Deleted video", "2026-07-31T01:00:00Z"),
                playlist_item("bbbbbbbbbbb", "already done", "2026-07-30T01:00:00Z"),
                playlist_item("ccccccccccc", "old news", "2026-06-01T01:00:00Z"),
                playlist_item("ddddddddddd", "clip #shorts", "2026-07-30T02:00:00Z"),
                playlist_item("eeeeeeeeeee", "a short one", "2026-07-30T03:00:00Z"),
                playlist_item("fffffffffff", "premiere soon", "2026-07-30T04:00:00Z"),
                playlist_item(
                    "ggggggggggg", "BTC weekly outlook", "2026-07-31T02:00:00Z"
                ),
            ]
        }
        videos = {
            "items": [
                video_item("eeeeeeeeeee", "PT45S"),
                video_item("fffffffffff", "P0D", live="upcoming"),
                video_item("ggggggggggg", "PT21M"),
            ]
        }
        get = FakeGet(
            {"playlistItems": [FakeResp(200, page)], "videos": [FakeResp(200, videos)]}
        )
        result = poll_channel(
            self._channel(), state, now=NOW, get=get, api_key="K", cold_start_days=14
        )
        assert result.excluded == {
            "below_floor": 1,
            "ledgered": 1,
            "title_filtered": 1,
            "too_short": 1,
            "live_or_upcoming": 1,
            "unavailable": 1,
        }
        assert [c.video_id for c in result.candidates] == ["ggggggggggg"]
        cand = result.candidates[0]
        assert cand.duration_s == 1260
        assert cand.est_tokens == 34040
        assert cand.url == "https://www.youtube.com/watch?v=ggggggggggg"
        assert cand.age_h == 10.0
        _, vparams = get.calls[1]
        assert vparams["part"] == "contentDetails,snippet"
        assert "eeeeeeeeeee" in vparams["id"]

    def test_uu_derivation_404_falls_back_to_channels_list(self) -> None:
        err = {"error": {"code": 404, "errors": [{"reason": "playlistNotFound"}]}}
        real_uploads = {
            "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUother"}}}]
        }
        get = FakeGet(
            {
                "playlistItems": [FakeResp(404, err), FakeResp(200, {"items": []})],
                "channels": [FakeResp(200, real_uploads)],
            }
        )
        result = poll_channel(
            self._channel(),
            dict(FRESH_STATE),
            now=NOW,
            get=get,
            api_key="K",
            cold_start_days=14,
        )
        assert result.errors == []
        assert get.calls[2][1]["playlistId"] == "UUother"

    def test_quota_403_reported_not_raised(self) -> None:
        err = {"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}]}}
        get = FakeGet({"playlistItems": [FakeResp(403, err)]})
        result = poll_channel(
            self._channel(),
            dict(FRESH_STATE),
            now=NOW,
            get=get,
            api_key="K",
            cold_start_days=14,
        )
        assert result.candidates == []
        assert any("quotaExceeded" in e for e in result.errors)

    def test_duration_batching_chunks_at_50(self) -> None:
        survivors: list[dict[str, Any]] = [
            {
                "video_id": f"v{i:010d}",
                "title": f"t{i}",
                "publish": NOW - timedelta(hours=2),
            }
            for i in range(60)
        ]
        videos_pages = [
            FakeResp(
                200,
                {"items": [video_item(s["video_id"], "PT10M") for s in survivors[:50]]},
            ),
            FakeResp(
                200,
                {"items": [video_item(s["video_id"], "PT10M") for s in survivors[50:]]},
            ),
        ]
        get = FakeGet({"videos": videos_pages})
        excluded = dict.fromkeys(
            (
                "below_floor",
                "ledgered",
                "title_filtered",
                "too_short",
                "live_or_upcoming",
                "unavailable",
            ),
            0,
        )
        cands = _resolve_durations(
            get, "K", survivors, make_channel(min_duration_s=60), NOW, excluded
        )
        assert len(cands) == 60
        assert len(get.calls) == 2
        assert len(get.calls[0][1]["id"].split(",")) == 50

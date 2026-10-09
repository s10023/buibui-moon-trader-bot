"""Tests for tools/yt_feed.py — strict TDD, no real network calls."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tools.video_marks import ITEM_CAP
from tools.yt_feed import (
    ChannelConfig,
    FeedConfig,
    _resolve_durations,
    _results_to_dict,
    backfill_channel,
    backfill_playlist,
    channel_hint,
    estimate_tokens,
    floor_for,
    is_intro_recap,
    list_channel_playlists,
    load_feed_config,
    load_state,
    main,
    parse_iso8601_duration,
    poll_channel,
    resolve_handle,
    run_mark,
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

    def test_paused_defaults_false_and_is_read_from_the_channel_block(
        self, tmp_path: Path
    ) -> None:
        default = load_feed_config(
            self._write(tmp_path, '[[channel]]\nid = "UCabcdefghijklmnopqrstu"\n')
        )
        assert default.channels[0].paused is False
        explicit = load_feed_config(
            self._write(
                tmp_path,
                '[[channel]]\nid = "UCabcdefghijklmnopqrstu"\npaused = true\n',
            )
        )
        assert explicit.channels[0].paused is True

    def test_item_cap_defaults_to_the_global_constant(self, tmp_path: Path) -> None:
        cfg = load_feed_config(
            self._write(tmp_path, '[[channel]]\nid = "UCabcdefghijklmnopqrstu"\n')
        )
        assert cfg.channels[0].item_cap == ITEM_CAP

    def test_item_cap_is_read_from_the_channel_block(self, tmp_path: Path) -> None:
        cfg = load_feed_config(
            self._write(
                tmp_path,
                '[[channel]]\nid = "UCabcdefghijklmnopqrstu"\nitem_cap = 12\n',
            )
        )
        assert cfg.channels[0].item_cap == 12

    def test_item_cap_is_an_int_not_a_tuple(self, tmp_path: Path) -> None:
        """The plan's literal loader snippet was `(int(...),)` — a 1-tuple. mypy
        catches it, but only if someone runs mypy; this fails loudly either way."""
        cfg = load_feed_config(
            self._write(
                tmp_path,
                '[[channel]]\nid = "UCabcdefghijklmnopqrstu"\nitem_cap = 12\n',
            )
        )
        assert isinstance(cfg.channels[0].item_cap, int)

    def test_missing_file_aborts(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            load_feed_config(tmp_path / "nope.toml")

    def test_non_uc_id_aborts(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            load_feed_config(self._write(tmp_path, '[[channel]]\nid = "abc"\n'))


class TestState:
    def test_missing_file_returns_fresh(self, tmp_path: Path) -> None:
        state = load_state(tmp_path / "yt-feed-state.json")
        assert state == {"version": 1, "channels": {}, "videos": {}, "playlists": {}}

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

    def _since_page(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Two candidates straddling a --since of 2026-07-30, both above the
        cold-start floor (NOW-14d = 2026-07-17) so only --since can separate
        them."""
        page = {
            "items": [
                playlist_item("oldoldold00", "before since", "2026-07-29T01:00:00Z"),
                playlist_item("newnewnew00", "after since", "2026-07-30T03:00:00Z"),
            ]
        }
        videos = {
            "items": [
                video_item("oldoldold00", "PT21M"),
                video_item("newnewnew00", "PT21M"),
            ]
        }
        return page, videos

    def test_poll_since_narrows_the_floor(self) -> None:
        page, videos = self._since_page()
        get = FakeGet(
            {"playlistItems": [FakeResp(200, page)], "videos": [FakeResp(200, videos)]}
        )
        result = poll_channel(
            self._channel(),
            dict(FRESH_STATE),
            now=NOW,
            get=get,
            api_key="K",
            cold_start_days=14,
            since=datetime(2026, 7, 30, tzinfo=UTC),
        )
        assert [c.video_id for c in result.candidates] == ["newnewnew00"]
        assert result.excluded["below_floor"] == 1
        assert result.floor_ts_utc == "2026-07-30T00:00:00+00:00"

    def test_poll_since_cannot_widen_past_the_persisted_floor(self) -> None:
        """A --since EARLIER than the floor must not resurface declined videos.

        Discriminates against `floor = since or floor_for(...)`: under that
        form the 07-29 video is admitted and this fails. The 07-29 item sits
        above the requested 2026-06-01 but below the persisted 07-30 floor,
        so only the max() keeps it excluded.
        """
        page, videos = self._since_page()
        state: dict[str, Any] = {
            "version": 1,
            "channels": {
                "UCabcdefghijklmnopqrstu": {"floor_ts_utc": "2026-07-30T00:00:00+00:00"}
            },
            "videos": {},
        }
        get = FakeGet(
            {"playlistItems": [FakeResp(200, page)], "videos": [FakeResp(200, videos)]}
        )
        result = poll_channel(
            self._channel(),
            state,
            now=NOW,
            get=get,
            api_key="K",
            cold_start_days=14,
            since=datetime(2026, 6, 1, tzinfo=UTC),
        )
        assert [c.video_id for c in result.candidates] == ["newnewnew00"]
        assert result.excluded["below_floor"] == 1
        assert result.floor_ts_utc == "2026-07-30T00:00:00+00:00"

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


class TestBackfillChannel:
    def _pages(self) -> dict[str, list[FakeResp]]:
        page1 = {
            "items": [playlist_item("aaaaaaaaaa1", "recent", "2026-07-30T00:00:00Z")],
            "nextPageToken": "P2",
        }
        page2 = {
            "items": [playlist_item("aaaaaaaaaa2", "ancient", "2025-01-15T00:00:00Z")],
        }
        videos = {
            "items": [
                video_item("aaaaaaaaaa1", "PT30M"),
                video_item("aaaaaaaaaa2", "PT30M"),
            ]
        }
        return {
            "playlistItems": [FakeResp(200, page1), FakeResp(200, page2)],
            "videos": [FakeResp(200, videos)],
        }

    def test_pagination_ignores_floor_and_respects_ledger(self) -> None:
        state: dict[str, Any] = {
            "version": 1,
            "channels": {
                "UCabcdefghijklmnopqrstu": {
                    "added_ts_utc": "2026-07-31T00:00:00+00:00",
                    "floor_ts_utc": "2026-07-17T00:00:00+00:00",
                }
            },
            "videos": {"aaaaaaaaaa1": {"status": "ingested"}},
        }
        get = FakeGet(self._pages())
        result = backfill_channel(
            make_channel(),
            state,
            now=NOW,
            get=get,
            api_key="K",
            since=None,
            max_videos=200,
        )
        # the 2025 video is WAY below the poll floor but IS a backfill candidate
        assert [c.video_id for c in result.candidates] == ["aaaaaaaaaa2"]
        assert result.excluded["ledgered"] == 1
        assert result.floor_ts_utc == ""
        # second page requested with the pageToken
        assert get.calls[1][1]["pageToken"] == "P2"

    def test_since_stops_paging_and_excludes_older(self) -> None:
        get = FakeGet(self._pages())
        since = datetime(2026, 1, 1, tzinfo=UTC)
        result = backfill_channel(
            make_channel(),
            {"version": 1, "channels": {}, "videos": {}},
            now=NOW,
            get=get,
            api_key="K",
            since=since,
            max_videos=200,
        )
        assert [c.video_id for c in result.candidates] == ["aaaaaaaaaa1"]
        assert result.excluded["below_floor"] == 1  # "older than --since" bucket
        # page1's last item (2026-07-30) is newer than since, so page2 WAS fetched;
        # its item then landed below since. Now verify early-stop: with since after
        # page1's last item, page2 must never be fetched.
        get2 = FakeGet(self._pages())
        result2 = backfill_channel(
            make_channel(),
            {"version": 1, "channels": {}, "videos": {}},
            now=NOW,
            get=get2,
            api_key="K",
            since=datetime(2026, 7, 31, tzinfo=UTC),
            max_videos=200,
        )
        assert result2.candidates == []
        playlist_calls = [c for c in get2.calls if c[0].endswith("playlistItems")]
        assert len(playlist_calls) == 1

    def test_max_videos_bounds_examined_entries(self) -> None:
        get = FakeGet(self._pages())
        result = backfill_channel(
            make_channel(),
            {"version": 1, "channels": {}, "videos": {}},
            now=NOW,
            get=get,
            api_key="K",
            since=None,
            max_videos=1,
        )
        assert len(result.candidates) == 1
        playlist_calls = [c for c in get.calls if c[0].endswith("playlistItems")]
        assert len(playlist_calls) == 1

    def test_empty_items_page_stops_pagination(self) -> None:
        # a page with a nextPageToken but zero items makes no progress; must
        # not loop forever chasing the token
        page1 = {"items": [], "nextPageToken": "P2"}
        get = FakeGet({"playlistItems": [FakeResp(200, page1)]})
        result = backfill_channel(
            make_channel(),
            {"version": 1, "channels": {}, "videos": {}},
            now=NOW,
            get=get,
            api_key="K",
            since=None,
            max_videos=200,
        )
        assert result.candidates == []
        playlist_calls = [c for c in get.calls if c[0].endswith("playlistItems")]
        assert len(playlist_calls) == 1


class TestMark:
    def test_writes_statuses_and_summary_count(self, tmp_path: Path) -> None:
        p = tmp_path / "s.json"
        n = run_mark(
            p,
            ingested=["aaaaaaaaaaa"],
            skipped=["bbbbbbbbbbb"],
            channel_seen=[],
            candidates_json=None,
            now=NOW,
        )
        assert n == 2
        state = load_state(p)
        assert state["videos"]["aaaaaaaaaaa"]["status"] == "ingested"
        assert state["videos"]["bbbbbbbbbbb"]["status"] == "skipped"
        assert state["videos"]["aaaaaaaaaaa"]["channel_id"] is None

    def test_candidates_json_enriches(self, tmp_path: Path) -> None:
        cj = tmp_path / "cands.json"
        cj.write_text(
            json.dumps(
                {
                    "candidates": [
                        {
                            "video_id": "aaaaaaaaaaa",
                            "channel_id": "UCx",
                            "title": "BTC weekly",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        p = tmp_path / "s.json"
        run_mark(
            p,
            ingested=["aaaaaaaaaaa"],
            skipped=[],
            channel_seen=[],
            candidates_json=cj,
            now=NOW,
        )
        entry = load_state(p)["videos"]["aaaaaaaaaaa"]
        assert entry["channel_id"] == "UCx"
        assert entry["title"] == "BTC weekly"

    def test_bad_id_aborts(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            run_mark(
                tmp_path / "s.json",
                ingested=["nope"],
                skipped=[],
                channel_seen=[],
                candidates_json=None,
                now=NOW,
            )

    def test_id_in_both_lists_aborts(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            run_mark(
                tmp_path / "s.json",
                ingested=["aaaaaaaaaaa"],
                skipped=["aaaaaaaaaaa"],
                channel_seen=[],
                candidates_json=None,
                now=NOW,
            )

    def test_remark_overwrites_last_wins(self, tmp_path: Path) -> None:
        p = tmp_path / "s.json"
        run_mark(
            p,
            ingested=[],
            skipped=["aaaaaaaaaaa"],
            channel_seen=[],
            candidates_json=None,
            now=NOW,
        )
        run_mark(
            p,
            ingested=["aaaaaaaaaaa"],
            skipped=[],
            channel_seen=[],
            candidates_json=None,
            now=NOW,
        )
        assert load_state(p)["videos"]["aaaaaaaaaaa"]["status"] == "ingested"

    def test_channel_seen_persists_but_never_moves_existing_floor(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "s.json"
        cid = "UCabcdefghijklmnopqrstuv"
        run_mark(
            p,
            ingested=[],
            skipped=[],
            channel_seen=[f"{cid}=2026-07-17T00:00:00+00:00"],
            candidates_json=None,
            now=NOW,
        )
        assert (
            load_state(p)["channels"][cid]["floor_ts_utc"]
            == "2026-07-17T00:00:00+00:00"
        )
        run_mark(
            p,
            ingested=[],
            skipped=[],
            channel_seen=[f"{cid}=2026-07-25T00:00:00+00:00"],
            candidates_json=None,
            now=NOW,
        )
        # static floor: a later --channel-seen must NOT advance it
        assert (
            load_state(p)["channels"][cid]["floor_ts_utc"]
            == "2026-07-17T00:00:00+00:00"
        )

    def test_channel_seen_derived_from_poll_json(self, tmp_path: Path) -> None:
        """The poll payload already carries both fields; don't retype them.

        `--channel-seen` takes ONE pair per flag, so marking a 9-channel poll
        meant repeating it nine times with values copied by hand out of the
        very JSON already being passed to `--candidates-json`.
        """
        p = tmp_path / "s.json"
        a, b = "UCabcdefghijklmnopqrstuv", "UCzyxwvutsrqponmlkjihgfe"
        payload = tmp_path / "poll.json"
        payload.write_text(
            json.dumps(
                {
                    "candidates": [],
                    "channels": [
                        {"channel_id": a, "floor_ts_utc": "2026-07-17T00:00:00+00:00"},
                        {"channel_id": b, "floor_ts_utc": "2026-07-18T00:00:00+00:00"},
                    ],
                }
            ),
            encoding="utf-8",
        )

        run_mark(
            p,
            ingested=[],
            skipped=[],
            channel_seen=[],
            candidates_json=payload,
            now=NOW,
        )

        chans = load_state(p)["channels"]
        assert chans[a]["floor_ts_utc"] == "2026-07-17T00:00:00+00:00"
        assert chans[b]["floor_ts_utc"] == "2026-07-18T00:00:00+00:00"

    def test_derived_channel_seen_never_moves_an_existing_floor(
        self, tmp_path: Path
    ) -> None:
        """Deriving must not weaken the static-floor guarantee.

        This is the wifey-#68 defect class: a watermark that advances on its
        own. The derived pairs go through the same setdefault as explicit ones,
        so a later poll reporting a newer floor cannot move a recorded one.
        """
        p = tmp_path / "s.json"
        cid = "UCabcdefghijklmnopqrstuv"
        run_mark(
            p,
            ingested=[],
            skipped=[],
            channel_seen=[f"{cid}=2026-07-17T00:00:00+00:00"],
            candidates_json=None,
            now=NOW,
        )
        payload = tmp_path / "poll.json"
        payload.write_text(
            json.dumps(
                {
                    "candidates": [],
                    "channels": [
                        {"channel_id": cid, "floor_ts_utc": "2026-08-01T00:00:00+00:00"}
                    ],
                }
            ),
            encoding="utf-8",
        )

        run_mark(
            p,
            ingested=[],
            skipped=[],
            channel_seen=[],
            candidates_json=payload,
            now=NOW,
        )

        assert (
            load_state(p)["channels"][cid]["floor_ts_utc"]
            == "2026-07-17T00:00:00+00:00"
        )

    def test_explicit_channel_seen_wins_over_derived(self, tmp_path: Path) -> None:
        """Ordering is load-bearing: setdefault means first writer wins."""
        p = tmp_path / "s.json"
        cid = "UCabcdefghijklmnopqrstuv"
        payload = tmp_path / "poll.json"
        payload.write_text(
            json.dumps(
                {
                    "candidates": [],
                    "channels": [
                        {"channel_id": cid, "floor_ts_utc": "2026-08-01T00:00:00+00:00"}
                    ],
                }
            ),
            encoding="utf-8",
        )

        run_mark(
            p,
            ingested=[],
            skipped=[],
            channel_seen=[f"{cid}=2026-07-17T00:00:00+00:00"],
            candidates_json=payload,
            now=NOW,
        )

        assert (
            load_state(p)["channels"][cid]["floor_ts_utc"]
            == "2026-07-17T00:00:00+00:00"
        )

    def test_derived_channel_seen_skips_incomplete_entries(
        self, tmp_path: Path
    ) -> None:
        """A payload shape without both fields must not abort the whole mark."""
        p = tmp_path / "s.json"
        cid = "UCabcdefghijklmnopqrstuv"
        payload = tmp_path / "poll.json"
        payload.write_text(
            json.dumps(
                {
                    "candidates": [],
                    "channels": [
                        {"channel_id": cid},  # no floor_ts_utc
                        {"floor_ts_utc": "2026-07-17T00:00:00+00:00"},  # no id
                    ],
                }
            ),
            encoding="utf-8",
        )

        run_mark(
            p,
            ingested=[],
            skipped=[],
            channel_seen=[],
            candidates_json=payload,
            now=NOW,
        )

        assert load_state(p)["channels"] == {}

    def test_bad_channel_seen_aborts(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            run_mark(
                tmp_path / "s.json",
                ingested=[],
                skipped=[],
                channel_seen=["UCx:2026-07-17T00:00:00+00:00"],  # colon, not =
                candidates_json=None,
                now=NOW,
            )

    def test_channel_seen_rejects_naive_timestamp(self, tmp_path: Path) -> None:
        # a valid-shape channel id but an offset-less timestamp must be
        # rejected, never silently assumed-UTC (mirrors video_calltime.py)
        with pytest.raises(SystemExit):
            run_mark(
                tmp_path / "s.json",
                ingested=[],
                skipped=[],
                channel_seen=["UCabcdefghijklmnopqrstuv=2026-07-17T00:00:00"],
                candidates_json=None,
                now=NOW,
            )

    def test_channel_seen_rejects_malformed_channel_id(self, tmp_path: Path) -> None:
        # passes the old bare startswith("UC") check but fails the full
        # 24-char shape
        with pytest.raises(SystemExit):
            run_mark(
                tmp_path / "s.json",
                ingested=[],
                skipped=[],
                channel_seen=["UCshort=2026-07-17T00:00:00+00:00"],
                candidates_json=None,
                now=NOW,
            )


class TestResolve:
    def test_resolve_request_shape_and_toml_block(self) -> None:
        payload = {
            "items": [{"id": "UCreal", "snippet": {"title": "Into The Cryptoverse"}}]
        }
        get = FakeGet({"channels": [FakeResp(200, payload)]})
        block = resolve_handle(get, "K", "intothecryptoverse")
        url, params = get.calls[0]
        assert url.endswith("/channels")
        assert params["forHandle"] == "@intothecryptoverse"
        assert params["part"] == "id,snippet"
        assert "[[channel]]" in block
        assert 'id = "UCreal"' in block
        assert 'name = "Into The Cryptoverse"' in block

    def test_resolve_unknown_handle_aborts(self) -> None:
        get = FakeGet({"channels": [FakeResp(200, {"items": []})]})
        with pytest.raises(SystemExit):
            resolve_handle(get, "K", "@ghost")


def write_config(tmp_path: Path, extra_channel: str = "") -> Path:
    p = tmp_path / "channels.toml"
    p.write_text(
        '[[channel]]\nid = "UCabcdefghijklmnopqrstu"\nname = "One"\n' + extra_channel,
        encoding="utf-8",
    )
    return p


class TestMainPoll:
    def test_main_loads_dotenv_before_reading_the_key(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A key that lives only in .env must reach os.environ BEFORE main() reads
        # it. Every other test sets the env var directly, so this is the only test
        # that exercises the .env -> os.environ hop at all.
        called: list[bool] = []

        def fake_load_dotenv(*a: Any, **k: Any) -> None:
            called.append(True)
            monkeypatch.setenv("YOUTUBE_API_KEY", "FROM_DOTENV")

        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        monkeypatch.setattr("tools.yt_feed.load_dotenv", fake_load_dotenv)
        get = FakeGet(
            {
                "channels": [
                    FakeResp(
                        200,
                        {"items": [{"id": "UCzzz", "snippet": {"title": "Chan"}}]},
                    )
                ]
            }
        )
        rc = main(["resolve", "@somehandle"], get=get, now=NOW)
        assert called == [True]
        assert rc == 0  # no "key is not set" abort
        assert get.calls[0][1]["key"] == "FROM_DOTENV"
        assert 'id = "UCzzz"' in capsys.readouterr().out

    def test_missing_api_key_exits_2(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr("tools.yt_feed.load_dotenv", lambda *a, **k: None)
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        rc = main(
            [
                "poll",
                "--config",
                str(write_config(tmp_path)),
                "--state",
                str(tmp_path / "s.json"),
            ],
            get=FakeGet({}),
            now=NOW,
        )
        assert rc == 2
        assert "YOUTUBE_API_KEY" in capsys.readouterr().err

    def test_partial_failure_exits_1_keeps_other_channel(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("YOUTUBE_API_KEY", "K")
        cfg = write_config(
            tmp_path, '[[channel]]\nid = "UCzzzzzzzzzzzzzzzzzzzzz"\nname = "Two"\n'
        )
        good = {
            "items": [
                playlist_item("ggggggggggg", "BTC weekly", "2026-07-31T02:00:00Z")
            ]
        }
        vids = {"items": [video_item("ggggggggggg", "PT21M")]}
        err = {"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}]}}
        get = FakeGet(
            {
                "playlistItems": [FakeResp(200, good), FakeResp(403, err)],
                "videos": [FakeResp(200, vids)],
            }
        )
        state_path = tmp_path / "s.json"
        rc = main(
            ["poll", "--config", str(cfg), "--state", str(state_path), "--json"],
            get=get,
            now=NOW,
        )
        assert rc == 1
        payload = json.loads(capsys.readouterr().out)
        assert [c["video_id"] for c in payload["candidates"]] == ["ggggggggggg"]
        assert any(
            "quotaExceeded" in e for ch in payload["channels"] for e in ch["errors"]
        )
        # read-only invariant: poll wrote NOTHING
        assert not state_path.exists()

    def test_paused_channel_is_not_polled_but_is_reported(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Two channels, one paused. The live one must still poll normally, and
        # the paused one must appear in the output -- silently omitting it would
        # make "paused" indistinguishable from "broken", the same confusion the
        # backfill-to-diagnose rule exists to resolve.
        monkeypatch.setenv("YOUTUBE_API_KEY", "K")
        cfg = write_config(
            tmp_path,
            '[[channel]]\nid = "UCzzzzzzzzzzzzzzzzzzzzz"\nname = "Paused"\n'
            "paused = true\n",
        )
        items = {
            "items": [
                playlist_item("ggggggggggg", "BTC weekly", "2026-07-31T02:00:00Z")
            ]
        }
        get = FakeGet(
            {
                "playlistItems": [FakeResp(200, items)],
                "videos": [
                    FakeResp(200, {"items": [video_item("ggggggggggg", "PT21M")]})
                ],
            }
        )
        rc = main(
            [
                "poll",
                "--config",
                str(cfg),
                "--state",
                str(tmp_path / "s.json"),
                "--json",
            ],
            get=get,
            now=NOW,
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        polled = [c["channel_id"] for c in payload["channels"]]
        assert polled == ["UCabcdefghijklmnopqrstu"]  # paused one never polled
        assert payload["paused"] == [
            {"channel_id": "UCzzzzzzzzzzzzzzzzzzzzz", "channel_name": "Paused"}
        ]
        # and it cost no API calls at all
        assert all("UCzzzzzzzzzzzzzzzzzzzzz" not in str(c) for c in get.calls)

    def test_paused_channel_is_still_reachable_by_backfill(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # The negative control that keeps the scope honest: `backfill` is the
        # diagnostic that tells a genuinely quiet channel from a broken one, so
        # pausing must NOT take it away. If this ever starts raising
        # "not in config", the pause filter has leaked out of the poll branch.
        monkeypatch.setenv("YOUTUBE_API_KEY", "K")
        cfg = write_config(
            tmp_path,
            '[[channel]]\nid = "UCzzzzzzzzzzzzzzzzzzzzz"\nname = "Paused"\n'
            "paused = true\n",
        )
        get = FakeGet(
            {
                "playlistItems": [FakeResp(200, {"items": []})],
                "videos": [FakeResp(200, {"items": []})],
            }
        )
        rc = main(
            [
                "backfill",
                "UCzzzzzzzzzzzzzzzzzzzzz",
                "--config",
                str(cfg),
                "--state",
                str(tmp_path / "s.json"),
                "--json",
            ],
            get=get,
            now=NOW,
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert [c["channel_id"] for c in payload["channels"]] == [
            "UCzzzzzzzzzzzzzzzzzzzzz"
        ]
        # backfill reports no `paused` block -- the key is poll-only
        assert payload["paused"] == []

    def test_backfill_requires_channel_in_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("YOUTUBE_API_KEY", "K")
        with pytest.raises(SystemExit, match="not in config"):
            main(
                [
                    "backfill",
                    "UCnotconfigured000000000",
                    "--config",
                    str(write_config(tmp_path)),
                    "--state",
                    str(tmp_path / "s.json"),
                ],
                get=FakeGet({}),
                now=NOW,
            )

    def test_mark_via_cli(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)  # mark needs no key
        state_path = tmp_path / "s.json"
        rc = main(
            [
                "mark",
                "--state",
                str(state_path),
                "--ingested",
                "aaaaaaaaaaa",
                "--skipped",
                "bbbbbbbbbbb",
                "--channel-seen",
                "UCabcdefghijklmnopqrstuv=2026-07-17T00:00:00+00:00",
            ],
            get=FakeGet({}),
            now=NOW,
        )
        assert rc == 0
        state = load_state(state_path)
        assert state["videos"]["aaaaaaaaaaa"]["status"] == "ingested"
        assert "UCabcdefghijklmnopqrstuv" in state["channels"]

    def test_mark_accepts_dash_led_ids(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # #956: a bare `--skipped -8u47LL2wZE` is read as an option and the
        # whole call exits 2. The `=` form must repeat and ACCUMULATE, and an
        # ids file must carry dash-led ids with no escaping at all.
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        state_path = tmp_path / "s.json"
        ids_file = tmp_path / "skipped.txt"
        ids_file.write_text("-JbMVBT9LlU  ccccccccccc\n# comment\n", encoding="utf-8")
        rc = main(
            [
                "mark",
                "--state",
                str(state_path),
                "--ingested=aaaaaaaaaaa",
                "--skipped=-8u47LL2wZE",
                "--skipped=bbbbbbbbbbb",
                "--skipped-file",
                str(ids_file),
            ],
            get=FakeGet({}),
            now=NOW,
        )
        assert rc == 0
        videos = load_state(state_path)["videos"]
        assert videos["aaaaaaaaaaa"]["status"] == "ingested"
        for vid in ("-8u47LL2wZE", "bbbbbbbbbbb", "-JbMVBT9LlU", "ccccccccccc"):
            assert videos[vid]["status"] == "skipped", vid


class TestExampleConfig:
    def test_example_config_parses(self) -> None:
        cfg = load_feed_config(Path("config/youtube_channels.toml.example"))
        assert cfg.cold_start_days == 14
        assert cfg.channels[0].name == "Benjamin Cowen"


class TestChannelHint:
    """`intro_recap_s` lookup — the /ingest-video side of the channel config.

    Context: this exists because a `mechanic` extracted from a channel's opening
    recap block routed to Stream B on 2026-08-03. `route_target`'s `retrospective`
    drop is setup-only by design, so nothing in code caught it; the rule lived only
    in an operator's head. These tests pin the lookup so it cannot regress the way
    the /ingest-video attribution filter did (diagnosed round 1, repeated round 7).
    """

    def _cfg(self) -> FeedConfig:
        return FeedConfig(
            cold_start_days=14,
            channels=(
                make_channel(
                    id="UCkSCETUQ-oPbVccY9Z7vZZg",
                    name="大漂亮的K线日记",
                    handle="@GiantCutie-K",
                    intro_recap_s=75,
                ),
                make_channel(
                    id="UCRvqjQPSeaWn-uEx-w0XOIg",
                    name="Benjamin Cowen",
                    handle="@intothecryptoverse",
                ),
            ),
        )

    def test_matches_on_handle_when_name_is_cjk(self) -> None:
        """The load-bearing case: meta.author matches neither `id` nor a CJK `name`."""
        match = channel_hint(self._cfg(), author="@GiantCutie-K")
        assert match is not None
        assert match.intro_recap_s == 75

    def test_handle_match_ignores_case_and_at_sign(self) -> None:
        for probe in ("giantcutie-k", "@giantcutie-k", "  @GIANTCUTIE-K  "):
            match = channel_hint(self._cfg(), author=probe)
            assert match is not None, probe
            assert match.intro_recap_s == 75, probe

    def test_channel_id_wins_over_author(self) -> None:
        match = channel_hint(
            self._cfg(),
            author="@GiantCutie-K",
            channel_id="UCRvqjQPSeaWn-uEx-w0XOIg",
        )
        assert match is not None
        assert match.name == "Benjamin Cowen"

    def test_falls_back_to_name_when_no_handle_configured(self) -> None:
        cfg = FeedConfig(
            cold_start_days=14,
            channels=(make_channel(name="Benjamin Cowen", intro_recap_s=30),),
        )
        match = channel_hint(cfg, author="Benjamin Cowen")
        assert match is not None
        assert match.intro_recap_s == 30

    def test_unknown_author_returns_none(self) -> None:
        assert channel_hint(self._cfg(), author="@nobody") is None

    def test_empty_author_returns_none(self) -> None:
        assert channel_hint(self._cfg(), author="") is None
        assert channel_hint(self._cfg(), author="@") is None


class TestIsIntroRecap:
    def test_flags_timestamps_inside_the_window(self) -> None:
        ch = make_channel(intro_recap_s=75)
        assert is_intro_recap(39.1, ch) is True
        assert is_intro_recap(59.9, ch) is True

    def test_boundary_is_exclusive(self) -> None:
        """`intro_recap_s` names the first second of real content."""
        assert is_intro_recap(75.0, make_channel(intro_recap_s=75)) is False
        assert is_intro_recap(74.999, make_channel(intro_recap_s=75)) is True

    def test_zero_disables_the_rule_entirely(self) -> None:
        """A channel with no rule must never flag anything, including ts=0.0."""
        assert is_intro_recap(0.0, make_channel(intro_recap_s=0)) is False
        assert is_intro_recap(10.0, make_channel(intro_recap_s=0)) is False

    def test_none_channel_is_safe(self) -> None:
        """An unconfigured channel degrades to no rule, never to an exception."""
        assert is_intro_recap(5.0, None) is False


class TestIntroRecapConfigParsing:
    def test_new_fields_parse(self, tmp_path: Path) -> None:
        p = tmp_path / "channels.toml"
        p.write_text(
            "[feed]\ncold_start_days = 14\n\n[[channel]]\n"
            'id = "UCkSCETUQ-oPbVccY9Z7vZZg"\nname = "大漂亮的K线日记"\n'
            'handle = "@GiantCutie-K"\nintro_recap_s = 75\n',
            encoding="utf-8",
        )
        cfg = load_feed_config(p)
        assert cfg.channels[0].handle == "@GiantCutie-K"
        assert cfg.channels[0].intro_recap_s == 75

    def test_both_fields_are_optional(self, tmp_path: Path) -> None:
        """Every existing config predates these keys and must still load."""
        p = tmp_path / "channels.toml"
        p.write_text(
            "[feed]\ncold_start_days = 14\n\n[[channel]]\n"
            'id = "UCabcdefghijklmnopqrstu"\nname = "Old Entry"\n',
            encoding="utf-8",
        )
        cfg = load_feed_config(p)
        assert cfg.channels[0].handle == ""
        assert cfg.channels[0].intro_recap_s == 0


class TestHintCli:
    def test_hint_needs_no_api_key(
        self, tmp_path: Path, capsys: Any, monkeypatch: Any
    ) -> None:
        """A pure config read must not be gated behind YOUTUBE_API_KEY."""
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        p = tmp_path / "channels.toml"
        p.write_text(
            "[feed]\ncold_start_days = 14\n\n[[channel]]\n"
            'id = "UCkSCETUQ-oPbVccY9Z7vZZg"\nname = "大漂亮的K线日记"\n'
            'handle = "@GiantCutie-K"\nintro_recap_s = 75\n',
            encoding="utf-8",
        )
        rc = main(
            ["hint", "--author", "@GiantCutie-K", "--config", str(p)],
            get=FakeGet({}),
            now=NOW,
        )
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["matched"] is True
        assert out["intro_recap_s"] == 75

    def test_hint_reports_unmatched_without_failing(
        self, tmp_path: Path, capsys: Any, monkeypatch: Any
    ) -> None:
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        p = tmp_path / "channels.toml"
        p.write_text(
            "[feed]\ncold_start_days = 14\n\n[[channel]]\n"
            'id = "UCabcdefghijklmnopqrstu"\nname = "Other"\n',
            encoding="utf-8",
        )
        rc = main(
            ["hint", "--author", "@nobody", "--config", str(p)],
            get=FakeGet({}),
            now=NOW,
        )
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["matched"] is False
        assert out["intro_recap_s"] == 0


class TestResolveEmitsNewKeys:
    def test_resolve_block_includes_handle_and_intro_recap(self) -> None:
        """A pasted block should carry the keys, or nobody learns they exist."""

        def get(url: str, *, params: dict[str, str]) -> Any:
            return FakeResp(
                200,
                {
                    "items": [
                        {"id": "UCabcdefghijklmnopqrstu", "snippet": {"title": "X"}}
                    ]
                },
            )

        block = resolve_handle(get, "K", "somehandle")
        assert 'handle = "@somehandle"' in block
        assert "intro_recap_s = 0" in block


class TestChannelPlaylists:
    """ST40: a channel's curated playlists were invisible — only `UU…` was ever paged.

    `uploads_playlist_id()` builds the uploads mirror and nothing called
    `playlists.list`, so hand-curated `PL…` playlists could not be discovered at
    all. They are where the educational / framework material lives, which is
    Stream B — the stream that has never been tested — while Stream C author picks
    are measured-dead. The plumbing already existed: `_fetch_playlist_page` takes
    an arbitrary playlist id and was merely only ever called with the mirror.
    """

    def _pages(self) -> dict[str, list[FakeResp]]:
        page1 = {
            "items": [
                {
                    "id": "PLaaaaaaaaaaaaaaa1",
                    "snippet": {"title": "Order Flow 101"},
                    "contentDetails": {"itemCount": 52},
                }
            ],
            "nextPageToken": "P2",
        }
        page2 = {
            "items": [
                {
                    "id": "PLaaaaaaaaaaaaaaa2",
                    "snippet": {"title": "Weekly recaps"},
                    "contentDetails": {"itemCount": 7},
                }
            ]
        }
        return {"playlists": [FakeResp(200, page1), FakeResp(200, page2)]}

    def test_enumerates_the_playlists_endpoint_keyed_on_channel(self) -> None:
        get = FakeGet(self._pages())

        found = list_channel_playlists(
            get, "K", "UCabcdefghijklmnopqrstu", dict(FRESH_STATE)
        )

        url, params = get.calls[0]
        assert url.endswith("/playlists")
        assert params["channelId"] == "UCabcdefghijklmnopqrstu"
        assert [p.playlist_id for p in found] == [
            "PLaaaaaaaaaaaaaaa1",
            "PLaaaaaaaaaaaaaaa2",
        ]

    def test_never_touches_playlistitems(self) -> None:
        """Enumeration is 1 quota unit per page; listing CONTENTS is a separate ask."""
        get = FakeGet(self._pages())

        list_channel_playlists(get, "K", "UCabcdefghijklmnopqrstu", dict(FRESH_STATE))

        assert not [c for c in get.calls if c[0].endswith("playlistItems")]

    def test_reports_the_tranche_cursor_from_state(self) -> None:
        state = {
            **FRESH_STATE,
            "playlists": {"PLaaaaaaaaaaaaaaa1": {"examined": 20}},
        }

        found = list_channel_playlists(
            get_ := FakeGet(self._pages()), "K", "UCx", state
        )
        assert get_.calls

        by_id = {p.playlist_id: p for p in found}
        assert (
            by_id["PLaaaaaaaaaaaaaaa1"].examined,
            by_id["PLaaaaaaaaaaaaaaa1"].item_count,
        ) == (20, 52)
        assert by_id["PLaaaaaaaaaaaaaaa2"].examined == 0


class TestBackfillPlaylist:
    """One TRANCHE of a curated playlist, on the SAME video ledger as the uploads path."""

    def _items(self, n: int, start: int = 1) -> list[dict[str, Any]]:
        return [
            playlist_item(f"bbbbbbbbb{i:02d}", f"lesson {i}", "2026-07-30T00:00:00Z")
            for i in range(start, start + n)
        ]

    def _routes(
        self, items: list[dict[str, Any]], token: str | None = None
    ) -> dict[str, list[FakeResp]]:
        page: dict[str, Any] = {"items": items}
        if token is not None:
            page["nextPageToken"] = token
        return {
            "playlistItems": [FakeResp(200, page)],
            "videos": [
                FakeResp(
                    200,
                    {
                        "items": [
                            video_item(i["contentDetails"]["videoId"], "PT30M")
                            for i in items
                        ]
                    },
                )
            ],
        }

    def test_pages_the_named_playlist_not_the_uploads_mirror(self) -> None:
        """The whole defect in one assertion."""
        get = FakeGet(self._routes(self._items(2)))

        backfill_playlist(
            make_channel(),
            "PLcuratedxxxxxxxx",
            dict(FRESH_STATE),
            now=NOW,
            get=get,
            api_key="K",
            since=None,
            max_videos=10,
        )

        playlist_calls = [c for c in get.calls if c[0].endswith("playlistItems")]
        assert playlist_calls[0][1]["playlistId"] == "PLcuratedxxxxxxxx"
        assert not any("UU" in c[1].get("playlistId", "") for c in playlist_calls)

    def test_a_second_tranche_resumes_where_the_first_stopped(self) -> None:
        """Constraint 1: 50+ videos is ~1.5M tokens, so a whole playlist at once is
        the same as not offering it. A curated playlist is in its author's order,
        so there is no chronological watermark to lean on — only this cursor."""
        state: dict[str, Any] = {**FRESH_STATE, "playlists": {"PLx": {"examined": 2}}}
        get = FakeGet(self._routes(self._items(4)))

        result = backfill_playlist(
            make_channel(),
            "PLx",
            state,
            now=NOW,
            get=get,
            api_key="K",
            since=None,
            max_videos=10,
        )

        assert [c.video_id for c in result.candidates] == ["bbbbbbbbb03", "bbbbbbbbb04"]
        assert result.playlist is not None
        assert (result.playlist.examined_before, result.playlist.examined_after) == (
            2,
            4,
        )

    def test_the_cursor_stops_at_the_first_entry_this_run_never_read(self) -> None:
        """The subtle one: advancing by the whole PAGE would step the next tranche
        past videos this run never looked at, and the cursor only moves forward —
        so those videos would never be offered again."""
        get = FakeGet(self._routes(self._items(5), token="P2"))

        result = backfill_playlist(
            make_channel(),
            "PLx",
            dict(FRESH_STATE),
            now=NOW,
            get=get,
            api_key="K",
            since=None,
            max_videos=2,
        )

        assert len(result.candidates) == 2
        assert result.playlist is not None
        assert result.playlist.examined_after == 2  # NOT 5, the page length
        assert result.playlist.exhausted is False

    def test_exhausted_when_the_playlist_ends(self) -> None:
        get = FakeGet(self._routes(self._items(2)))

        result = backfill_playlist(
            make_channel(),
            "PLx",
            dict(FRESH_STATE),
            now=NOW,
            get=get,
            api_key="K",
            since=None,
            max_videos=10,
        )

        assert result.playlist is not None
        assert result.playlist.exhausted is True

    def test_the_payload_names_what_the_tranche_OFFERED(self) -> None:
        """`mark` cannot tell a deferred candidate from a routed one without this;
        the cursor is a count, and a count cannot name who is still owed a look."""
        get = FakeGet(self._routes(self._items(2)))

        result = backfill_playlist(
            make_channel(),
            "PLx",
            dict(FRESH_STATE),
            now=NOW,
            get=get,
            api_key="K",
            since=None,
            max_videos=10,
        )
        payload = _results_to_dict([result], NOW)

        assert payload["playlists"][0]["offered"] == [
            "bbbbbbbbb01",
            "bbbbbbbbb02",
        ]

    def test_the_video_ledger_is_shared_with_the_uploads_path(self) -> None:
        """Constraint 2: playlists CONTAIN uploads, so a separate namespace would
        re-present videos already decided — the re-ask problem `paused` had to fix."""
        state: dict[str, Any] = {
            **FRESH_STATE,
            "videos": {"bbbbbbbbb01": {"status": "ingested"}},
        }
        get = FakeGet(self._routes(self._items(2)))

        result = backfill_playlist(
            make_channel(),
            "PLx",
            state,
            now=NOW,
            get=get,
            api_key="K",
            since=None,
            max_videos=10,
        )

        assert [c.video_id for c in result.candidates] == ["bbbbbbbbb02"]
        assert result.excluded["ledgered"] == 1

    def test_api_error_is_reported_not_raised(self) -> None:
        get = FakeGet({"playlistItems": [FakeResp(404, {})]})

        result = backfill_playlist(
            make_channel(),
            "PLx",
            dict(FRESH_STATE),
            now=NOW,
            get=get,
            api_key="K",
            since=None,
            max_videos=10,
        )

        assert result.errors and result.candidates == []


class TestPlaylistCursorIsWrittenOnlyByMark:
    """`mark` stays the ONLY state writer; the cursor advances, unlike a channel floor."""

    def test_playlist_seen_advances_the_cursor(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"

        run_mark(
            path,
            ingested=[],
            skipped=[],
            channel_seen=[],
            playlist_seen=["PLxxxxxxxxxxxx=30"],
            candidates_json=None,
            now=NOW,
        )

        assert load_state(path)["playlists"]["PLxxxxxxxxxxxx"]["examined"] == 30

    def test_the_cursor_never_moves_backwards(self, tmp_path: Path) -> None:
        """A channel floor is STATIC (setdefault); a tranche cursor is PROGRESS (max).
        Replaying an older payload must not re-present videos already decided."""
        path = tmp_path / "state.json"
        run_mark(
            path,
            ingested=[],
            skipped=[],
            channel_seen=[],
            playlist_seen=["PLxxxxxxxxxxxx=30"],
            candidates_json=None,
            now=NOW,
        )

        run_mark(
            path,
            ingested=[],
            skipped=[],
            channel_seen=[],
            playlist_seen=["PLxxxxxxxxxxxx=10"],
            candidates_json=None,
            now=NOW,
        )

        assert load_state(path)["playlists"]["PLxxxxxxxxxxxx"]["examined"] == 30

    def test_the_cursor_is_derived_from_the_backfill_payload(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "state.json"
        payload = tmp_path / "cands.json"
        payload.write_text(
            json.dumps(
                {
                    "candidates": [],
                    "playlists": [
                        {
                            "playlist_id": "PLxxxxxxxxxxxx",
                            "examined_after": 12,
                            "examined_before": 0,
                            "exhausted": False,
                            "title": "",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        run_mark(
            path,
            ingested=[],
            skipped=[],
            channel_seen=[],
            playlist_seen=[],
            candidates_json=payload,
            now=NOW,
        )

        assert load_state(path)["playlists"]["PLxxxxxxxxxxxx"]["examined"] == 12

    def _payload(self, tmp_path: Path, offered: list[str] | None, **extra: Any) -> Path:
        entry: dict[str, Any] = {
            "playlist_id": "PLxxxxxxxxxxxx",
            "examined_after": 12,
            "examined_before": 4,
            "exhausted": False,
            "title": "",
            **extra,
        }
        if offered is not None:
            entry["offered"] = offered
        path = tmp_path / "cands.json"
        path.write_text(
            json.dumps({"candidates": [], "playlists": [entry]}), encoding="utf-8"
        )
        return path

    def test_a_deferred_candidate_holds_the_whole_tranche(self, tmp_path: Path) -> None:
        """THE defect: `examined_after` counts what the run OFFERED, and the next
        tranche skips the cursor's leading entries — so advancing past an undecided
        candidate does not postpone it, it makes `defer` unreachable."""
        path = tmp_path / "state.json"

        run_mark(
            path,
            ingested=["bbbbbbbbb01"],
            skipped=[],
            channel_seen=[],
            playlist_seen=[],
            candidates_json=self._payload(tmp_path, ["bbbbbbbbb01", "bbbbbbbbb02"]),
            now=NOW,
        )

        # bbbbbbbbb02 was offered and never decided, so the cursor stays put
        assert load_state(path)["playlists"]["PLxxxxxxxxxxxx"]["examined"] == 4

    def test_a_fully_decided_tranche_advances(self, tmp_path: Path) -> None:
        """Specificity control: without it, a cursor frozen for ANY reason would
        read exactly like the hold above."""
        path = tmp_path / "state.json"

        run_mark(
            path,
            ingested=["bbbbbbbbb01"],
            skipped=["bbbbbbbbb02"],
            channel_seen=[],
            playlist_seen=[],
            candidates_json=self._payload(tmp_path, ["bbbbbbbbb01", "bbbbbbbbb02"]),
            now=NOW,
        )

        assert load_state(path)["playlists"]["PLxxxxxxxxxxxx"]["examined"] == 12

    def test_a_payload_predating_offered_still_advances(self, tmp_path: Path) -> None:
        """An old payload cannot answer the question; freezing its cursor would
        strand a tranche the operator has no way to read."""
        path = tmp_path / "state.json"

        run_mark(
            path,
            ingested=[],
            skipped=[],
            channel_seen=[],
            playlist_seen=[],
            candidates_json=self._payload(tmp_path, None),
            now=NOW,
        )

        assert load_state(path)["playlists"]["PLxxxxxxxxxxxx"]["examined"] == 12

    def test_an_empty_tranche_advances(self, tmp_path: Path) -> None:
        """Every entry was excluded before it became a candidate — nothing is owed
        a second look, so the tranche is genuinely spent."""
        path = tmp_path / "state.json"

        run_mark(
            path,
            ingested=[],
            skipped=[],
            channel_seen=[],
            playlist_seen=[],
            candidates_json=self._payload(tmp_path, []),
            now=NOW,
        )

        assert load_state(path)["playlists"]["PLxxxxxxxxxxxx"]["examined"] == 12

    def test_a_malformed_pair_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            run_mark(
                tmp_path / "s.json",
                ingested=[],
                skipped=[],
                channel_seen=[],
                playlist_seen=["not-a-playlist=3"],
                candidates_json=None,
                now=NOW,
            )

    def test_a_non_numeric_cursor_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            run_mark(
                tmp_path / "s.json",
                ingested=[],
                skipped=[],
                channel_seen=[],
                playlist_seen=["PLxxxxxxxxxxxx=soon"],
                candidates_json=None,
                now=NOW,
            )

    def test_a_pre_st40_state_file_gains_the_key_without_a_reset(
        self, tmp_path: Path
    ) -> None:
        """A version bump would make every existing file "unrecognized", and that
        refusal exists to stop a reset that re-queues everything ever ingested."""
        path = tmp_path / "state.json"
        path.write_text(
            json.dumps({"version": 1, "channels": {}, "videos": {"z": {}}}),
            encoding="utf-8",
        )

        state = load_state(path)

        assert state["playlists"] == {}
        assert state["videos"] == {"z": {}}


class TestMainPlaylistWiring:
    def test_playlists_subcommand_prints_each_playlist(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("YOUTUBE_API_KEY", "K")
        get = FakeGet(
            {
                "playlists": [
                    FakeResp(
                        200,
                        {
                            "items": [
                                {
                                    "id": "PLaaaaaaaaaaaaaaa1",
                                    "snippet": {"title": "Order Flow 101"},
                                    "contentDetails": {"itemCount": 52},
                                }
                            ]
                        },
                    )
                ]
            }
        )

        rc = main(
            [
                "playlists",
                "UCabcdefghijklmnopqrstu",
                "--state",
                str(tmp_path / "s.json"),
            ],
            get=get,
            now=NOW,
        )

        assert rc == 0
        out = capsys.readouterr().out
        assert "PLaaaaaaaaaaaaaaa1" in out
        assert "0/52 examined" in out

    def test_playlists_needs_no_channel_config(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """`config/youtube_channels.toml` is GITIGNORED — absent on a fresh clone and in CI.

        `playlists` takes a raw UC id and reads nothing from it, so loading it would
        SystemExit on a file the subcommand never uses. That shipped, and CI caught it while
        every local run passed on a machine that happens to have the config.

        ⚠ **The obvious fix — pass a fixture `--config` — would have HIDDEN this.** The test
        would go green while the CLI stayed broken for anyone without operator data. So this
        test asserts the ABSENCE of the dependency, which means pointing `--config` at a path
        that does not exist and requiring success anyway.
        """
        monkeypatch.setenv("YOUTUBE_API_KEY", "K")
        get = FakeGet({"playlists": [FakeResp(200, {"items": []})]})

        rc = main(
            [
                "playlists",
                "UCabcdefghijklmnopqrstu",
                "--config",
                str(tmp_path / "does-not-exist.toml"),
                "--state",
                str(tmp_path / "s.json"),
            ],
            get=get,
            now=NOW,
        )

        assert rc == 0
        assert "0 playlist(s)" in capsys.readouterr().out

    def test_backfill_rejects_a_playlist_id_that_is_not_one(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("YOUTUBE_API_KEY", "K")
        cfg = tmp_path / "channels.toml"
        cfg.write_text(
            '[[channel]]\nid = "UCabcdefghijklmnopqrstu"\nname = "Test"\n',
            encoding="utf-8",
        )

        with pytest.raises(SystemExit):
            main(
                [
                    "backfill",
                    "UCabcdefghijklmnopqrstu",
                    "--playlist",
                    "https://youtube.com/playlist?list=PLx",
                    "--config",
                    str(cfg),
                    "--state",
                    str(tmp_path / "s.json"),
                ],
                get=FakeGet({}),
                now=NOW,
            )


class TestChapterDerivedRecapWindow:
    """ST46(1): a video's own chapters beat the hand-tuned per-channel constant.

    `intro_recap_s` is fitted per CHANNEL from a sample of uploads, so it is wrong on
    any upload that opens differently. Measured on 4Dkw1jz04lY: @GiantCutie-K's
    configured 120s against a recap chapter that actually runs to 186s.
    """

    def test_chapter_window_overrides_a_too_short_channel_constant(self) -> None:
        ch = make_channel(intro_recap_s=120)
        assert is_intro_recap(150.0, ch) is False
        assert is_intro_recap(150.0, ch, recap_end_s=186.0) is True

    def test_chapter_window_overrides_a_too_long_channel_constant(self) -> None:
        """The override must cut BOTH ways, or it is just a bigger constant."""
        ch = make_channel(intro_recap_s=120)
        assert is_intro_recap(60.0, ch) is True
        assert is_intro_recap(60.0, ch, recap_end_s=30.0) is False

    def test_falls_back_to_the_channel_constant_without_chapters(self) -> None:
        """~50% of the measured corpus has no chapters at all, so this is the
        common path rather than the edge case."""
        ch = make_channel(intro_recap_s=120)
        assert is_intro_recap(60.0, ch, recap_end_s=0.0) is True
        assert is_intro_recap(130.0, ch, recap_end_s=0.0) is False

    def test_chapters_trim_even_for_an_unconfigured_channel(self) -> None:
        """A video's own chapters need no channel row to be authoritative."""
        assert is_intro_recap(60.0, None, recap_end_s=186.0) is True
        assert is_intro_recap(200.0, None, recap_end_s=186.0) is False

    def test_no_channel_and_no_chapters_still_flags_nothing(self) -> None:
        assert is_intro_recap(0.0, None) is False

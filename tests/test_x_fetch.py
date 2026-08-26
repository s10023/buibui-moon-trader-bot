"""Tests for tools/x_fetch.py — strict TDD, no real network calls."""

from __future__ import annotations

import json
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import pytest

from tools.x_fetch import (
    _CACHE_SCHEMA,
    BatchResult,
    Unavailable,
    XPost,
    _format_human,
    _load_cached,
    _orig,
    _write_cache,
    download_photos,
    download_quoted_photos,
    fetch_x_batch,
    fetch_x_post,
    main,
    parse_tweet_id,
    resolve,
    walk_thread,
)


@dataclass
class FakeResp:
    status_code: int
    text: str = ""
    content: bytes = b""


def make_get(resp: FakeResp) -> Callable[..., FakeResp]:
    def _get(url: str, *, headers: dict[str, str]) -> FakeResp:
        return resp

    return _get


# ---------------------------------------------------------------------------
# Task 1: parse_tweet_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected",
    [
        (
            "https://x.com/cryptic_heych/status/2071837700500644228?s=20",
            "2071837700500644228",
        ),
        ("https://twitter.com/jack/status/20", "20"),
        ("https://x.com/foo/status/123/", "123"),
    ],
)
def test_parse_tweet_id(url: str, expected: str) -> None:
    assert parse_tweet_id(url) == expected


def test_parse_tweet_id_invalid() -> None:
    with pytest.raises(ValueError):
        parse_tweet_id("https://x.com/cryptic_heych")


# ---------------------------------------------------------------------------
# Task 2: XPost model + fetch_x_post
# ---------------------------------------------------------------------------

_CRYPTIC = {
    "__typename": "Tweet",
    "text": "Today's statistical analysis\n$BTC https://t.co/x",
    "created_at": "2026-06-30T06:06:15.000Z",
    "user": {"name": "HeycH", "screen_name": "cryptic_heych"},
    "photos": [{"url": "https://pbs.twimg.com/media/ABC.jpg"}],
    "mediaDetails": [
        {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/ABC.jpg"}
    ],
}


def test_fetch_maps_fields() -> None:
    url = "https://x.com/cryptic_heych/status/2071837700500644228"
    post = fetch_x_post(url, get=make_get(FakeResp(200, json.dumps(_CRYPTIC))))
    assert isinstance(post, XPost)
    assert post.author == "cryptic_heych"
    assert post.author_name == "HeycH"
    assert post.post_ts_utc == "2026-06-30T06:06:15.000Z"
    assert post.text.startswith("Today's statistical analysis")
    assert post.photo_urls == ("https://pbs.twimg.com/media/ABC.jpg?name=orig",)
    assert post.video_present is False
    assert post.is_thread is False and post.is_quote is False


def test_fetch_video_flag() -> None:
    payload = dict(_CRYPTIC, photos=[], mediaDetails=[{"type": "video"}])
    post = fetch_x_post(
        "https://x.com/a/status/9", get=make_get(FakeResp(200, json.dumps(payload)))
    )
    assert isinstance(post, XPost)
    assert post.video_present is True
    assert post.photo_urls == ()


def test_fetch_maps_quoted_tweet() -> None:
    payload = dict(
        _CRYPTIC,
        quoted_tweet={
            "text": "original call: long here",
            "user": {"screen_name": "og_caller", "name": "OG"},
        },
    )
    post = fetch_x_post(
        "https://x.com/a/status/9", get=make_get(FakeResp(200, json.dumps(payload)))
    )
    assert isinstance(post, XPost)
    assert post.is_quote is True
    assert post.quoted_text == "original call: long here"
    assert post.quoted_author == "og_caller"


def test_fetch_no_quoted_tweet_empty_fields() -> None:
    post = fetch_x_post(
        "https://x.com/a/status/9", get=make_get(FakeResp(200, json.dumps(_CRYPTIC)))
    )
    assert isinstance(post, XPost)
    assert post.is_quote is False
    assert post.quoted_text == ""
    assert post.quoted_author == ""


def test_fetch_tombstone() -> None:
    payload = {"__typename": "TweetTombstone"}
    res = fetch_x_post(
        "https://x.com/a/status/9", get=make_get(FakeResp(200, json.dumps(payload)))
    )
    assert isinstance(res, Unavailable)


def test_fetch_http_error() -> None:
    res = fetch_x_post("https://x.com/a/status/9", get=make_get(FakeResp(404)))
    assert isinstance(res, Unavailable)


# ---------------------------------------------------------------------------
# Task 3: download_photos
# ---------------------------------------------------------------------------


def test_download_photos_writes_files(tmp_path: Path) -> None:
    post = XPost(
        source="twitter",
        author="a",
        author_name="A",
        url="u",
        post_ts_utc="t",
        text="x",
        photo_urls=("https://pbs.twimg.com/media/ABC.jpg?name=orig",),
        video_present=False,
        is_thread=False,
        is_quote=False,
    )
    paths = download_photos(
        post, tmp_path, get=make_get(FakeResp(200, content=b"\xff\xd8jpeg"))
    )
    assert paths == [tmp_path / "0.jpg"]
    assert (tmp_path / "0.jpg").read_bytes() == b"\xff\xd8jpeg"


def test_download_photos_skips_errors(tmp_path: Path) -> None:
    post = XPost(
        source="twitter",
        author="a",
        author_name="A",
        url="u",
        post_ts_utc="t",
        text="x",
        photo_urls=("https://pbs.twimg.com/media/ABC.jpg?name=orig",),
        video_present=False,
        is_thread=False,
        is_quote=False,
    )
    paths = download_photos(post, tmp_path, get=make_get(FakeResp(500)))
    assert paths == []


# ---------------------------------------------------------------------------
# Task 4: CLI / _format_human
# ---------------------------------------------------------------------------


def test_format_human() -> None:
    post = XPost(
        source="twitter",
        author="cryptic_heych",
        author_name="HeycH",
        url="u",
        post_ts_utc="2026-06-30T06:06:15.000Z",
        text="Today's analysis $BTC",
        photo_urls=("https://pbs.twimg.com/media/ABC.jpg?name=orig",),
        video_present=False,
        is_thread=False,
        is_quote=False,
    )
    out = _format_human(post)
    assert "@cryptic_heych" in out
    assert "Today's analysis $BTC" in out
    assert "photos: 1" in out
    assert "ABC.jpg?name=orig" in out


def test_format_human_shows_quote() -> None:
    post = XPost(
        source="twitter",
        author="a",
        author_name="A",
        url="u",
        post_ts_utc="t",
        text="new take",
        photo_urls=(),
        video_present=False,
        is_thread=False,
        is_quote=True,
        quoted_text="the original call",
        quoted_author="og_caller",
    )
    out = _format_human(post)
    assert "quoting @og_caller" in out
    assert "the original call" in out


# ---------------------------------------------------------------------------
# Fix robustness: non-dict JSON + missing photo url
# ---------------------------------------------------------------------------


def test_fetch_non_dict_json() -> None:
    res = fetch_x_post(
        "https://x.com/a/status/9",
        get=make_get(FakeResp(200, "[1,2,3]")),
    )
    assert isinstance(res, Unavailable)


def test_orig_strips_existing_query() -> None:
    assert (
        _orig("https://pbs.twimg.com/media/ABC.jpg?format=jpg&name=small")
        == "https://pbs.twimg.com/media/ABC.jpg?name=orig"
    )


# ---------------------------------------------------------------------------
# Batch fetch: cooldown + dedup cache
# ---------------------------------------------------------------------------


class RecordingSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, secs: float) -> None:
        self.calls.append(secs)


def _meta(text: str, photos: list[str] | None = None) -> str:
    payload = dict(
        _CRYPTIC,
        text=text,
        photos=[{"url": u} for u in (photos or [])],
        mediaDetails=[],
    )
    return json.dumps(payload)


def make_routed_get(
    meta_by_id: dict[str, FakeResp],
    photo: FakeResp | None = None,
    calls: list[str] | None = None,
) -> Callable[..., FakeResp]:
    def _get(url: str, *, headers: dict[str, str]) -> FakeResp:
        if calls is not None:
            calls.append(url)
        if "tweet-result" in url:
            m = re.search(r"id=(\d+)", url)
            return meta_by_id.get(m.group(1) if m else "", FakeResp(404))
        return photo or FakeResp(404)

    return _get


def _url(tid: str) -> str:
    return f"https://x.com/a/status/{tid}"


def test_batch_jitters_between_network_fetches_only(tmp_path: Path) -> None:
    get = make_routed_get(
        {"1": FakeResp(200, _meta("one")), "2": FakeResp(200, _meta("two"))}
    )
    sleep = RecordingSleep()
    results = fetch_x_batch(
        [_url("1"), _url("2")],
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        min_delay=3.0,
        max_delay=7.0,
        get=get,
        sleep=sleep,
        rng=random.Random(0),
    )
    assert len(results) == 2
    assert all(isinstance(r, BatchResult) for r in results)
    assert all(isinstance(r.post, XPost) and not r.cached for r in results)
    # exactly one jittered pause — between the two network fetches, never before the first
    assert len(sleep.calls) == 1
    assert 3.0 <= sleep.calls[0] <= 7.0


def test_batch_second_run_hits_cache_no_network_no_sleep(tmp_path: Path) -> None:
    cache_dir, media_root = tmp_path / "posts", tmp_path / "media"
    first = fetch_x_batch(
        [_url("1")],
        cache_dir=cache_dir,
        media_root=media_root,
        get=make_routed_get({"1": FakeResp(200, _meta("hello"))}),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert first[0].cached is False

    calls: list[str] = []
    sleep = RecordingSleep()
    second = fetch_x_batch(
        [_url("1")],
        cache_dir=cache_dir,
        media_root=media_root,
        get=make_routed_get({"1": FakeResp(404)}, calls=calls),  # would 404 if hit
        sleep=sleep,
        rng=random.Random(0),
    )
    assert second[0].cached is True
    assert isinstance(second[0].post, XPost)
    assert second[0].post.text == "hello"
    assert calls == []  # no network at all
    assert sleep.calls == []  # cache hit adds no pause


def test_batch_writes_cache_and_downloads_photos(tmp_path: Path) -> None:
    cache_dir, media_root = tmp_path / "posts", tmp_path / "media"
    get = make_routed_get(
        {
            "5": FakeResp(
                200, _meta("chart", photos=["https://pbs.twimg.com/media/Z.jpg"])
            )
        },
        photo=FakeResp(200, content=b"\xff\xd8img"),
    )
    results = fetch_x_batch(
        [_url("5")],
        cache_dir=cache_dir,
        media_root=media_root,
        get=get,
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert (cache_dir / "5.json").exists()
    downloaded = media_root / "5" / "0.jpg"
    assert downloaded.exists() and downloaded.read_bytes() == b"\xff\xd8img"
    assert results[0].photo_paths == [str(downloaded)]


def test_batch_force_refetches_despite_cache(tmp_path: Path) -> None:
    cache_dir, media_root = tmp_path / "posts", tmp_path / "media"
    fetch_x_batch(
        [_url("1")],
        cache_dir=cache_dir,
        media_root=media_root,
        get=make_routed_get({"1": FakeResp(200, _meta("old"))}),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    calls: list[str] = []
    results = fetch_x_batch(
        [_url("1")],
        cache_dir=cache_dir,
        media_root=media_root,
        force=True,
        get=make_routed_get({"1": FakeResp(200, _meta("new"))}, calls=calls),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert results[0].cached is False
    assert calls  # network was hit
    assert isinstance(results[0].post, XPost) and results[0].post.text == "new"


def test_main_batch_json_emits_array(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    get = make_routed_get(
        {"1": FakeResp(200, _meta("one")), "2": FakeResp(200, _meta("two"))}
    )
    rc = main(
        [
            _url("1"),
            _url("2"),
            "--json",
            "--min-delay",
            "0",
            "--max-delay",
            "0",
            "--cache-dir",
            str(tmp_path / "c"),
            "--media-root",
            str(tmp_path / "m"),
        ],
        get=get,
        sleep=lambda _s: None,
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert isinstance(out, list) and len(out) == 2
    assert out[0]["url"] == _url("1")
    assert out[0]["cached"] is False
    assert out[0]["post"]["text"] == "one"
    assert out[1]["post"]["text"] == "two"


def test_main_single_url_unchanged(capsys: pytest.CaptureFixture[str]) -> None:
    get = make_get(FakeResp(200, _meta("solo")))
    rc = main([_url("1"), "--json"], get=get)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert isinstance(out, dict)  # single-URL path still emits one object, not an array
    assert out["text"] == "solo"


def test_batch_unavailable_not_cached(tmp_path: Path) -> None:
    cache_dir = tmp_path / "posts"
    results = fetch_x_batch(
        [_url("9")],
        cache_dir=cache_dir,
        media_root=tmp_path / "media",
        get=make_routed_get({"9": FakeResp(404)}),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert isinstance(results[0].post, Unavailable)
    assert not (cache_dir / "9.json").exists()


# ---------------------------------------------------------------------------
# Thread walk — recover a self-thread from its tail (spec 2026-08-10)
# ---------------------------------------------------------------------------


def _thread_meta(
    tid: str,
    author: str,
    text: str,
    reply_to: str | None = None,
    reply_to_author: str | None = None,
    conv: int = 0,
    photos: list[str] | None = None,
    quoted_id: str | None = None,
    quoted_photos: list[str] | None = None,
) -> str:
    payload = dict(
        _CRYPTIC,
        text=text,
        photos=[{"url": u} for u in (photos or [])],
        mediaDetails=[],
        id_str=tid,
        user={"name": author.upper(), "screen_name": author},
        conversation_count=conv,
    )
    if reply_to is not None:
        payload["in_reply_to_status_id_str"] = reply_to
        payload["in_reply_to_screen_name"] = reply_to_author or author
        payload["parent"] = {"id_str": reply_to, "text": "parent body"}
    if quoted_id is not None:
        payload["quoted_tweet"] = {
            "id_str": quoted_id,
            "text": f"quoted {quoted_id}",
            "user": {"screen_name": "q", "name": "Q"},
            "photos": [{"url": u} for u in (quoted_photos or [])],
        }
    return json.dumps(payload)


def _ids(calls: list[str]) -> list[str]:
    out = []
    for u in calls:
        m = re.search(r"id=(\d+)", u)
        if "tweet-result" in u and m:
            out.append(m.group(1))
    return out


def _selfthread() -> dict[str, FakeResp]:
    return {
        "3": FakeResp(200, _thread_meta("3", "a", "leaf", reply_to="2")),
        "2": FakeResp(200, _thread_meta("2", "a", "mid", reply_to="1")),
        "1": FakeResp(200, _thread_meta("1", "a", "root", conv=9)),
    }


def test_walk_follows_chain_in_request_order(tmp_path: Path) -> None:
    calls: list[str] = []
    chain = walk_thread(
        _url("3"),
        cache_dir=tmp_path / "posts",
        get=make_routed_get(_selfthread(), calls=calls),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert _ids(calls) == ["3", "2", "1"]  # it really walks, leaf -> root
    assert [p.text for p in chain.posts] == ["root", "mid", "leaf"]  # returned in order
    assert [p.thread_pos for p in chain.posts] == [0, 1, 2]
    assert chain.posts[0].conversation_count == 9


def test_walk_stops_at_root(tmp_path: Path) -> None:
    calls: list[str] = []
    chain = walk_thread(
        _url("1"),
        cache_dir=tmp_path / "posts",
        get=make_routed_get(
            {"1": FakeResp(200, _thread_meta("1", "a", "solo"))}, calls=calls
        ),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert [p.text for p in chain.posts] == ["solo"]
    assert _ids(calls) == ["1"]
    assert chain.notes == []


def test_walk_thread_default_does_not_write_cache(tmp_path: Path) -> None:
    """download=False (the default) must never create the cache dir: cache entries
    carry photo_paths, and a photo-less entry would make a later ingest skip that
    post's chart download. This is a structural guarantee, not a behaviour to
    infer from absence of assertions — a stray future _write_cache call on this
    path must fail a test, not just go unnoticed."""
    cache_dir = tmp_path / "posts"
    walk_thread(
        _url("1"),
        cache_dir=cache_dir,
        get=make_routed_get({"1": FakeResp(200, _thread_meta("1", "a", "solo"))}),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert not cache_dir.exists()


def test_walk_stops_when_author_changes(tmp_path: Path) -> None:
    calls: list[str] = []
    chain = walk_thread(
        _url("2"),
        cache_dir=tmp_path / "posts",
        get=make_routed_get(
            {
                "2": FakeResp(
                    200,
                    _thread_meta("2", "a", "reply", reply_to="1", reply_to_author="b"),
                ),
                "1": FakeResp(200, _thread_meta("1", "b", "someone else")),
            },
            calls=calls,
        ),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert [p.text for p in chain.posts] == ["reply"]
    assert _ids(calls) == ["2"]  # never climbed into @b's post
    assert any("@b" in n for n in chain.notes)


def test_walk_hop_cap_is_reported(tmp_path: Path) -> None:
    metas = {
        str(i): FakeResp(200, _thread_meta(str(i), "a", f"p{i}", reply_to=str(i - 1)))
        for i in range(2, 11)
    }
    metas["1"] = FakeResp(200, _thread_meta("1", "a", "p1"))
    chain = walk_thread(
        _url("10"),
        max_hops=3,
        cache_dir=tmp_path / "posts",
        get=make_routed_get(metas),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert len(chain.posts) == 3
    assert any("max_hops" in n for n in chain.notes)


def test_walk_broken_chain_returns_partial(tmp_path: Path) -> None:
    chain = walk_thread(
        _url("3"),
        cache_dir=tmp_path / "posts",
        get=make_routed_get(
            {"3": FakeResp(200, _thread_meta("3", "a", "leaf", reply_to="2"))}
        ),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert [p.text for p in chain.posts] == ["leaf"]
    assert any("404" in n for n in chain.notes)


def test_walk_reuses_cached_ancestor_without_network(tmp_path: Path) -> None:
    cache_dir = tmp_path / "posts"
    fetch_x_batch(
        [_url("1")],
        cache_dir=cache_dir,
        media_root=tmp_path / "media",
        get=make_routed_get({"1": FakeResp(200, _thread_meta("1", "a", "root"))}),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    calls: list[str] = []
    chain = walk_thread(
        _url("2"),
        cache_dir=cache_dir,
        get=make_routed_get(
            {"2": FakeResp(200, _thread_meta("2", "a", "leaf", reply_to="1"))},
            calls=calls,
        ),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert [p.text for p in chain.posts] == ["root", "leaf"]
    assert _ids(calls) == ["2"]  # ancestor served from cache, no network


def test_walk_sleeps_between_network_hops_only(tmp_path: Path) -> None:
    sleeper = RecordingSleep()
    walk_thread(
        _url("3"),
        cache_dir=tmp_path / "posts",
        get=make_routed_get(_selfthread()),
        sleep=sleeper,
        rng=random.Random(0),
    )
    assert len(sleeper.calls) == 2  # 3 network fetches, none before the first


def test_pre_change_cache_entry_is_now_a_miss(tmp_path: Path) -> None:
    """INVERTED (Task 4b / ruling R8): before schema versioning, a legacy entry —
    written before the thread fields existed — still loaded, with every field the
    entry predates silently defaulted (that was the whole point of this test, at
    the time). Measured 2026-08-15: that is exactly what made 183 of 183 real
    cache entries answer "no quote" / "not truncated" for posts whose live
    payload said otherwise, indistinguishable from posts that genuinely had
    neither. An entry with no ``cache_schema`` key must now be a MISS so the
    post is re-fetched instead of answered wrong."""
    cache_dir = tmp_path / "posts"
    cache_dir.mkdir(parents=True)
    legacy = {
        "post": {
            "source": "twitter",
            "author": "a",
            "author_name": "A",
            "url": _url("1"),
            "post_ts_utc": "2026-08-01T00:00:00.000Z",
            "text": "legacy",
            "photo_urls": [],
            "video_present": False,
            "is_thread": False,
            "is_quote": False,
            "quoted_text": "",
            "quoted_author": "",
        },
        "photo_paths": [],
    }
    (cache_dir / "1.json").write_text(json.dumps(legacy))
    assert _load_cached(cache_dir, "1") is None


def test_stale_cache_schema_version_is_a_miss(tmp_path: Path) -> None:
    """A version key that IS present but does not match the current schema (e.g.
    a future re-versioning) must also read as a miss, not just an absent key."""
    cache_dir = tmp_path / "posts"
    cache_dir.mkdir(parents=True)
    stale = {
        "cache_schema": _CACHE_SCHEMA - 1,
        "post": {
            "source": "twitter",
            "author": "a",
            "author_name": "A",
            "url": _url("1"),
            "post_ts_utc": "2026-08-01T00:00:00.000Z",
            "text": "stale",
            "photo_urls": [],
            "video_present": False,
            "is_thread": False,
            "is_quote": False,
        },
        "photo_paths": [],
    }
    (cache_dir / "1.json").write_text(json.dumps(stale))
    assert _load_cached(cache_dir, "1") is None


def test_write_then_load_cached_round_trips_all_components(tmp_path: Path) -> None:
    """The schema stamp must not break the happy path: a freshly written entry
    loads back with post + photo_paths + quoted_photo_paths intact."""
    cache_dir = tmp_path / "posts"
    post = XPost(
        source="twitter",
        author="a",
        author_name="A",
        url=_url("1"),
        post_ts_utc="2026-08-01T00:00:00.000Z",
        text="fresh",
        photo_urls=("https://pbs.twimg.com/media/A.jpg?name=orig",),
        video_present=False,
        is_thread=False,
        is_quote=True,
        quoted_id="999",
        quoted_photo_urls=("https://pbs.twimg.com/media/Q.jpg?name=orig",),
    )
    _write_cache(cache_dir, "1", post, ["media/1/0.jpg"], ["media/1_quoted/0.jpg"])
    loaded = _load_cached(cache_dir, "1")
    assert loaded is not None
    loaded_post, photo_paths, quoted_photo_paths = loaded
    assert loaded_post == post
    assert photo_paths == ["media/1/0.jpg"]
    assert quoted_photo_paths == ["media/1_quoted/0.jpg"]


def test_batch_refetches_schema_stale_entry_and_recovers_the_quote(
    tmp_path: Path,
) -> None:
    """Pins the actual user-visible defect (ruling R8): a schema-stale cache
    entry for a post whose live payload HAS a quoted post must not be served —
    the batch must re-fetch, and the returned post's quoted_id must be
    non-empty. Before Task 4b this returned the stale entry with
    quoted_id == "", indistinguishable from a post with no quote at all."""
    cache_dir, media_root = tmp_path / "posts", tmp_path / "media"
    cache_dir.mkdir(parents=True)
    stale = {
        "post": {
            "source": "twitter",
            "author": "a",
            "author_name": "A",
            "url": _url("9"),
            "post_ts_utc": "2026-08-01T00:00:00.000Z",
            "text": "old text, pre quoted_id field",
            "photo_urls": [],
            "video_present": False,
            "is_thread": False,
            "is_quote": False,
        },
        "photo_paths": [],
    }
    (cache_dir / "9.json").write_text(json.dumps(stale))

    live_payload = json.dumps(
        dict(
            _CRYPTIC,
            text="new text, has a quote",
            photos=[],
            mediaDetails=[],
            quoted_tweet={
                "id_str": "999888777",
                "text": "the original call",
                "user": {"screen_name": "og_caller", "name": "OG"},
            },
        )
    )
    calls: list[str] = []
    results = fetch_x_batch(
        [_url("9")],
        cache_dir=cache_dir,
        media_root=media_root,
        get=make_routed_get({"9": FakeResp(200, live_payload)}, calls=calls),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert calls  # the stale entry did NOT satisfy the read — network was hit
    assert results[0].cached is False
    assert isinstance(results[0].post, XPost)
    assert results[0].post.quoted_id == "999888777"


def test_main_thread_flag_emits_chain(tmp_path: Path, capsys: Any) -> None:
    rc = main(
        [_url("3"), "--thread", "--json", "--cache-dir", str(tmp_path / "posts")],
        get=make_routed_get(_selfthread()),
        sleep=RecordingSleep(),
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    # one array element per input URL, keyed by that URL — N inputs, N outputs,
    # parseable in one `json.loads` however many URLs were passed
    assert [e["url"] for e in out] == [_url("3")]
    assert [p["text"] for p in out[0]["posts"]] == ["root", "mid", "leaf"]
    assert out[0]["notes"] == []


def test_main_thread_human_shows_recovered_count(tmp_path: Path, capsys: Any) -> None:
    rc = main(
        [_url("3"), "--thread", "--cache-dir", str(tmp_path / "posts")],
        get=make_routed_get(_selfthread()),
        sleep=RecordingSleep(),
    )
    assert rc == 0
    assert "thread: 3 posts" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Task 1: payload-honesty fields — text_truncated, edited (real captured payloads)
# ---------------------------------------------------------------------------

_PAYLOAD_DIR = Path(__file__).parent / "data" / "x_payloads"


def _payload(name: str) -> str:
    return (_PAYLOAD_DIR / f"{name}.json").read_text()


def test_longform_post_flags_text_truncated() -> None:
    get = make_get(FakeResp(200, _payload("truncated_longform")))
    post = fetch_x_post("https://x.com/a/status/2080609907561124004", get=get)
    assert isinstance(post, XPost)
    assert post.text_truncated is True


def test_complete_post_with_image_does_not_flag_truncated() -> None:
    """Regression for spec 3.1.

    display_text_range[1] < len(text) is True for THIS post too, because the
    trailing t.co media link sits outside the display range. A range-based
    implementation passes the test above and fails this one.
    """
    get = make_get(FakeResp(200, _payload("complete_with_image")))
    post = fetch_x_post("https://x.com/a/status/2077667306172236028", get=get)
    assert isinstance(post, XPost)
    assert post.text_truncated is False


def test_edited_post_flags_edited() -> None:
    get = make_get(FakeResp(200, _payload("edited_post")))
    post = fetch_x_post("https://x.com/a/status/2077667306172236028", get=get)
    assert isinstance(post, XPost)
    assert post.edited is True


def test_unedited_post_does_not_flag_edited() -> None:
    get = make_get(FakeResp(200, _payload("complete_with_image")))
    post = fetch_x_post("https://x.com/a/status/2077667306172236028", get=get)
    assert isinstance(post, XPost)
    assert post.edited is False


# ---------------------------------------------------------------------------
# Task 2: quoted-post images and id
# ---------------------------------------------------------------------------


def test_quoted_tweet_photos_and_id_are_parsed() -> None:
    get = make_get(FakeResp(200, _payload("quote_with_image")))
    post = fetch_x_post("https://x.com/a/status/2079877893694320817", get=get)
    assert isinstance(post, XPost)
    assert post.quoted_id == "2062887573790359920"
    assert len(post.quoted_photo_urls) == 1
    assert post.quoted_photo_urls[0].endswith("?name=orig")


def test_quoted_photos_download_to_a_separate_dir(tmp_path: Path) -> None:
    post = XPost(
        source="twitter",
        author="a",
        author_name="A",
        url="https://x.com/a/status/1",
        post_ts_utc="",
        text="t",
        photo_urls=("https://pbs.twimg.com/media/PARENT.jpg?name=orig",),
        video_present=False,
        is_thread=False,
        is_quote=True,
        quoted_photo_urls=("https://pbs.twimg.com/media/QUOTED.jpg?name=orig",),
    )
    get = make_get(FakeResp(200, content=b"bytes"))
    parent = download_photos(post, tmp_path / "1", get=get)
    quoted = download_quoted_photos(post, tmp_path / "1_quoted", get=get)
    assert [p.name for p in parent] == ["0.jpg"]
    assert [p.name for p in quoted] == ["0.jpg"]
    assert parent[0] != quoted[0]


def test_batch_downloads_quoted_photos_and_caches_them(tmp_path: Path) -> None:
    def routed_get(url: str, *, headers: dict[str, str]) -> FakeResp:
        if "syndication" in url:
            return FakeResp(200, _payload("quote_with_image"))
        return FakeResp(200, content=b"img")

    results = fetch_x_batch(
        ["https://x.com/a/status/2079877893694320817"],
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=routed_get,
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    assert len(results[0].quoted_photo_paths) == 1
    assert "_quoted" in results[0].quoted_photo_paths[0]
    # and the cache round-trips it
    again = fetch_x_batch(
        ["https://x.com/a/status/2079877893694320817"],
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=routed_get,
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    assert again[0].cached is True
    assert again[0].quoted_photo_paths == results[0].quoted_photo_paths


# ---------------------------------------------------------------------------
# Task 3: resolve() — the bundle
# ---------------------------------------------------------------------------


def test_resolve_returns_chain_with_roles_and_photos(tmp_path: Path) -> None:
    """A 2-post self-thread: the bookmarked leaf plus its parent, both with images."""
    leaf = _thread_meta(
        text="leaf",
        tid="200",
        reply_to="100",
        author="a",
        photos=["https://pbs.twimg.com/media/LEAF.jpg"],
    )
    root = _thread_meta(
        text="root",
        tid="100",
        reply_to=None,
        author="a",
        photos=["https://pbs.twimg.com/media/ROOT.jpg"],
    )
    bodies = {"200": leaf, "100": root}

    def routed_get(url: str, *, headers: dict[str, str]) -> FakeResp:
        if "syndication" in url:
            tid = re.search(r"id=(\d+)", url).group(1)  # type: ignore[union-attr]
            return FakeResp(200, bodies[tid])
        return FakeResp(200, content=b"img")

    bundle = resolve(
        "https://x.com/a/status/200",
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=routed_get,
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    assert [rp.role for rp in bundle.posts] == ["chain_parent", "bookmarked"]
    # every post in the bundle carries LOCAL paths — not just the bookmarked one
    assert all(rp.photo_paths for rp in bundle.posts)


def test_resolve_pulls_in_quoted_post(tmp_path: Path) -> None:
    main_post = _thread_meta(
        text="main",
        tid="200",
        reply_to=None,
        author="a",
        quoted_id="900",
        # the chain post's OWN embedded quoted-tweet image — carried in the SAME
        # payload as post 200, distinct from post 900's own separately-fetched photos
        quoted_photos=["https://pbs.twimg.com/media/EMBEDDED.jpg"],
    )
    quoted = _thread_meta(text="quoted", tid="900", reply_to=None, author="b")
    bodies = {"200": main_post, "900": quoted}

    def routed_get(url: str, *, headers: dict[str, str]) -> FakeResp:
        if "syndication" in url:
            tid = re.search(r"id=(\d+)", url).group(1)  # type: ignore[union-attr]
            return FakeResp(200, bodies[tid])
        return FakeResp(200, content=b"img")

    bundle = resolve(
        "https://x.com/a/status/200",
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=routed_get,
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    roles = {rp.post.author: rp.role for rp in bundle.posts}
    assert roles == {"a": "bookmarked", "b": "quoted"}
    quoted_rp = next(rp for rp in bundle.posts if rp.role == "quoted")
    assert quoted_rp.referred_by == "200"
    assert quoted_rp.depth == 1
    # the CHAIN post's own embedded quote image must not be dropped on the floor
    bookmarked_rp = next(rp for rp in bundle.posts if rp.role == "bookmarked")
    assert bookmarked_rp.quoted_photo_paths


def test_resolve_terminates_on_a_quote_cycle(tmp_path: Path) -> None:
    a = _thread_meta(text="a", tid="200", reply_to=None, author="a", quoted_id="900")
    b = _thread_meta(text="b", tid="900", reply_to=None, author="b", quoted_id="200")
    bodies = {"200": a, "900": b}

    def routed_get(url: str, *, headers: dict[str, str]) -> FakeResp:
        if "syndication" in url:
            tid = re.search(r"id=(\d+)", url).group(1)  # type: ignore[union-attr]
            return FakeResp(200, bodies[tid])
        return FakeResp(200, content=b"img")

    bundle = resolve(
        "https://x.com/a/status/200",
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=routed_get,
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    ids = [rp.post.url.rsplit("/", 1)[-1] for rp in bundle.posts]
    assert sorted(ids) == ["200", "900"]  # each exactly once
    assert any("already in bundle" in n for n in bundle.notes)


def test_resolve_respects_max_quote_depth(tmp_path: Path) -> None:
    chain = {
        "200": _thread_meta(
            text="a", tid="200", reply_to=None, author="a", quoted_id="300"
        ),
        "300": _thread_meta(
            text="b", tid="300", reply_to=None, author="b", quoted_id="400"
        ),
        "400": _thread_meta(
            text="c", tid="400", reply_to=None, author="c", quoted_id="500"
        ),
        "500": _thread_meta(text="d", tid="500", reply_to=None, author="d"),
    }

    def routed_get(url: str, *, headers: dict[str, str]) -> FakeResp:
        if "syndication" in url:
            tid = re.search(r"id=(\d+)", url).group(1)  # type: ignore[union-attr]
            return FakeResp(200, chain[tid])
        return FakeResp(200, content=b"img")

    bundle = resolve(
        "https://x.com/a/status/200",
        max_quote_depth=2,
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=routed_get,
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    assert [rp.post.author for rp in bundle.posts] == ["a", "b", "c"]
    assert any("max_quote_depth" in n for n in bundle.notes)


def test_resolve_delays_between_quote_hops(tmp_path: Path) -> None:
    """Each quote hop is its own fetch_x_batch call; without delay_first every hop
    after the first looks like an ungated first fetch to that function and the
    randomized cooldown is silently skipped."""
    chain = {
        "200": _thread_meta(
            text="a", tid="200", reply_to=None, author="a", quoted_id="300"
        ),
        "300": _thread_meta(
            text="b", tid="300", reply_to=None, author="b", quoted_id="400"
        ),
        "400": _thread_meta(text="c", tid="400", reply_to=None, author="c"),
    }

    def routed_get(url: str, *, headers: dict[str, str]) -> FakeResp:
        if "syndication" in url:
            tid = re.search(r"id=(\d+)", url).group(1)  # type: ignore[union-attr]
            return FakeResp(200, chain[tid])
        return FakeResp(200, content=b"img")

    sleep = RecordingSleep()
    resolve(
        "https://x.com/a/status/200",
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=routed_get,
        sleep=sleep,
        rng=random.Random(0),
    )
    # today (pre-fix) this reads 0: both quote-hop fetches look like a fresh
    # "first fetch" to fetch_x_batch and skip the cooldown entirely
    assert len(sleep.calls) == 2


def test_resolve_degrades_on_unavailable_quoted_post(tmp_path: Path) -> None:
    """Spec §7: an unavailable quoted post degrades (note appended, bundle still
    returned) rather than aborting the whole resolve() call."""
    main_post = _thread_meta(
        text="main", tid="200", reply_to=None, author="a", quoted_id="900"
    )

    def routed_get(url: str, *, headers: dict[str, str]) -> FakeResp:
        if "syndication" in url:
            tid = re.search(r"id=(\d+)", url).group(1)  # type: ignore[union-attr]
            if tid == "200":
                return FakeResp(200, main_post)
            return FakeResp(404)  # the quoted post is gone
        return FakeResp(200, content=b"img")

    bundle = resolve(
        "https://x.com/a/status/200",
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=routed_get,
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    assert [rp.role for rp in bundle.posts] == ["bookmarked"]  # 900 never made it in
    assert any("900" in n and "unavailable" in n for n in bundle.notes)


# ---------------------------------------------------------------------------
# Task 4: `--resolve` CLI
# ---------------------------------------------------------------------------


def test_main_resolve_json_emits_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = _thread_meta(text="only", tid="200", reply_to=None, author="a")

    def routed_get(url: str, *, headers: dict[str, str]) -> FakeResp:
        if "syndication" in url:
            return FakeResp(200, body)
        return FakeResp(200, content=b"img")

    rc = main(
        [
            "https://x.com/a/status/200",
            "--resolve",
            "--json",
            "--cache-dir",
            str(tmp_path / "posts"),
            "--media-root",
            str(tmp_path / "media"),
            "--min-delay",
            "0",
            "--max-delay",
            "0",
        ],
        get=routed_get,
        sleep=lambda _s: None,
    )
    assert rc == 0
    emitted = json.loads(capsys.readouterr().out)
    assert isinstance(emitted, list) and len(emitted) == 1
    payload = emitted[0]
    assert payload["url"] == "https://x.com/a/status/200"
    post = payload["posts"][0]
    # Task 5 documents this JSON shape as an operator-facing contract — a
    # renamed or typo'd key must fail here rather than reach the docs.
    assert post["role"] == "bookmarked"
    assert post["depth"] == 0
    assert post["referred_by"] == ""
    assert post["photo_paths"] == []
    assert post["quoted_photo_paths"] == []
    assert post["text_truncated"] is False  # a flat XPost field rides along
    assert "notes" in payload


def test_main_resolve_prints_the_unavailability_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Final-review Minor 4. An empty bundle used to `continue` before its notes
    printed, so `stopped at 200: HTTP 404` never reached the operator — while the
    skill asks them to tell protected from deleted from age-gated."""
    rc = main(
        [
            "https://x.com/a/status/200",
            "--resolve",
            "--cache-dir",
            str(tmp_path / "posts"),
            "--media-root",
            str(tmp_path / "media"),
        ],
        get=make_routed_get({"200": FakeResp(404)}),
        sleep=lambda _s: None,
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "UNAVAILABLE" in err
    assert "stopped at 200" in err and "404" in err


def test_main_thread_prints_the_unavailability_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Same defect on the sibling path — fixed on both, per Minor 4."""
    rc = main(
        [
            "https://x.com/a/status/200",
            "--thread",
            "--cache-dir",
            str(tmp_path / "posts"),
        ],
        get=make_routed_get({"200": FakeResp(404)}),
        sleep=lambda _s: None,
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "stopped at 200" in err and "404" in err


def test_main_thread_honours_max_hops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Final-review Important 3. `--max-hops` shipped accepted-and-ignored on the
    `--thread` path (`args.max_hops` was never forwarded), so a 2-hop request
    returned the whole chain with empty notes. The absence of this test is what
    let that ship."""
    metas = {
        str(i): FakeResp(200, _thread_meta(str(i), "a", f"p{i}", reply_to=str(i - 1)))
        for i in range(2, 9)
    }
    metas["1"] = FakeResp(200, _thread_meta("1", "a", "p1"))
    rc = main(
        [
            _url("8"),
            "--thread",
            "--json",
            "--max-hops",
            "2",
            "--cache-dir",
            str(tmp_path / "posts"),
            "--min-delay",
            "0",
            "--max-delay",
            "0",
        ],
        get=make_routed_get(metas),
        sleep=lambda _s: None,
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out[0]["posts"]) == 2
    assert any("max_hops" in n for n in out[0]["notes"])


def test_main_thread_force_refetches_despite_cache(tmp_path: Path) -> None:
    """`--force` was accepted and ignored on the `--thread` path too — the same
    defect as `--resolve --force`, in the same argparse block."""
    cache_dir = tmp_path / "posts"
    fetch_x_batch(
        [_url("1")],
        cache_dir=cache_dir,
        media_root=tmp_path / "media",
        get=make_routed_get({"1": FakeResp(200, _thread_meta("1", "a", "old"))}),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    calls: list[str] = []
    rc = main(
        [
            _url("1"),
            "--thread",
            "--force",
            "--cache-dir",
            str(cache_dir),
            "--min-delay",
            "0",
            "--max-delay",
            "0",
        ],
        get=make_routed_get(
            {"1": FakeResp(200, _thread_meta("1", "a", "new"))}, calls=calls
        ),
        sleep=lambda _s: None,
    )
    assert rc == 0
    assert _ids(calls) == ["1"]  # the cache did not answer it


def test_main_resolve_human_notes_go_to_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mirrors test_main_thread_human_shows_recovered_count. `--thread` sends
    its notes to stderr while post lines stay on stdout; `--resolve` must match
    that split so an operator piping stdout doesn't silently lose notes from
    one flag but not the other."""
    main_post = _thread_meta(
        text="main", tid="200", reply_to=None, author="a", quoted_id="900"
    )

    def routed_get(url: str, *, headers: dict[str, str]) -> FakeResp:
        if "syndication" in url:
            match = re.search(r"id=(\d+)", url)
            tid = match.group(1) if match else ""
            if tid == "200":
                return FakeResp(200, main_post)
            return FakeResp(404)  # the quoted post is gone
        return FakeResp(200, content=b"img")

    rc = main(
        [
            "https://x.com/a/status/200",
            "--resolve",
            "--cache-dir",
            str(tmp_path / "posts"),
            "--media-root",
            str(tmp_path / "media"),
            "--min-delay",
            "0",
            "--max-delay",
            "0",
        ],
        get=routed_get,
        sleep=lambda _s: None,
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "[bookmarked d0]" in captured.out
    assert "! quoted 900 unavailable" in captured.err
    assert "! quoted 900 unavailable" not in captured.out


# ---------------------------------------------------------------------------
# Final review fix wave — Important 2-5, Minors 1-4
# ---------------------------------------------------------------------------


def _bodies_get(
    bodies: dict[str, str],
    *,
    photo_status: dict[str, int] | None = None,
    calls: list[str] | None = None,
) -> Callable[..., FakeResp]:
    """Routes syndication by id and photo GETs by url, with per-url status
    control so a partial media failure is expressible."""

    def _get(url: str, *, headers: dict[str, str]) -> FakeResp:
        if calls is not None:
            calls.append(url)
        if "syndication" in url:
            match = re.search(r"id=(\d+)", url)
            tid = match.group(1) if match else ""
            body = bodies.get(tid)
            return FakeResp(200, body) if body is not None else FakeResp(404)
        status = (photo_status or {}).get(url.split("?", 1)[0], 200)
        return FakeResp(status, content=b"img") if status == 200 else FakeResp(status)

    return _get


def test_resolve_notes_a_quoted_post_whose_own_chain_is_unwalked(
    tmp_path: Path,
) -> None:
    """Important 2. 200 quotes 900, and 900 is itself a reply to 800. The walk
    stops there — spec 5 now reads that row as ⚠, and the bundle must SAY so
    rather than reading as complete."""
    bodies = {
        "200": _thread_meta("200", "a", "main", quoted_id="900"),
        "900": _thread_meta("900", "b", "quoted leaf", reply_to="800"),
    }
    bundle = resolve(
        "https://x.com/a/status/200",
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=_bodies_get(bodies),
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    assert [parse_tweet_id(rp.post.url) for rp in bundle.posts] == ["200", "900"]
    assert any("900" in n and "800" in n and "NOT walked" in n for n in bundle.notes), (
        bundle.notes
    )


def test_resolve_notes_a_quote_with_no_resolvable_id(tmp_path: Path) -> None:
    """Important 2. A tombstoned `quoted_tweet` yields is_quote=True with an empty
    quoted_id: nothing queues it and, before this fix, nothing noted it — the quote
    vanished entirely."""
    payload = json.dumps(
        dict(
            _CRYPTIC,
            text="main",
            photos=[],
            mediaDetails=[],
            id_str="200",
            user={"screen_name": "a", "name": "A"},
            quoted_tweet={"text": "", "user": {}},
        )
    )
    bundle = resolve(
        "https://x.com/a/status/200",
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=_bodies_get({"200": payload}),
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    assert len(bundle.posts) == 1
    assert any("200" in n and "no id" in n for n in bundle.notes), bundle.notes


def test_resolve_force_refetches_despite_cache(tmp_path: Path) -> None:
    """Important 3. `resolve` took no `force`, so `--resolve --force` performed 0
    network calls and returned stale cached text — with the cache now authoritative
    (schema versioning) and `edited: true` being exactly the case you want to
    re-fetch, `rm -rf .cache/x-posts` was the only escape hatch."""
    cache_dir, media_root = tmp_path / "posts", tmp_path / "media"
    resolve(
        "https://x.com/a/status/200",
        cache_dir=cache_dir,
        media_root=media_root,
        get=_bodies_get({"200": _thread_meta("200", "a", "old")}),
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    calls: list[str] = []
    bundle = resolve(
        "https://x.com/a/status/200",
        force=True,
        cache_dir=cache_dir,
        media_root=media_root,
        get=_bodies_get({"200": _thread_meta("200", "a", "new")}, calls=calls),
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    assert _ids(calls) == ["200"]
    assert bundle.posts[0].post.text == "new"


def test_resolve_force_refetches_a_quoted_post_too(tmp_path: Path) -> None:
    """`force` must reach the quote hop as well, not just the chain walk —
    otherwise the flag is half-wired and the stale half is invisible."""
    cache_dir, media_root = tmp_path / "posts", tmp_path / "media"
    bodies = {
        "200": _thread_meta("200", "a", "main", quoted_id="900"),
        "900": _thread_meta("900", "b", "old quote"),
    }
    resolve(
        "https://x.com/a/status/200",
        cache_dir=cache_dir,
        media_root=media_root,
        get=_bodies_get(bodies),
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    calls: list[str] = []
    bundle = resolve(
        "https://x.com/a/status/200",
        force=True,
        cache_dir=cache_dir,
        media_root=media_root,
        get=_bodies_get(
            {**bodies, "900": _thread_meta("900", "b", "new quote")}, calls=calls
        ),
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    assert _ids(calls) == ["200", "900"]
    quoted = next(rp for rp in bundle.posts if rp.role == "quoted")
    assert quoted.post.text == "new quote"


def test_resolve_notes_a_failed_image_download(tmp_path: Path) -> None:
    """Important 5. One of two photo GETs fails: before this fix the bundle
    carried one path, emitted no note, and the entry cached as complete — so the
    lost chart never retried and nothing said it was lost."""
    cache_dir, media_root = tmp_path / "posts", tmp_path / "media"
    bodies = {
        "200": _thread_meta(
            "200",
            "a",
            "two charts",
            photos=[
                "https://pbs.twimg.com/media/A.jpg",
                "https://pbs.twimg.com/media/B.jpg",
            ],
        )
    }
    get = _bodies_get(bodies, photo_status={"https://pbs.twimg.com/media/B.jpg": 429})
    bundle = resolve(
        "https://x.com/a/status/200",
        cache_dir=cache_dir,
        media_root=media_root,
        get=get,
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    assert len(bundle.posts[0].photo_paths) == 1
    assert any("1 of 2 images failed" in n for n in bundle.notes), bundle.notes

    # and the shortfall stays visible on the cached re-run, where the retry never
    # happens at all
    again = resolve(
        "https://x.com/a/status/200",
        cache_dir=cache_dir,
        media_root=media_root,
        get=get,
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    assert any("1 of 2 images failed" in n for n in again.notes), again.notes


def test_resolve_notes_a_failed_quoted_image_download(tmp_path: Path) -> None:
    """The same shortfall on the quote hop's own fetch — `fetch_x_batch` is where
    those images are downloaded, so the note has to originate there and be carried
    into the bundle."""
    bodies = {
        "200": _thread_meta("200", "a", "main", quoted_id="900"),
        "900": _thread_meta(
            "900", "b", "quoted", photos=["https://pbs.twimg.com/media/Q.jpg"]
        ),
    }
    bundle = resolve(
        "https://x.com/a/status/200",
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=_bodies_get(
            bodies, photo_status={"https://pbs.twimg.com/media/Q.jpg": 429}
        ),
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    assert any("1 of 1 images failed" in n and "900" in n for n in bundle.notes), (
        bundle.notes
    )


def test_resolve_gives_a_quoted_post_its_canonical_url(tmp_path: Path) -> None:
    """Minor 1. A quote hop addresses the post as x.com/i/status/<id> because it
    has no handle until the payload answers. The bundle — and the cache entry a
    later canonical fetch reads — must carry the real one: this is a pipeline about
    attribution."""
    cache_dir = tmp_path / "posts"
    bodies = {
        "200": _thread_meta("200", "a", "main", quoted_id="900"),
        "900": _thread_meta("900", "bee", "quoted"),
    }
    bundle = resolve(
        "https://x.com/a/status/200",
        cache_dir=cache_dir,
        media_root=tmp_path / "media",
        get=_bodies_get(bodies),
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    quoted = next(rp for rp in bundle.posts if rp.role == "quoted")
    assert quoted.post.url == "https://x.com/bee/status/900"
    cached = _load_cached(cache_dir, "900")
    assert cached is not None and cached[0].url == "https://x.com/bee/status/900"


def test_resolve_downloads_a_shared_quoted_image_once(tmp_path: Path) -> None:
    """Minor 2. The referrer's embedded copy of its quoted post's photos and the
    quoted post's own photos are the SAME image. Downloading both cost 2 GETs for
    1 url and put the same chart under two ResolvedPosts, so two vision subagents
    read it and the digest could double-count it."""
    shared = "https://pbs.twimg.com/media/SHARED.jpg"
    bodies = {
        "200": _thread_meta(
            "200", "a", "main", quoted_id="900", quoted_photos=[shared]
        ),
        "900": _thread_meta("900", "b", "quoted", photos=[shared]),
    }
    calls: list[str] = []
    bundle = resolve(
        "https://x.com/a/status/200",
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=_bodies_get(bodies, calls=calls),
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    assert len([c for c in calls if "SHARED" in c]) == 1
    quoted = next(rp for rp in bundle.posts if rp.role == "quoted")
    bookmarked = next(rp for rp in bundle.posts if rp.role == "bookmarked")
    assert len(quoted.photo_paths) == 1  # it lives with the post that owns it
    assert bookmarked.quoted_photo_paths == []  # and is not listed twice


def test_resolve_keeps_an_embedded_quote_image_the_quoted_post_lacks(
    tmp_path: Path,
) -> None:
    """The Minor 2 de-duplication is keyed on the image URLS matching. When they
    do not — the embedded copy carries a chart the quoted post's own payload does
    not — dropping it would delete the only copy of that evidence."""
    bodies = {
        "200": _thread_meta(
            "200",
            "a",
            "main",
            quoted_id="900",
            quoted_photos=["https://pbs.twimg.com/media/EMBEDDED.jpg"],
        ),
        "900": _thread_meta("900", "b", "quoted"),
    }
    bundle = resolve(
        "https://x.com/a/status/200",
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=_bodies_get(bodies),
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    bookmarked = next(rp for rp in bundle.posts if rp.role == "bookmarked")
    assert bookmarked.quoted_photo_paths


def test_resolve_does_not_reuse_a_partial_embedded_copy(tmp_path: Path) -> None:
    """The Minor 2 reuse is skipped when the referrer's embedded copy is
    INCOMPLETE. Reusing a short list would turn one failed GET on the referrer
    into a permanent loss on the quoted post too, where the fresh download it
    would have done is a free retry."""
    urls = ["https://pbs.twimg.com/media/A.jpg", "https://pbs.twimg.com/media/B.jpg"]
    bodies = {
        "200": _thread_meta("200", "a", "main", quoted_id="900", quoted_photos=urls),
        "900": _thread_meta("900", "b", "quoted", photos=urls),
    }
    seen_b = {"n": 0}

    def flaky_get(url: str, *, headers: dict[str, str]) -> FakeResp:
        if "syndication" in url:
            match = re.search(r"id=(\d+)", url)
            return FakeResp(200, bodies[match.group(1) if match else ""])
        if "B.jpg" in url:
            seen_b["n"] += 1
            if seen_b["n"] == 1:  # fails once, on the referrer's embedded copy
                return FakeResp(429)
        return FakeResp(200, content=b"img")

    bundle = resolve(
        "https://x.com/a/status/200",
        cache_dir=tmp_path / "posts",
        media_root=tmp_path / "media",
        get=flaky_get,
        sleep=lambda _s: None,
        rng=random.Random(0),
    )
    quoted = next(rp for rp in bundle.posts if rp.role == "quoted")
    assert len(quoted.photo_paths) == 2  # downloaded fresh, so B was recovered
    assert any("1 of 2 quoted-post images failed" in n for n in bundle.notes)


def test_no_empty_quoted_dir_for_a_post_without_a_quote(tmp_path: Path) -> None:
    """Minor 3. `download_quoted_photos` ran unconditionally, so every post
    created an empty `{id}_quoted/`."""
    media_root = tmp_path / "media"
    fetch_x_batch(
        [_url("5")],
        cache_dir=tmp_path / "posts",
        media_root=media_root,
        get=make_routed_get({"5": FakeResp(200, _meta("no quote here"))}),
        sleep=RecordingSleep(),
        rng=random.Random(0),
    )
    assert not (media_root / "5_quoted").exists()


def test_main_resolve_json_multi_url_emits_one_array(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Important 4. Two URLs produced two concatenated top-level JSON objects and
    `json.loads` rejected the lot with `Extra data:` — while SKILL.md step 1
    mandates exactly this invocation with every pasted URL."""
    bodies = {
        "200": _thread_meta("200", "a", "first"),
        "300": _thread_meta("300", "b", "second"),
    }
    rc = main(
        [
            "https://x.com/a/status/200",
            "https://x.com/b/status/300",
            "--resolve",
            "--json",
            "--cache-dir",
            str(tmp_path / "posts"),
            "--media-root",
            str(tmp_path / "media"),
            "--min-delay",
            "0",
            "--max-delay",
            "0",
        ],
        get=_bodies_get(bodies),
        sleep=lambda _s: None,
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert isinstance(out, list) and len(out) == 2
    assert out[0]["url"] == "https://x.com/a/status/200"
    assert out[1]["url"] == "https://x.com/b/status/300"
    assert out[0]["posts"][0]["text"] == "first"
    assert out[1]["posts"][0]["text"] == "second"


def test_main_resolve_json_keeps_an_unavailable_url_in_the_array(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Important 4's correlation half: an unavailable URL printed nothing, so N
    inputs yielded N-1 outputs with no key tying a bundle back to its input."""
    rc = main(
        [
            "https://x.com/a/status/200",
            "https://x.com/b/status/300",
            "--resolve",
            "--json",
            "--cache-dir",
            str(tmp_path / "posts"),
            "--media-root",
            str(tmp_path / "media"),
            "--min-delay",
            "0",
            "--max-delay",
            "0",
        ],
        get=_bodies_get({"200": _thread_meta("200", "a", "first")}),
        sleep=lambda _s: None,
    )
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert [e["url"] for e in out] == [
        "https://x.com/a/status/200",
        "https://x.com/b/status/300",
    ]
    assert out[1]["posts"] == []
    assert any("300" in n for n in out[1]["notes"])


def test_main_resolve_force_reaches_the_network(tmp_path: Path) -> None:
    """Important 3, at the CLI boundary: `--resolve --force` performed 0 network
    calls."""
    cache_dir, media_root = tmp_path / "posts", tmp_path / "media"
    args = [
        "https://x.com/a/status/200",
        "--resolve",
        "--cache-dir",
        str(cache_dir),
        "--media-root",
        str(media_root),
        "--min-delay",
        "0",
        "--max-delay",
        "0",
    ]
    main(
        args,
        get=_bodies_get({"200": _thread_meta("200", "a", "old")}),
        sleep=lambda _s: None,
    )
    calls: list[str] = []
    rc = main(
        [*args, "--force"],
        get=_bodies_get({"200": _thread_meta("200", "a", "new")}, calls=calls),
        sleep=lambda _s: None,
    )
    assert rc == 0
    assert _ids(calls) == ["200"]


# ---------------------------------------------------------------------------
# ST102 — numeric account ids. Handles are mutable, ids are permanent.
# ---------------------------------------------------------------------------


def test_fetch_persists_every_account_id_the_payload_carries() -> None:
    """The payload carries user.id_str beside every screen_name, plus a
    top-level in_reply_to_user_id_str. This parser dropped all three, so the
    follow-list roster had to be rebuilt from a paid archive instead of
    maintaining itself off calls already being made."""
    payload = json.dumps(
        dict(
            _CRYPTIC,
            user={"name": "HeycH", "screen_name": "cryptic_heych", "id_str": "111"},
            in_reply_to_status_id_str="900",
            in_reply_to_screen_name="someone_else",
            in_reply_to_user_id_str="222",
            quoted_tweet={
                "id_str": "800",
                "text": "the original call",
                "user": {"screen_name": "og_caller", "name": "OG", "id_str": "333"},
            },
        )
    )
    post = fetch_x_post(_url("1"), get=make_get(FakeResp(200, payload)))
    assert isinstance(post, XPost)
    assert post.author_id == "111"
    assert post.in_reply_to_author_id == "222"
    assert post.quoted_author_id == "333"


def test_absent_account_id_reads_empty_rather_than_invented() -> None:
    """An id the payload does not carry stays empty. A follow-list diff keys on
    account_id precisely because it survives a rename, so a value derived from
    the handle there would corrupt the only field that can detect one."""
    post = fetch_x_post(_url("1"), get=make_get(FakeResp(200, json.dumps(_CRYPTIC))))
    assert isinstance(post, XPost)
    assert post.author_id == ""
    assert post.quoted_author_id == ""
    assert post.in_reply_to_author_id == ""


def test_account_ids_survive_the_cache_round_trip(tmp_path: Path) -> None:
    """A cached read must carry the ids too — the cache is what a later roster
    pass reads, and an id present only on the network path would resolve an
    author once and lose him on every re-read."""
    cache_dir, media_root = tmp_path / "posts", tmp_path / "media"
    payload = json.dumps(
        dict(
            _CRYPTIC,
            photos=[],
            mediaDetails=[],
            user={"name": "HeycH", "screen_name": "cryptic_heych", "id_str": "111"},
        )
    )
    common: dict[str, Any] = {
        "cache_dir": cache_dir,
        "media_root": media_root,
        "sleep": RecordingSleep(),
        "rng": random.Random(0),
    }
    first = fetch_x_batch(
        [_url("1")], get=make_routed_get({"1": FakeResp(200, payload)}), **common
    )
    assert isinstance(first[0].post, XPost) and first[0].post.author_id == "111"

    calls: list[str] = []
    second = fetch_x_batch([_url("1")], get=make_routed_get({}, calls=calls), **common)
    assert not calls  # served from cache — no network
    assert second[0].cached is True
    assert isinstance(second[0].post, XPost)
    assert second[0].post.author_id == "111"


def test_xpost_field_set_is_pinned_to_the_cache_schema() -> None:
    """The bump rule is PROSE on _CACHE_SCHEMA and prose is not a guarantee: a
    field added without a bump is served from every pre-existing cache entry
    silently defaulted, which is ruling R8's defect exactly. Adding or removing
    a field here is meant to fail this test — bump _CACHE_SCHEMA in the same
    edit, then update the set below."""
    assert {f.name for f in fields(XPost)} == {
        "source",
        "author",
        "author_name",
        "url",
        "post_ts_utc",
        "text",
        "photo_urls",
        "video_present",
        "is_thread",
        "is_quote",
        "quoted_text",
        "quoted_author",
        "quoted_id",
        "quoted_photo_urls",
        "text_truncated",
        "edited",
        "in_reply_to_id",
        "in_reply_to_author",
        "conversation_count",
        "thread_pos",
        "author_id",
        "quoted_author_id",
        "in_reply_to_author_id",
    }

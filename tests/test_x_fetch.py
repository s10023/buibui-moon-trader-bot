"""Tests for tools/x_fetch.py — strict TDD, no real network calls."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from tools.x_fetch import (
    Unavailable,
    XPost,
    _format_human,
    download_photos,
    fetch_x_post,
    parse_tweet_id,
)


@dataclass
class FakeResp:
    status_code: int
    text: str = ""
    content: bytes = b""


def make_get(resp: FakeResp):  # type: ignore[no-untyped-def]
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

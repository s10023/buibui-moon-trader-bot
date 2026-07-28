"""Tests for tools/video_fetch.py — no real network, no real subprocesses."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from tools.video_fetch import (
    Unavailable,
    VideoMeta,
    fetch_meta,
    parse_video_url,
)

YT_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
X_URL = "https://x.com/someone/status/1234567890"

YTDLP_JSON = json.dumps(
    {
        "id": "dQw4w9WgXcQ",
        "uploader_id": "@cryptoTrader",
        "title": "BTC weekly outlook",
        "timestamp": 1785247200,
        "duration": 2280,
        "language": "zh",
    }
)


@dataclass
class FakeProc:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def make_run(proc: FakeProc) -> Callable[..., FakeProc]:
    def _run(cmd: list[str]) -> FakeProc:
        return proc

    return _run


def test_parse_video_url_youtube_watch() -> None:
    assert parse_video_url(YT_URL) == ("youtube", "dQw4w9WgXcQ")


def test_parse_video_url_youtube_short() -> None:
    assert parse_video_url("https://youtu.be/dQw4w9WgXcQ") == ("youtube", "dQw4w9WgXcQ")


def test_parse_video_url_x() -> None:
    assert parse_video_url(X_URL) == ("x-video", "1234567890")


def test_parse_video_url_rejects_unknown_host() -> None:
    with pytest.raises(ValueError, match="not a supported video URL"):
        parse_video_url("https://example.com/video/1")


def test_fetch_meta_maps_ytdlp_json() -> None:
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, YTDLP_JSON)))
    assert isinstance(meta, VideoMeta)
    assert meta.source == "youtube"
    assert meta.video_id == "dQw4w9WgXcQ"
    assert meta.author == "@cryptoTrader"
    assert meta.duration_s == 2280.0
    assert meta.lang == "zh"
    assert meta.publish_ts_utc.startswith("2026-")


def test_fetch_meta_unavailable_on_nonzero_exit() -> None:
    got = fetch_meta(YT_URL, run=make_run(FakeProc(1, "", "Private video")))
    assert isinstance(got, Unavailable)
    assert "Private video" in got.reason


def test_fetch_meta_unavailable_on_bad_json() -> None:
    got = fetch_meta(YT_URL, run=make_run(FakeProc(0, "not json")))
    assert isinstance(got, Unavailable)
    assert "JSON" in got.reason

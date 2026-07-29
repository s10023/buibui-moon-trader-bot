"""Tests for tools/video_fetch.py — no real network, no real subprocesses."""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from tools.video_fetch import (
    GROQ_MAX_BYTES,
    Unavailable,
    VideoMeta,
    _result_to_dict,
    extract_frames,
    fetch_meta,
    fetch_transcript,
    fetch_video_batch,
    parse_video_url,
    parse_vtt,
    split_audio,
)
from tools.video_marks import FrameMark, TranscriptSegment

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


def test_fetch_meta_unavailable_on_non_numeric_duration() -> None:
    payload = json.dumps({**json.loads(YTDLP_JSON), "duration": "N/A"})
    got = fetch_meta(YT_URL, run=make_run(FakeProc(0, payload)))
    assert isinstance(got, Unavailable)
    assert "duration" in got.reason


def test_fetch_meta_unavailable_on_out_of_range_timestamp() -> None:
    payload = json.dumps({**json.loads(YTDLP_JSON), "timestamp": 1e20})
    got = fetch_meta(YT_URL, run=make_run(FakeProc(0, payload)))
    assert isinstance(got, Unavailable)
    assert "timestamp" in got.reason


def test_fetch_meta_missing_timestamp_yields_empty_string_not_now() -> None:
    raw = json.loads(YTDLP_JSON)
    del raw["timestamp"]
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, json.dumps(raw))))
    assert isinstance(meta, VideoMeta)
    assert meta.publish_ts_utc == ""


def test_fetch_meta_bool_timestamp_treated_as_absent() -> None:
    payload = json.dumps({**json.loads(YTDLP_JSON), "timestamp": True})
    meta = fetch_meta(YT_URL, run=make_run(FakeProc(0, payload)))
    assert isinstance(meta, VideoMeta)
    assert meta.publish_ts_utc == ""


VTT = """WEBVTT

00:00:04.000 --> 00:00:07.000
大盘很安静

00:00:10.500 --> 00:00:13.000
我在这里做多
"""


def test_parse_vtt_maps_cues_to_segments() -> None:
    segments = parse_vtt(VTT, lang="zh")
    assert segments == [
        TranscriptSegment(ts_s=4.0, text="大盘很安静", lang="zh"),
        TranscriptSegment(ts_s=10.5, text="我在这里做多", lang="zh"),
    ]


def test_parse_vtt_ignores_header_and_blank_lines() -> None:
    assert parse_vtt("WEBVTT\n\n\n", lang="en") == []


def _meta() -> VideoMeta:
    return VideoMeta(
        source="youtube",
        video_id="dQw4w9WgXcQ",
        author="@cryptoTrader",
        title="BTC weekly outlook",
        publish_ts_utc="2026-07-28T14:00:00+00:00",
        duration_s=2280.0,
        lang="zh",
        url=YT_URL,
    )


def test_fetch_transcript_prefers_captions(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def _run(cmd: list[str]) -> FakeProc:
        captured.append(cmd)
        (tmp_path / "sub.zh.vtt").write_text(VTT)
        return FakeProc(0, "")

    segments = fetch_transcript(_meta(), run=_run, work_dir=tmp_path)
    assert isinstance(segments, list)
    assert segments[1].text == "我在这里做多"
    assert not any("whisper" in " ".join(c) for c in captured)


def test_fetch_transcript_unavailable_without_captions_or_key(tmp_path: Path) -> None:
    got = fetch_transcript(_meta(), run=make_run(FakeProc(0, "")), work_dir=tmp_path)
    assert isinstance(got, Unavailable)
    assert "no captions" in got.reason.lower()


def test_split_audio_returns_single_chunk_when_small(tmp_path: Path) -> None:
    audio = tmp_path / "a.opus"
    audio.write_bytes(b"x" * 1024)
    chunks = split_audio(audio, 600.0, run=make_run(FakeProc(0)))
    assert chunks == [(audio, 0.0)]


def test_split_audio_splits_oversized_and_carries_offsets(tmp_path: Path) -> None:
    audio = tmp_path / "a.opus"
    audio.write_bytes(b"x" * (GROQ_MAX_BYTES * 2 + 1))
    calls: list[list[str]] = []

    def _run(cmd: list[str]) -> FakeProc:
        calls.append(cmd)
        return FakeProc(0)

    chunks = split_audio(audio, 900.0, run=_run)
    assert len(chunks) == 3
    assert [round(offset, 1) for _, offset in chunks] == [0.0, 300.0, 600.0]
    assert all(c[0] == "ffmpeg" for c in calls)


def test_split_audio_drops_chunks_ffmpeg_failed_on(tmp_path: Path) -> None:
    audio = tmp_path / "a.opus"
    audio.write_bytes(b"x" * (GROQ_MAX_BYTES * 2 + 1))
    chunks = split_audio(audio, 900.0, run=make_run(FakeProc(1)))
    assert chunks == []


MULTILINE_VTT = """WEBVTT

00:00:04.000 --> 00:00:07.000
this is the first line
and this is the second

00:00:10.500 --> 00:00:13.000
single line cue
"""


def test_parse_vtt_joins_multi_line_cues() -> None:
    assert parse_vtt(MULTILINE_VTT, lang="en") == [
        TranscriptSegment(
            ts_s=4.0, text="this is the first line and this is the second", lang="en"
        ),
        TranscriptSegment(ts_s=10.5, text="single line cue", lang="en"),
    ]


def test_parse_vtt_joins_cue_without_trailing_blank_line() -> None:
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nline one\nline two\n"
    assert parse_vtt(vtt, lang="en") == [
        TranscriptSegment(ts_s=1.0, text="line one line two", lang="en")
    ]


def test_parse_vtt_ignores_sequence_identifier_lines() -> None:
    vtt = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\nhello\n"
    assert parse_vtt(vtt, lang="en") == [
        TranscriptSegment(ts_s=1.0, text="hello", lang="en")
    ]


def test_split_audio_refuses_to_chunk_without_a_duration(tmp_path: Path) -> None:
    audio = tmp_path / "a.opus"
    audio.write_bytes(b"x" * (GROQ_MAX_BYTES * 2 + 1))
    assert split_audio(audio, 0.0, run=make_run(FakeProc(0))) == []


def test_split_audio_small_file_ignores_missing_duration(tmp_path: Path) -> None:
    audio = tmp_path / "a.opus"
    audio.write_bytes(b"x" * 1024)
    assert split_audio(audio, 0.0, run=make_run(FakeProc(0))) == [(audio, 0.0)]


# ---------------------------------------------------------------------------
# Self-review regression: fetch_transcript must degrade, not raise (see
# "Correctness notes" in the task-4 brief) even when Groq's own response is
# malformed — a later task calls this inside a batch loop that must survive
# one bad video.
# ---------------------------------------------------------------------------


@dataclass
class FakeHttpResp:
    status_code: int
    text: str = ""


def test_fetch_transcript_groq_skips_malformed_segments_without_raising(
    tmp_path: Path,
) -> None:
    def _run(cmd: list[str]) -> FakeProc:
        if "-x" in cmd:  # the audio-extraction yt-dlp call
            out = Path(cmd[cmd.index("-o") + 1])
            out.write_bytes(b"x" * 1024)
        return FakeProc(0, "")

    payload = json.dumps(
        {
            "language": "en",
            "segments": [
                {"start": 1.0, "text": "good segment"},
                {"start": "not-a-number", "text": "non-numeric start"},
                {"text": "missing start key entirely"},
                {"start": 2.0},
                {"start": 3.0, "text": "   "},
            ],
        }
    )

    def _get(
        url: str,
        *,
        headers: dict[str, str],
        files: dict[str, object],
        data: dict[str, str],
    ) -> FakeHttpResp:
        return FakeHttpResp(200, payload)

    segments = fetch_transcript(
        _meta(), run=_run, get=_get, groq_key="fake-key", work_dir=tmp_path
    )
    assert segments == [TranscriptSegment(ts_s=1.0, text="good segment", lang="en")]


# ---------------------------------------------------------------------------
# Task 5: extract_frames, dedup cache, batch fetch, CLI
# ---------------------------------------------------------------------------


def test_extract_frames_one_ffmpeg_call_per_mark(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def _run(cmd: list[str]) -> FakeProc:
        calls.append(cmd)
        return FakeProc(0)

    marks = [FrameMark(20.0, "item", 3), FrameMark(90.0, "deixis", 2)]
    paths = extract_frames(_meta(), marks, tmp_path, run=_run)
    assert len(calls) == 2
    assert all(c[0] == "ffmpeg" for c in calls)
    assert [Path(p).name for p in paths] == ["f_0020.jpg", "f_0090.jpg"]


def test_extract_frames_skips_failed_grabs(tmp_path: Path) -> None:
    paths = extract_frames(
        _meta(), [FrameMark(20.0, "item", 3)], tmp_path, run=make_run(FakeProc(1))
    )
    assert paths == []


# fetch_video_batch passes work_dir=cache_dir/<video_id> to fetch_transcript, so a fake
# that writes captions to tmp_path itself would be globbed for in the wrong directory.
# Derive the location from yt-dlp's own -o argument, which is how yt-dlp names sub files.
def make_ytdlp_run(
    calls: list[list[str]] | None = None, fail_substr: str | None = None
) -> Callable[..., FakeProc]:
    def _run(cmd: list[str]) -> FakeProc:
        if calls is not None:
            calls.append(cmd)
        joined = " ".join(cmd)
        if fail_substr is not None and fail_substr in joined:
            return FakeProc(1, "", "Private video")
        if "--dump-json" in cmd:
            return FakeProc(0, YTDLP_JSON)
        if "-o" in cmd:
            out = Path(cmd[cmd.index("-o") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.with_name(f"{out.name}.zh.vtt").write_text(VTT, encoding="utf-8")
        return FakeProc(0)

    return _run


def test_batch_second_network_fetch_sleeps_first_does_not(tmp_path: Path) -> None:
    slept: list[float] = []
    fetch_video_batch(
        [YT_URL, "https://youtu.be/AAAAAAAAAAA"],
        cache_dir=tmp_path,
        run=make_ytdlp_run(),
        sleep=slept.append,
        rng=random.Random(0),
    )
    assert len(slept) == 1


def test_batch_cache_hit_does_no_network_and_no_sleep(tmp_path: Path) -> None:
    slept: list[float] = []
    calls: list[list[str]] = []
    run = make_ytdlp_run(calls)

    first_run = fetch_video_batch(
        [YT_URL], cache_dir=tmp_path, run=run, sleep=slept.append
    )
    assert first_run[0].cached is False
    calls_after_first = len(calls)

    results = fetch_video_batch(
        [YT_URL], cache_dir=tmp_path, run=run, sleep=slept.append
    )
    assert len(calls) == calls_after_first
    assert results[0].cached is True
    assert results[0].segments[1].text == "我在这里做多"
    assert slept == []


def test_batch_isolates_one_bad_video(tmp_path: Path) -> None:
    results = fetch_video_batch(
        ["https://youtu.be/BBBBBBBBBBB", YT_URL],
        cache_dir=tmp_path,
        run=make_ytdlp_run(fail_substr="BBBBBBBBBBB"),
    )
    assert isinstance(results[0].meta, Unavailable)
    assert isinstance(results[1].meta, VideoMeta)


# ---------------------------------------------------------------------------
# Fix round 1: cache-hit URL must be the URL actually requested at that
# position (not whichever URL first populated the video_id), and a
# transcript-only failure must not discard the already-fetched VideoMeta.
# ---------------------------------------------------------------------------


def test_cache_hit_returns_the_url_actually_requested(tmp_path: Path) -> None:
    run = make_ytdlp_run()
    fetch_video_batch(["https://youtu.be/dQw4w9WgXcQ"], cache_dir=tmp_path, run=run)
    results = fetch_video_batch([YT_URL], cache_dir=tmp_path, run=run)
    assert results[0].cached is True
    assert results[0].url == YT_URL


def test_transcript_failure_keeps_meta_and_reports_separately(tmp_path: Path) -> None:
    def _run(cmd: list[str]) -> FakeProc:
        if "--dump-json" in cmd:
            return FakeProc(0, YTDLP_JSON)
        return FakeProc(0)  # no captions written, no groq key configured

    results = fetch_video_batch([YT_URL], cache_dir=tmp_path, run=_run)
    assert isinstance(results[0].meta, VideoMeta)
    assert results[0].meta.author == "@cryptoTrader"
    assert results[0].transcript_error != ""
    assert results[0].segments == []


def test_result_to_dict_surfaces_transcript_error_as_unavailable(
    tmp_path: Path,
) -> None:
    def _run(cmd: list[str]) -> FakeProc:
        if "--dump-json" in cmd:
            return FakeProc(0, YTDLP_JSON)
        return FakeProc(0)

    results = fetch_video_batch([YT_URL], cache_dir=tmp_path, run=_run)
    payload = _result_to_dict(results[0])
    assert payload["unavailable"]
    assert payload["meta"] is not None

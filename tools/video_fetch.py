"""Fetch, transcribe, and frame-extract videos for /ingest-video.

Read-only over the public internet. yt-dlp for metadata + captions, ffmpeg for frames,
Groq whisper-large-v3 only when a video has no captions. `run` (subprocess), `get`
(HTTP), `sleep` and `rng` are injected so the test suite never touches the network —
same contract as tools/x_fetch.py.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

_YT_RE = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})")
_X_RE = re.compile(r"(?:twitter|x)\.com/[^/]+/status/(\d+)")


class Completedish(Protocol):
    returncode: int
    stdout: str
    stderr: str


class RunProc(Protocol):
    def __call__(self, cmd: list[str]) -> Completedish: ...


def _subprocess_run(cmd: list[str]) -> Completedish:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)


@dataclass(frozen=True)
class VideoMeta:
    source: str
    video_id: str
    author: str
    title: str
    publish_ts_utc: str
    duration_s: float
    lang: str
    url: str


@dataclass(frozen=True)
class Unavailable:
    reason: str


def parse_video_url(url: str) -> tuple[str, str]:
    match = _YT_RE.search(url)
    if match:
        return "youtube", match.group(1)
    match = _X_RE.search(url)
    if match:
        return "x-video", match.group(1)
    raise ValueError(f"not a supported video URL: {url!r}")


def fetch_meta(url: str, *, run: RunProc = _subprocess_run) -> VideoMeta | Unavailable:
    source, video_id = parse_video_url(url)
    proc = run(["yt-dlp", "--dump-json", "--no-warnings", "--skip-download", url])
    if proc.returncode != 0:
        return Unavailable(proc.stderr.strip() or f"yt-dlp exit {proc.returncode}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return Unavailable("yt-dlp returned non-JSON output")
    if not isinstance(data, dict):
        return Unavailable("yt-dlp returned unexpected JSON shape")
    timestamp = data.get("timestamp")
    publish = ""
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        try:
            publish = datetime.fromtimestamp(float(timestamp), UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return Unavailable(f"unusable timestamp in yt-dlp JSON: {timestamp!r}")
    try:
        duration = float(data.get("duration") or 0.0)
    except (TypeError, ValueError):
        return Unavailable(
            f"unusable duration in yt-dlp JSON: {data.get('duration')!r}"
        )
    return VideoMeta(
        source=source,
        video_id=video_id,
        author=str(data.get("uploader_id") or data.get("uploader") or ""),
        title=str(data.get("title") or ""),
        publish_ts_utc=publish,
        duration_s=duration,
        lang=str(data.get("language") or ""),
        url=url,
    )

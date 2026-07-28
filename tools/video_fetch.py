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
from pathlib import Path
from typing import Protocol

from tools.video_marks import TranscriptSegment

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


GROQ_MAX_BYTES = (
    24 * 1024 * 1024
)  # Groq rejects >25MB; leave headroom for multipart overhead

_VTT_CUE_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}"
)
_GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

# Fed to ASR so crypto jargon is not mangled at the source. Cheapest of the three
# transcript-quality layers; the other two live in the vision pass.
_ASR_VOCAB = (
    "FVG, OTE, BOS, CHoCH, liquidity sweep, order block, equal highs, equal lows, "
    "funding rate, open interest, BTC, ETH, SOL, XRP, DOGE, BNB, perp, longs, shorts"
)


class HttpResponse(Protocol):
    status_code: int
    text: str


class HttpPost(Protocol):
    def __call__(
        self,
        url: str,
        *,
        headers: dict[str, str],
        files: dict[str, object],
        data: dict[str, str],
    ) -> HttpResponse: ...


def parse_vtt(text: str, lang: str) -> list[TranscriptSegment]:
    """WebVTT cues → segments. Cue start time is the segment timestamp."""
    segments: list[TranscriptSegment] = []
    pending_ts: float | None = None
    for line in text.splitlines():
        stripped = line.strip()
        match = _VTT_CUE_RE.search(stripped)
        if match:
            hours, minutes, seconds, millis = (int(g) for g in match.groups())
            pending_ts = hours * 3600 + minutes * 60 + seconds + millis / 1000
            continue
        if pending_ts is None or not stripped or stripped == "WEBVTT":
            continue
        segments.append(TranscriptSegment(ts_s=pending_ts, text=stripped, lang=lang))
        pending_ts = None
    return segments


def fetch_transcript(
    meta: VideoMeta,
    *,
    run: RunProc = _subprocess_run,
    get: HttpPost | None = None,
    groq_key: str | None = None,
    work_dir: Path = Path(".cache/video"),
) -> list[TranscriptSegment] | Unavailable:
    """Existing captions in any language first; Groq whisper-large-v3 only when absent."""
    work_dir.mkdir(parents=True, exist_ok=True)
    run(
        [
            "yt-dlp",
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-format",
            "vtt",
            "--sub-langs",
            "all",
            "-o",
            str(work_dir / "sub"),
            meta.url,
        ]
    )
    vtts = sorted(work_dir.glob("sub*.vtt"))
    if vtts:
        return parse_vtt(vtts[0].read_text(encoding="utf-8"), lang=meta.lang or "en")
    if groq_key is None or get is None:
        return Unavailable("no captions available and no GROQ_API_KEY configured")
    return _transcribe_groq(
        meta, run=run, get=get, groq_key=groq_key, work_dir=work_dir
    )


def split_audio(
    audio: Path,
    duration_s: float,
    *,
    run: RunProc,
    max_bytes: int = GROQ_MAX_BYTES,
) -> list[tuple[Path, float]]:
    """Split oversized audio into (chunk, time_offset) pairs. Offsets restore absolute
    timestamps after per-chunk transcription."""
    size = audio.stat().st_size
    if size <= max_bytes:
        return [(audio, 0.0)]
    parts = -(-size // max_bytes)  # ceil
    span = duration_s / parts
    chunks: list[tuple[Path, float]] = []
    for i in range(parts):
        offset = i * span
        out = audio.with_name(f"{audio.stem}_{i:02d}{audio.suffix}")
        proc = run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(offset),
                "-t",
                str(span),
                "-i",
                str(audio),
                "-c",
                "copy",
                str(out),
            ]
        )
        if proc.returncode == 0:
            chunks.append((out, offset))
    return chunks


def _transcribe_groq(
    meta: VideoMeta,
    *,
    run: RunProc,
    get: HttpPost,
    groq_key: str,
    work_dir: Path,
) -> list[TranscriptSegment] | Unavailable:
    audio = work_dir / f"{meta.video_id}.opus"
    proc = run(
        [
            "yt-dlp",
            "-f",
            "bestaudio",
            "-x",
            "--audio-format",
            "opus",
            "--audio-quality",
            "6",
            "-o",
            str(audio),
            meta.url,
        ]
    )
    if proc.returncode != 0 or not audio.exists():
        return Unavailable(proc.stderr.strip() or "audio extraction failed")
    segments: list[TranscriptSegment] = []
    chunks = split_audio(audio, meta.duration_s, run=run)
    if not chunks:
        return Unavailable("audio chunking failed")
    for chunk, offset in chunks:
        with chunk.open("rb") as handle:
            resp = get(
                _GROQ_URL,
                headers={"Authorization": f"Bearer {groq_key}"},
                files={"file": handle},
                data={
                    "model": "whisper-large-v3",
                    "response_format": "verbose_json",
                    "prompt": _ASR_VOCAB,
                },
            )
        if resp.status_code != 200:
            return Unavailable(f"Groq HTTP {resp.status_code}")
        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError:
            return Unavailable("Groq returned non-JSON output")
        lang = str(payload.get("language") or meta.lang or "en")
        for entry in payload.get("segments", []):
            try:
                text = str(entry["text"]).strip()
                ts_s = float(entry["start"]) + offset
            except (KeyError, TypeError, ValueError):
                # Malformed segment from Groq (missing/non-numeric field) — skip it
                # rather than crashing the whole transcript; fetch_transcript must
                # degrade, not raise, since a later task calls it inside a batch loop.
                continue
            if text:
                segments.append(TranscriptSegment(ts_s=ts_s, text=text, lang=lang))
    return segments

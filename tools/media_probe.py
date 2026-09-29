"""Canary for the yt-dlp media leg — the one failure that kills the vision pass.

ST41, measured 2026-08-18: yt-dlp stable `2026.7.4` resolved ONLY the
`android_vr` player client for YouTube, whose `googlevideo` media URLs return
`HTTP 403: Forbidden` unconditionally. **One failure, three symptoms** —
captions, the Groq whisper fallback and frame extraction all sit downstream of
the media leg — so it presented as a dead vision pass rather than as a download
problem, and an entire ingest round was lost before anyone looked at the
dependency.

The fix pinned a DATED NIGHTLY. It will go stale (YouTube breaks yt-dlp every
few weeks) and nothing watched it: `yt-feed poll` covers DISCOVERY, not FETCH,
so the next break would present exactly as the last one did — a degraded round
found by hand, after the round. This module is what watches it.

Two design points are load-bearing:

- **It drives `_ensure_local_media`, production's own download call**, rather
  than re-specifying the flags. A probe that builds its own yt-dlp command tests
  a path the pipeline never takes, and drifts from it silently.
- **Never probe with `--download-sections`.** A range fetch hands the URL to
  ffmpeg, which does not carry yt-dlp's client context and 403s even when a
  plain download works — it manufactures the exact false positive this check
  exists to avoid.

This lives in `tools/` rather than inside `docs/plans/daily_check.py` on
purpose: that file is gitignored, so logic buried there reaches no reclone, no
CI and not the wifey fork.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from tools.video_fetch import (
    Completedish,
    RunProc,
    VideoMeta,
    _ensure_local_media,
    _subprocess_run,
)

# "Me at the zoo" — 19 seconds, uploaded 2005-04-23, the first video on YouTube.
# Chosen because it is short (the fetch costs a few hundred KB), unlisted-proof,
# never age- or region-gated, and about as unlikely to be deleted as anything on
# the platform.
CANARY_ID = "jNQXAC9IVRw"
CANARY_URL = f"https://www.youtube.com/watch?v={CANARY_ID}"

# How long a stamped verdict stands before the canary is re-fetched. The daily
# 09:10 UTC push pays the fetch; hand-runs read the stamp, so checking the
# report five times in a session does not hit YouTube five times.
STALE_AFTER_H = 20.0


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    detail: str
    ytdlp_version: str
    checked_at_utc: str
    cached: bool = False


class _Recorder:
    """Wraps the injected runner to keep the last process for its stderr.

    `_ensure_local_media` returns `None` on failure and swallows the reason —
    but the reason IS the deliverable here, since the previous break was filed
    as an unexplained "frames down" precisely because the underlying message
    never reached anything the operator read.
    """

    def __init__(self, inner: RunProc) -> None:
        self.inner = inner
        self.last: Completedish | None = None

    def __call__(self, cmd: list[str]) -> Completedish:
        proc = self.inner(cmd)
        self.last = proc
        return proc


def _installed_version() -> str:
    try:
        return version("yt-dlp")
    except PackageNotFoundError:
        return "unknown"


def _error_line(proc: Completedish | None) -> str:
    if proc is None:
        return "no output"
    text = (proc.stderr or proc.stdout or "").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return "no output"
    for ln in reversed(lines):
        if "ERROR" in ln or "403" in ln:
            return ln[:200]
    return lines[-1][:200]


def probe_media_leg(*, run: RunProc = _subprocess_run) -> ProbeResult:
    """Fetch the canary through the production media path; report what happened."""
    recorder = _Recorder(run)
    ver = _installed_version()
    now = datetime.now(UTC).isoformat()
    meta = VideoMeta(
        source="youtube",
        video_id=CANARY_ID,
        author="jawed",
        title="Me at the zoo",
        publish_ts_utc="2005-04-23T00:00:00+00:00",
        duration_s=19.0,
        lang="en",
        url=CANARY_URL,
    )
    with tempfile.TemporaryDirectory(prefix="media-probe-") as td:
        media = _ensure_local_media(meta, Path(td), run=recorder)
        size = media.stat().st_size if media is not None else 0

    if media is not None and size > 0:
        return ProbeResult(
            ok=True,
            detail=f"yt-dlp {ver} pulled {size // 1024} KB from the canary",
            ytdlp_version=ver,
            checked_at_utc=now,
        )
    # A clean exit that produced nothing is just as fatal as a 403: ffmpeg seeks
    # a LOCAL file, so a missing one costs the whole vision pass.
    detail = (
        f"yt-dlp {ver} exited 0 but produced no media file"
        if media is None and _clean_exit(recorder)
        else f"yt-dlp {ver} FAILED: {_error_line(recorder.last)}"
    )
    return ProbeResult(ok=False, detail=detail, ytdlp_version=ver, checked_at_utc=now)


def _clean_exit(recorder: _Recorder) -> bool:
    return recorder.last is not None and recorder.last.returncode == 0


def _load_stamp(stamp: Path, max_age_h: float) -> ProbeResult | None:
    """Return a stamped verdict while it is fresh, else `None`.

    Corrupt or unreadable stamps re-probe rather than raising — this check is
    never allowed to be the thing that breaks the daily report.
    """
    try:
        raw = json.loads(stamp.read_text(encoding="utf-8"))
        checked = datetime.fromisoformat(str(raw["checked_at_utc"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if (datetime.now(UTC) - checked).total_seconds() / 3600 > max_age_h:
        return None
    return ProbeResult(
        ok=bool(raw.get("ok")),
        detail=str(raw.get("detail", "")),
        ytdlp_version=str(raw.get("ytdlp_version", "")),
        checked_at_utc=checked.isoformat(),
        cached=True,
    )


def cached_probe(
    stamp: Path, *, max_age_h: float = STALE_AFTER_H, run: RunProc = _subprocess_run
) -> ProbeResult:
    """`probe_media_leg`, but at most once per `max_age_h`, stamped on disk."""
    fresh = _load_stamp(stamp, max_age_h)
    if fresh is not None:
        return fresh
    result = probe_media_leg(run=run)
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(
            json.dumps(
                {
                    "ok": result.ok,
                    "detail": result.detail,
                    "ytdlp_version": result.ytdlp_version,
                    "checked_at_utc": result.checked_at_utc,
                }
            ),
            encoding="utf-8",
        )
    except OSError:
        pass  # an unwritable stamp costs a re-probe, never the report
    return result

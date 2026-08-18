"""Behavioural tests for `tools/media_probe.py` — the yt-dlp media-leg canary.

The defect this guards was ST41, measured 2026-08-18. yt-dlp stable `2026.7.4`
resolved ONLY the `android_vr` player client for YouTube, and that client's
`googlevideo` media URLs return `HTTP 403: Forbidden` unconditionally. One
failure took down three things at once — captions, the Groq whisper fallback and
frame extraction all sit downstream of the media leg — so it presented as a dead
vision pass rather than a download problem, and a whole ingest round was lost
before anyone looked at the dependency.

The fix pinned a DATED NIGHTLY, which will go stale (YouTube breaks yt-dlp every
few weeks), and nothing watched it: `yt-feed poll` covers discovery, not fetch,
so the next break would present exactly as the last one did — a degraded round
found by hand, after the round.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tools.media_probe import ProbeResult, cached_probe, probe_media_leg

# The verbatim failure the pipeline surfaced during ST41.
ST41_STDERR = "ERROR: unable to download video data: HTTP Error 403: Forbidden"


@dataclass
class FakeProc:
    returncode: int
    stdout: str
    stderr: str


class FakeYtDlp:
    """Stands in for the yt-dlp subprocess, recording what it was asked to run.

    On success it writes a file where the real binary would, because "exit 0"
    alone is not the contract the pipeline depends on — `_ensure_local_media`
    hands ffmpeg a LOCAL file, so a clean exit that produced nothing is still a
    dead media leg.
    """

    def __init__(self, *, returncode: int = 0, stderr: str = "", writes: bool = True):
        self.returncode = returncode
        self.stderr = stderr
        self.writes = writes
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> FakeProc:
        self.calls.append(cmd)
        if self.returncode == 0 and self.writes:
            template = Path(cmd[cmd.index("-o") + 1])
            (template.parent / template.name.replace("%(ext)s", "mp4")).write_bytes(
                b"\x00" * 2048
            )
        return FakeProc(returncode=self.returncode, stdout="", stderr=self.stderr)


def test_reports_ok_when_the_media_leg_downloads() -> None:
    """The healthy case: bytes came back, so the vision pass has its input."""
    fake = FakeYtDlp()

    result = probe_media_leg(run=fake)

    assert result.ok, result.detail
    assert len(fake.calls) == 1, f"probe should fetch exactly once: {fake.calls}"


def test_reports_the_403_when_the_media_leg_is_down() -> None:
    """The ST41 shape: yt-dlp fails and the 403 must reach the operator's line.

    Surfacing the error text is the point — the previous break was filed as an
    unexplained "frames down" precisely because the underlying message never
    made it to anything the operator read.
    """
    fake = FakeYtDlp(returncode=1, stderr=ST41_STDERR)

    result = probe_media_leg(run=fake)

    assert not result.ok
    assert "403" in result.detail, f"the 403 never reached the report: {result.detail}"


def test_a_clean_exit_that_produced_no_file_is_still_a_failure() -> None:
    """rc=0 with nothing on disk must not read as healthy.

    ffmpeg seeks a local file; a missing one costs the whole vision pass just as
    surely as a 403 does, and it would otherwise be an invisible green.
    """
    fake = FakeYtDlp(returncode=0, writes=False)

    result = probe_media_leg(run=fake)

    assert not result.ok
    assert "no media" in result.detail.lower(), result.detail


def test_probe_drives_the_production_download_path() -> None:
    """The canary must fetch the way the pipeline does, or it tests nothing.

    Two specifics, both load-bearing. `--js-runtimes node` is production's
    prefix: without a JS runtime yt-dlp cannot solve YouTube's signature
    challenge and every media download 403s, so a probe omitting it would fail
    for a reason production never hits. And `--download-sections` must be
    ABSENT — a range fetch hands the URL to ffmpeg, which does not carry
    yt-dlp's client context and 403s even when a plain download works, so it
    manufactures exactly the false positive this check exists to avoid.
    """
    fake = FakeYtDlp()

    probe_media_leg(run=fake)

    cmd = fake.calls[0]
    assert cmd[0] == "yt-dlp", cmd
    assert "--js-runtimes" in cmd and "node" in cmd, cmd
    assert "--download-sections" not in cmd, (
        "a range fetch 403s even on a healthy media leg — it mis-diagnoses ST41"
    )


def test_cached_probe_reuses_a_fresh_stamp(tmp_path: Path) -> None:
    """A hand-run must not re-hit YouTube; only the daily cron pays the fetch."""
    stamp = tmp_path / "media-probe.json"
    fake = FakeYtDlp()

    first = cached_probe(stamp, run=fake)
    second = cached_probe(stamp, run=fake)

    assert first.ok and second.ok
    assert len(fake.calls) == 1, f"cached run re-fetched: {fake.calls}"
    assert second.cached and not first.cached


def test_cached_probe_refetches_once_the_stamp_is_stale(tmp_path: Path) -> None:
    """Positive control for the cache: a stale stamp must NOT be reused.

    Without this the reuse test above is satisfied by a probe that never runs
    again at all, which is indistinguishable from a working cache and would
    leave the media leg unwatched exactly as it is today.
    """
    stamp = tmp_path / "media-probe.json"
    fake = FakeYtDlp()
    cached_probe(stamp, run=fake)

    aged = json.loads(stamp.read_text())
    aged["checked_at_utc"] = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    stamp.write_text(json.dumps(aged))
    result = cached_probe(stamp, run=fake)

    assert len(fake.calls) == 2, "a stale stamp must trigger a fresh fetch"
    assert not result.cached


def test_probe_result_names_the_installed_version(tmp_path: Path) -> None:
    """The pin is a dated nightly, so the report must say which one is live.

    "yt-dlp is broken again" is only actionable next to the version it broke on.
    """
    result = probe_media_leg(run=FakeYtDlp())

    assert isinstance(result, ProbeResult)
    assert result.ytdlp_version, "no version recorded"
    assert result.ytdlp_version in result.detail, result.detail
